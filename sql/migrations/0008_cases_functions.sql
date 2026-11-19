-- =============================================================================
-- 0008_cases_functions.sql  —  案件管理（第1段階「返信獲得」）: 関数・トリガー
--
-- PostgreSQL は関数作成時に PUBLIC へ EXECUTE を与える。このファイルでは
-- 各関数を作成した直後、同じトランザクション内で PUBLIC / anon / authenticated
-- から EXECUTE を REVOKE する（0002_functions.sqlと同じ方針）。したがって
-- 【0008単独の実行が完了した時点】で、どの関数も外部から呼べない状態になる。
-- 0009_cases_rls_grants.sql は、そこから利用者向けの6関数だけを
-- authenticated へ改めて GRANT する。
--
-- SECURITY DEFINER 関数の約束（0002と同じ）:
--   - SET search_path = ''
--   - 参照先はすべてスキーマ付きで完全修飾（public.xxx / auth.xxx）
--   - auth.uid() が NULL なら拒否
--   - 任意の user_id を引数に取らない（本人の行かどうかは常にDB側で確認する）
--   - 返却情報は必要最小限。内部エラーの詳細は返さない
--   - オーバーロードを作らない（1関数1シグネチャ）
--
-- 利用者向けRPCは6つ:
--   create_case_from_candidate / log_case_event / void_case_event /
--   correct_case_event / redact_case_event / save_ai_assist_result
-- 内部専用（直接呼べない）の補助関数は2つ:
--   _case_apply_event（状態機械の純粋なロジック）/
--   _case_recompute（有効なイベントだけを発生順に再生して案件状態を再計算する）
-- =============================================================================

begin;

-- ── _case_apply_event（内部専用: 状態機械） ──────────────────────────────────
-- 現在の状態・ラベルと、記録しようとしているイベント種別から、遷移後の
-- status / next_action_at / next_action_label を返す。テーブルには一切触れない
-- 純粋なロジックなので SECURITY INVOKER で良い（SECURITY DEFINER関数の中から
-- 呼ばれるときは、その呼び出し元の実行コンテキストでそのまま動く）。
-- 許可されない遷移は例外にする（errcode 'P0001'。log_case_event()・
-- _case_recompute()の両方から、この同じ例外を使って「現在の状態では行えない
-- 操作」を区別する）。
--
-- status_change（advanced/on_hold/lost）はここでは扱わない
-- （log_case_event()・_case_recompute()の中で別途ガードする）。
create or replace function public._case_apply_event(
    p_status      text,
    p_label       text,
    p_kind        text,
    p_occurred_at timestamptz
)
returns table (
    new_status           text,
    new_next_action_at    date,
    new_next_action_label text
)
language plpgsql
security invoker
set search_path = ''
as $$
declare
    v_jst_date date := (p_occurred_at at time zone 'Asia/Tokyo')::date;
begin
    if p_kind = 'initial' then
        if p_status <> 'not_contacted' then
            raise exception 'invalid_transition' using errcode = 'P0001';
        end if;
        return query select 'awaiting_first_reply'::text, v_jst_date + 3, '1回目フォロー'::text;

    elsif p_kind = 'followup1' then
        if p_status <> 'awaiting_first_reply' or p_label <> '1回目フォロー' then
            raise exception 'invalid_transition' using errcode = 'P0001';
        end if;
        return query select 'awaiting_first_reply'::text, v_jst_date + 7, '2回目フォロー'::text;

    elsif p_kind = 'followup2' then
        if p_status <> 'awaiting_first_reply' or p_label <> '2回目フォロー' then
            raise exception 'invalid_transition' using errcode = 'P0001';
        end if;
        return query select 'awaiting_first_reply'::text, null::date, ''::text;

    elsif p_kind in ('reply', 'condition_answer') then
        if p_status not in ('awaiting_first_reply', 'awaiting_next_reply', 'on_hold', 'lost') then
            raise exception 'invalid_transition' using errcode = 'P0001';
        end if;
        return query select 'awaiting_our_reply'::text, null::date, ''::text;

    elsif p_kind in ('our_reply', 'condition_check') then
        if p_status not in ('awaiting_our_reply', 'on_hold', 'lost') then
            raise exception 'invalid_transition' using errcode = 'P0001';
        end if;
        return query select 'awaiting_next_reply'::text, null::date, ''::text;

    else
        raise exception 'invalid_kind' using errcode = '22023';
    end if;
end;
$$;

comment on function public._case_apply_event(text, text, text, timestamptz) is
  '内部専用。状態機械の純粋なロジック（テーブル非依存）。log_case_event/_case_recomputeから呼ぶ';

revoke execute on function public._case_apply_event(text, text, text, timestamptz) from public;
revoke execute on function public._case_apply_event(text, text, text, timestamptz) from anon;
revoke execute on function public._case_apply_event(text, text, text, timestamptz) from authenticated;


-- ── _case_recompute（内部専用: 取消し後の再計算） ─────────────────────────────
-- 指定した案件の、有効な（voided_atがnullの）イベントだけを発生順
-- （occurred_at, created_at）に発生順に再生し、status/next_action_at/
-- next_action_label/first_sent_at/has_reply/last_reply_at/advanced_at を
-- 再計算してcasesへ書き込む。
--
-- 再生の途中で _case_apply_event() が invalid_transition を投げた場合
-- （例: フォロー1の前提になっていた初回送信を取り消した）は、そのまま
-- 例外を外へ伝播させる。ここでは BEGIN/EXCEPTION で捕まえない。
-- 呼び出し元（void_case_event/correct_case_event）にも例外処理を置かないため、
-- Postgresの既定の挙動どおり、この呼び出しを含むトランザクション全体が
-- 中断され、直前のUPDATE（voided_atのセット等）も一切コミットされない。
create or replace function public._case_recompute(p_case_id uuid)
returns void
language plpgsql
security invoker
set search_path = ''
as $$
declare
    v_status        text := 'not_contacted';
    v_label         text := '';
    v_next_at       date := null;
    v_first_sent_at timestamptz := null;
    v_has_reply     boolean := false;
    v_last_reply_at timestamptz := null;
    v_advanced_at   timestamptz := null;
    v_ev            record;
    v_eff           record;
begin
    for v_ev in
        select kind, occurred_at, target_status
          from public.case_events
         where case_id = p_case_id
           and voided_at is null
         order by occurred_at asc, created_at asc
    loop
        if v_ev.kind = 'note' then
            continue;

        elsif v_ev.kind = 'status_change' then
            if v_ev.target_status = 'advanced'
               and v_status not in ('awaiting_our_reply', 'awaiting_next_reply') then
                raise exception 'invalid_transition' using errcode = 'P0001';
            end if;
            v_status := v_ev.target_status;
            v_next_at := null;
            v_label := '';
            if v_ev.target_status = 'advanced' then
                v_advanced_at := coalesce(v_advanced_at, v_ev.occurred_at);
            end if;

        else
            select * into v_eff
              from public._case_apply_event(v_status, v_label, v_ev.kind, v_ev.occurred_at);
            v_status := v_eff.new_status;
            v_next_at := v_eff.new_next_action_at;
            v_label := v_eff.new_next_action_label;

            if v_ev.kind = 'initial' then
                v_first_sent_at := coalesce(v_first_sent_at, v_ev.occurred_at);
            end if;
            if v_ev.kind in ('reply', 'condition_answer') then
                v_has_reply := true;
                v_last_reply_at := v_ev.occurred_at;
            end if;
        end if;
    end loop;

    update public.cases
       set status             = v_status,
           next_action_at     = v_next_at,
           next_action_label  = v_label,
           first_sent_at      = v_first_sent_at,
           has_reply          = v_has_reply,
           last_reply_at      = v_last_reply_at,
           advanced_at        = v_advanced_at
     where id = p_case_id;
end;
$$;

comment on function public._case_recompute(uuid) is
  '内部専用。有効なイベントだけを発生順に再生して案件状態を再計算する。'
  '矛盾があれば例外を伝播させ、呼び出し元のトランザクション全体を中断させる';

revoke execute on function public._case_recompute(uuid) from public;
revoke execute on function public._case_recompute(uuid) from anon;
revoke execute on function public._case_recompute(uuid) from authenticated;


-- ── create_case_from_candidate ───────────────────────────────────────────
-- マイ候補リスト（saved_items）の1件から案件を作る唯一の経路。
-- 商品情報・連絡先候補はDB側で products から取得してスナップショットする
-- （クライアントから渡された値は一切信用しない）。
create or replace function public.create_case_from_candidate(
    p_saved_item_id uuid
)
returns jsonb
language plpgsql
volatile
security definer
set search_path = ''
as $$
declare
    v_uid      uuid := auth.uid();
    v_saved    record;
    v_product  record;
    v_contact  jsonb;
    v_channel  text;
    v_case_id  uuid;
    v_dup      boolean := false;
begin
    if v_uid is null then
        raise exception 'not authenticated' using errcode = '28000';
    end if;

    -- 本人の候補であることをここで確認する（saved_itemsのRLSはSECURITY DEFINERの
    -- 中では効かないため、user_idの一致を明示的に確認する）
    select s.id, s.product_id
      into v_saved
      from public.saved_items s
     where s.id = p_saved_item_id
       and s.user_id = v_uid;

    if v_saved.id is null then
        raise exception 'saved item not found' using errcode = '22023';
    end if;

    select p.id, p.platform, p.name, p.maker, p.source_url, p.contact
      into v_product
      from public.products p
     where p.id = v_saved.product_id;

    if v_product.id is null then
        raise exception 'product not available' using errcode = '22023';
    end if;

    -- 連絡先はキーを明示的に許可リストへ制限してコピーする（不要なデータを
    -- 保存しない。将来チャネルを追加する場合はこの一覧を更新する）
    select coalesce(jsonb_object_agg(kv.key, kv.value), '{}'::jsonb)
      into v_contact
      from jsonb_each(coalesce(v_product.contact, '{}'::jsonb)) as kv(key, value)
     where kv.key in ('email', 'facebook', 'instagram', 'contact_form',
                      'linkedin', 'official_url');

    -- primary_channel の初期値は 'none' に留め、Python側(cases.py)が既存の
    -- contact_info.resolve_priority() と同じ検証済みロジックで確定させ、
    -- 直後にUPDATEする（SQL側でメール形式等の妥当性検証を重複して持たない）。
    v_channel := 'none';

    insert into public.cases (
        user_id, product_id, saved_item_id,
        platform, product_name, maker_name, source_url, contact_snapshot,
        primary_channel
    )
    values (
        v_uid, v_product.id, v_saved.id,
        left(coalesce(v_product.platform, ''), 50),
        left(coalesce(v_product.name, ''), 500),
        left(coalesce(v_product.maker, ''), 300),
        left(coalesce(v_product.source_url, ''), 2048),
        v_contact,
        v_channel
    )
    on conflict (user_id, product_id) do nothing
    returning id into v_case_id;

    if v_case_id is null then
        v_dup := true;
        select c.id into v_case_id
          from public.cases c
         where c.user_id = v_uid
           and c.product_id = v_product.id;
    end if;

    return jsonb_build_object(
        'case_id', v_case_id,
        'already_exists', v_dup
    );
end;
$$;

comment on function public.create_case_from_candidate(uuid) is
  '商品案件の作成。他人のsaved_itemからは作成できず、同一利用者・同一商品の'
  '重複案件は作らない（二重クリックでも安全）';

revoke execute on function public.create_case_from_candidate(uuid) from public;
revoke execute on function public.create_case_from_candidate(uuid) from anon;
revoke execute on function public.create_case_from_candidate(uuid) from authenticated;


-- ── log_case_event ────────────────────────────────────────────────────────
-- 案件イベント（送信・フォロー・返信・条件確認/回答・メモ・手動の状態変更）を
-- 記録する唯一の経路。状態機械は _case_apply_event() に委ねる。
-- kind='status_change' の時だけ p_new_status（advanced/on_hold/lost）を使う。
create or replace function public.log_case_event(
    p_case_id         uuid,
    p_kind            text,
    p_channel         text default '',
    p_recipient       text default '',
    p_occurred_at     timestamptz default now(),
    p_message_text    text default '',
    p_translated_text text default '',
    p_new_status      text default null
)
returns jsonb
language plpgsql
volatile
security definer
set search_path = ''
as $$
declare
    v_uid             uuid := auth.uid();
    v_case            record;
    v_eff             record;
    v_event_id        uuid;
    v_new_status      text;
    v_new_next_at     date;
    v_new_next_label  text;
    v_target_status   text := null;
begin
    if v_uid is null then
        raise exception 'not authenticated' using errcode = '28000';
    end if;

    if p_kind not in ('initial', 'followup1', 'followup2', 'reply', 'our_reply',
                      'condition_check', 'condition_answer', 'status_change', 'note') then
        raise exception 'invalid kind' using errcode = '22023';
    end if;
    if char_length(coalesce(p_message_text, '')) > 20000
       or char_length(coalesce(p_translated_text, '')) > 20000 then
        raise exception 'text too long' using errcode = '22023';
    end if;
    if p_occurred_at is null then
        raise exception 'occurred_at is required' using errcode = '22023';
    end if;

    -- 本人の案件であることを確認しつつロックする（同一案件への並行書き込みを
    -- 直列化し、状態機械の判定と更新を一貫させる）
    select c.id, c.status, c.next_action_label
      into v_case
      from public.cases c
     where c.id = p_case_id
       and c.user_id = v_uid
     for update;

    if v_case.id is null then
        raise exception 'case not found' using errcode = '22023';
    end if;

    if p_kind = 'note' then
        insert into public.case_events (
            case_id, user_id, kind, channel, recipient, occurred_at,
            message_text, translated_text
        )
        values (
            p_case_id, v_uid, 'note', coalesce(p_channel, ''), coalesce(p_recipient, ''),
            p_occurred_at, coalesce(p_message_text, ''), coalesce(p_translated_text, '')
        )
        returning id into v_event_id;

        return jsonb_build_object(
            'event_id', v_event_id, 'case_id', p_case_id,
            'status', v_case.status, 'next_action_at', null, 'next_action_label', null
        );
    end if;

    if p_kind = 'status_change' then
        if p_new_status not in ('advanced', 'on_hold', 'lost') then
            raise exception 'invalid target status' using errcode = '22023';
        end if;
        if p_new_status = 'advanced'
           and v_case.status not in ('awaiting_our_reply', 'awaiting_next_reply') then
            raise exception 'invalid_transition' using errcode = 'P0001';
        end if;
        -- on_hold / lost はほぼ任意の状態から手動で設定できる
        v_new_status := p_new_status;
        v_new_next_at := null;
        v_new_next_label := '';
        v_target_status := p_new_status;
    else
        select * into v_eff
          from public._case_apply_event(v_case.status, v_case.next_action_label,
                                        p_kind, p_occurred_at);
        v_new_status := v_eff.new_status;
        v_new_next_at := v_eff.new_next_action_at;
        v_new_next_label := v_eff.new_next_action_label;
    end if;

    insert into public.case_events (
        case_id, user_id, kind, channel, recipient, occurred_at,
        message_text, translated_text, target_status
    )
    values (
        p_case_id, v_uid, p_kind, coalesce(p_channel, ''), coalesce(p_recipient, ''),
        p_occurred_at, coalesce(p_message_text, ''), coalesce(p_translated_text, ''),
        v_target_status
    )
    returning id into v_event_id;

    update public.cases
       set status            = v_new_status,
           next_action_at    = v_new_next_at,
           next_action_label = v_new_next_label,
           first_sent_at     = case when p_kind = 'initial'
                                    then coalesce(first_sent_at, p_occurred_at)
                                    else first_sent_at end,
           has_reply         = has_reply or (p_kind in ('reply', 'condition_answer')),
           last_reply_at     = case when p_kind in ('reply', 'condition_answer')
                                    then p_occurred_at else last_reply_at end,
           advanced_at       = case when v_target_status = 'advanced'
                                    then coalesce(advanced_at, p_occurred_at)
                                    else advanced_at end
     where id = p_case_id;

    return jsonb_build_object(
        'event_id', v_event_id, 'case_id', p_case_id,
        'status', v_new_status,
        'next_action_at', v_new_next_at, 'next_action_label', v_new_next_label
    );
end;
$$;

comment on function public.log_case_event(uuid, text, text, text, timestamptz, text, text, text) is
  '案件イベントの記録。状態機械は_case_apply_event()に委ね、現在の状態と矛盾する'
  'イベントは拒否する（例: 初回送信前のフォロー、重複フォロー）';

revoke execute on function public.log_case_event(
    uuid, text, text, text, timestamptz, text, text, text) from public;
revoke execute on function public.log_case_event(
    uuid, text, text, text, timestamptz, text, text, text) from anon;
revoke execute on function public.log_case_event(
    uuid, text, text, text, timestamptz, text, text, text) from authenticated;


-- ── void_case_event ───────────────────────────────────────────────────────
-- イベントの取消し（物理削除はしない）。取消し後、残っている有効なイベントから
-- _case_recompute() で状態を再計算する。再計算が矛盾を検出した場合
-- （後続のイベントが成立しなくなる）は例外が外へ伝播し、voided_atのセットを
-- 含むトランザクション全体が中断される（部分的な更新は残らない）。
create or replace function public.void_case_event(
    p_event_id uuid,
    p_reason   text default ''
)
returns jsonb
language plpgsql
volatile
security definer
set search_path = ''
as $$
declare
    v_uid uuid := auth.uid();
    v_ev  record;
begin
    if v_uid is null then
        raise exception 'not authenticated' using errcode = '28000';
    end if;

    select e.id, e.case_id, e.voided_at
      into v_ev
      from public.case_events e
     where e.id = p_event_id
       and e.user_id = v_uid
     for update of e;

    if v_ev.id is null then
        raise exception 'event not found' using errcode = '22023';
    end if;
    if v_ev.voided_at is not null then
        raise exception 'already voided' using errcode = '22023';
    end if;

    update public.case_events
       set voided_at     = now(),
           voided_reason = left(coalesce(p_reason, ''), 300)
     where id = p_event_id;

    -- 矛盾があればここで例外が上がり、上のUPDATEも含めてロールバックされる
    perform public._case_recompute(v_ev.case_id);

    return jsonb_build_object('event_id', p_event_id, 'case_id', v_ev.case_id, 'voided', true);
end;
$$;

comment on function public.void_case_event(uuid, text) is
  'イベントの取消し（物理削除はしない）。後続イベントと矛盾する取消しは'
  '例外(invalid_transition)になり、トランザクション全体が失敗する';

revoke execute on function public.void_case_event(uuid, text) from public;
revoke execute on function public.void_case_event(uuid, text) from anon;
revoke execute on function public.void_case_event(uuid, text) from authenticated;


-- ── correct_case_event ────────────────────────────────────────────────────
-- 本文の訂正。元の行は取消し済みとして残し、訂正済みの新しい行を追加して
-- superseded_by で紐づける。kind/occurred_at/channel/recipient/target_status は
-- 元の行から引き継ぐ（変わるのは本文だけ）。
create or replace function public.correct_case_event(
    p_event_id        uuid,
    p_message_text    text default '',
    p_translated_text text default ''
)
returns jsonb
language plpgsql
volatile
security definer
set search_path = ''
as $$
declare
    v_uid    uuid := auth.uid();
    v_ev     record;
    v_new_id uuid;
begin
    if v_uid is null then
        raise exception 'not authenticated' using errcode = '28000';
    end if;

    if char_length(coalesce(p_message_text, '')) > 20000
       or char_length(coalesce(p_translated_text, '')) > 20000 then
        raise exception 'text too long' using errcode = '22023';
    end if;

    select e.*
      into v_ev
      from public.case_events e
     where e.id = p_event_id
       and e.user_id = v_uid
     for update;

    if v_ev.id is null then
        raise exception 'event not found' using errcode = '22023';
    end if;
    if v_ev.voided_at is not null then
        raise exception 'event already voided' using errcode = '22023';
    end if;
    if v_ev.redacted_at is not null then
        raise exception 'event already redacted' using errcode = '22023';
    end if;

    insert into public.case_events (
        case_id, user_id, kind, channel, recipient, occurred_at,
        message_text, translated_text, target_status
    )
    values (
        v_ev.case_id, v_uid, v_ev.kind, v_ev.channel, v_ev.recipient, v_ev.occurred_at,
        coalesce(p_message_text, ''), coalesce(p_translated_text, ''), v_ev.target_status
    )
    returning id into v_new_id;

    update public.case_events
       set voided_at     = now(),
           voided_reason = 'corrected',
           superseded_by = v_new_id
     where id = p_event_id;

    -- kind/occurred_atは変わらないため結果は変わらないはずだが、整合性を
    -- 単一のルールで保証するため、訂正後も必ず再計算する。
    perform public._case_recompute(v_ev.case_id);

    return jsonb_build_object(
        'old_event_id', p_event_id, 'new_event_id', v_new_id, 'case_id', v_ev.case_id
    );
end;
$$;

comment on function public.correct_case_event(uuid, text, text) is
  '本文の訂正。元の行はvoided_reason=''corrected''で取消し済みにし、'
  '訂正済みの新しい行をsuperseded_byで紐づける（物理削除はしない）';

revoke execute on function public.correct_case_event(uuid, text, text) from public;
revoke execute on function public.correct_case_event(uuid, text, text) from anon;
revoke execute on function public.correct_case_event(uuid, text, text) from authenticated;


-- ── redact_case_event ─────────────────────────────────────────────────────
-- 機密情報の削除（伏せ字化）。message_text/translated_text/ai_assistを
-- 復元不能な固定文言へ置き換える。kind/occurred_atは変えないため、
-- フォロー期限の計算やイベントの順序には影響しない（再計算は不要）。
create or replace function public.redact_case_event(
    p_event_id uuid
)
returns jsonb
language plpgsql
volatile
security definer
set search_path = ''
as $$
declare
    v_uid uuid := auth.uid();
    v_ev  record;
begin
    if v_uid is null then
        raise exception 'not authenticated' using errcode = '28000';
    end if;

    select e.id, e.redacted_at
      into v_ev
      from public.case_events e
     where e.id = p_event_id
       and e.user_id = v_uid
     for update;

    if v_ev.id is null then
        raise exception 'event not found' using errcode = '22023';
    end if;
    if v_ev.redacted_at is not null then
        raise exception 'already redacted' using errcode = '22023';
    end if;

    update public.case_events
       set message_text    = '［伏せ字化済み：内容は削除されました］',
           translated_text = '［伏せ字化済み：内容は削除されました］',
           ai_assist        = null,
           redacted_at      = now()
     where id = p_event_id;

    return jsonb_build_object('event_id', p_event_id, 'redacted', true);
end;
$$;

comment on function public.redact_case_event(uuid) is
  '機密情報の削除（伏せ字化）。本文・翻訳・AI支援結果を復元不能な固定文言へ置き換える';

revoke execute on function public.redact_case_event(uuid) from public;
revoke execute on function public.redact_case_event(uuid) from anon;
revoke execute on function public.redact_case_event(uuid) from authenticated;


-- ── save_ai_assist_result ─────────────────────────────────────────────────
-- 将来のAI返信アシスタントが結果を保存するための専用RPC。今回のフェーズでは
-- Python側からAI APIを呼び出さないため、このRPC自体もまだ呼ばれない
-- （呼び出し用意だけを先に作る）。対象は本人の受信イベント（reply/
-- condition_answer）だけ。ai_assist以外の列は一切変更しない。
create or replace function public.save_ai_assist_result(
    p_event_id  uuid,
    p_ai_assist jsonb
)
returns jsonb
language plpgsql
volatile
security definer
set search_path = ''
as $$
declare
    v_uid uuid := auth.uid();
    v_ev  record;
begin
    if v_uid is null then
        raise exception 'not authenticated' using errcode = '28000';
    end if;

    if p_ai_assist is null or jsonb_typeof(p_ai_assist) <> 'object' then
        raise exception 'invalid payload' using errcode = '22023';
    end if;
    if octet_length(p_ai_assist::text) > 20000 then
        raise exception 'payload too large' using errcode = '22023';
    end if;

    select e.id, e.kind, e.voided_at, e.redacted_at
      into v_ev
      from public.case_events e
     where e.id = p_event_id
       and e.user_id = v_uid
     for update;

    if v_ev.id is null then
        raise exception 'event not found' using errcode = '22023';
    end if;
    if v_ev.kind not in ('reply', 'condition_answer') then
        raise exception 'not an inbound message' using errcode = '22023';
    end if;
    if v_ev.voided_at is not null then
        raise exception 'event voided' using errcode = '22023';
    end if;
    if v_ev.redacted_at is not null then
        raise exception 'event redacted' using errcode = '22023';
    end if;

    update public.case_events
       set ai_assist = p_ai_assist
     where id = p_event_id;

    return jsonb_build_object('event_id', p_event_id, 'saved', true);
end;
$$;

comment on function public.save_ai_assist_result(uuid, jsonb) is
  'AI返信アシスタントの結果を保存する。対象は本人の受信イベント（reply/'
  'condition_answer）だけ。伏せ字化・取消し済みのイベントには保存できない';

revoke execute on function public.save_ai_assist_result(uuid, jsonb) from public;
revoke execute on function public.save_ai_assist_result(uuid, jsonb) from anon;
revoke execute on function public.save_ai_assist_result(uuid, jsonb) from authenticated;


-- ── updated_at の自動更新（既存のpublic.set_updated_at()を再利用） ───────────
drop trigger if exists cases_set_updated_at on public.cases;
create trigger cases_set_updated_at
    before update on public.cases
    for each row execute function public.set_updated_at();

drop trigger if exists case_targets_set_updated_at on public.case_targets;
create trigger case_targets_set_updated_at
    before update on public.case_targets
    for each row execute function public.set_updated_at();

commit;
