-- =============================================================================
-- 0002_functions.sql  —  フェーズ3A: 関数とトリガー
--
-- PostgreSQL は関数作成時に PUBLIC へ EXECUTE を与える。このファイルでは
-- 各関数を作成した直後、同じトランザクション内で PUBLIC / anon / authenticated
-- から EXECUTE を REVOKE する。したがって【0002単独の実行が完了した時点】で、
-- どの関数も外部（PUBLIC・anon・authenticated のいずれからも）から呼べない
-- 状態になる。0003_rls_grants.sql は、そこから is_admin() と save_candidate()
-- の2つだけを authenticated へ改めて GRANT する。
--
-- 内部専用の関数（handle_new_user / set_updated_at）はトリガーとしてのみ動く。
-- トリガーの発火は EXECUTE 権限のチェックを経由しないため、EXECUTE を
-- 誰にも与えなくても正常に動作する。
--
-- SECURITY DEFINER 関数の約束（全関数で守る）:
--   - SET search_path = ''
--   - 参照先はすべてスキーマ付きで完全修飾（public.xxx / auth.xxx）
--   - auth.uid() が NULL なら拒否
--   - 任意の user_id を引数に取らない（保存者は必ず auth.uid() から決める）
--   - 返却情報は必要最小限
--   - オーバーロードを作らない（1関数1シグネチャ）
-- =============================================================================

begin;

-- ── is_admin ────────────────────────────────────────────────────────────────
-- 管理者判定。app_admins はクライアントに権限を与えないため、
-- SECURITY DEFINER のこの関数だけが読める。
-- user_metadata は本人が update_user() で書き換えられるので使わない。
create or replace function public.is_admin()
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
    select auth.uid() is not null
       and exists (
           select 1
             from public.app_admins a
            where a.user_id = auth.uid()
       );
$$;

comment on function public.is_admin() is
  '管理者かどうか。app_admins を読む唯一の経路。保存処理とは分離している';

-- 作成直後にその場で権限を遮断する（0003を待たない）。
-- 最終的に authenticated への EXECUTE は 0003 で改めて GRANT する。
revoke execute on function public.is_admin() from public;
revoke execute on function public.is_admin() from anon;
revoke execute on function public.is_admin() from authenticated;


-- ── save_candidate ──────────────────────────────────────────────────────────
-- 候補保存の唯一の書き込み口。
-- products / saved_items への INSERT 権限は誰にも与えないため、
-- 保存はこの関数を通す以外に方法がない。
--
-- url_key は「=」の完全一致だけで使う。LIKE / ILIKE / text_search は使わない。
-- したがって1回の呼び出しで触れるのは 0 件か 1 件で、走査による発見はできない。
--
-- 戻り値は3項目のみ。「商品行が既にあったか」は返さない
-- （他人が登録済みであることを推測させないため）。
create or replace function public.save_candidate(
    p_url_key    text,
    p_source_url text,
    p_product    jsonb
)
returns jsonb
language plpgsql
volatile
security definer
set search_path = ''
as $$
declare
    v_uid  uuid := auth.uid();
    v_key  text;
    v_src  text;
    v_host text;
    v_pid  uuid;
    v_sid  uuid;
    v_dup  boolean := false;
begin
    -- 1) 未ログインは拒否 -----------------------------------------------------
    if v_uid is null then
        raise exception 'not authenticated' using errcode = '28000';
    end if;

    -- 2) ペイロードの型と大きさ ----------------------------------------------
    if p_product is null or jsonb_typeof(p_product) <> 'object' then
        raise exception 'invalid payload' using errcode = '22023';
    end if;
    if octet_length(p_product::text) > 100000 then
        raise exception 'payload too large' using errcode = '22023';
    end if;
    -- analysis / contact は省略可。あるならオブジェクトであること
    if coalesce(jsonb_typeof(p_product -> 'analysis'), 'object') <> 'object' then
        raise exception 'invalid analysis' using errcode = '22023';
    end if;
    if coalesce(jsonb_typeof(p_product -> 'contact'), 'object') <> 'object' then
        raise exception 'invalid contact' using errcode = '22023';
    end if;
    if octet_length(coalesce(p_product -> 'analysis', '{}'::jsonb)::text) > 100000 then
        raise exception 'analysis too large' using errcode = '22023';
    end if;
    if octet_length(coalesce(p_product -> 'contact', '{}'::jsonb)::text) > 20000 then
        raise exception 'contact too large' using errcode = '22023';
    end if;

    -- 3) url_key の検証 -------------------------------------------------------
    --    アプリ（product_key.py）とDBで合意した正規化形式:
    --      https:// + ホスト名（www.なし） + パス
    --      クエリなし / フラグメントなし / 末尾スラッシュなし
    --
    --    ドメインは「文字列の部分一致」では判定しない。https:// の直後から
    --    最初の "/" までを v_host として切り出し（＝URLのホスト名部分の抽出）、
    --    許可リストと完全一致でのみ比較する。これにより
    --      ・zeczec.com.example.com のような「後ろに何かが続く」偽装
    --      ・example-zeczec.com のような「似た名前」偽装
    --    のいずれも v_host が許可リストの文字列と一致しないため拒否される。
    --
    --    対象ドメインを増やすときは、この配列と product_key.ALLOWED_HOSTS の
    --    両方を同時に更新すること（tests/test_sql_migrations.py が一致を検証する）。
    v_key := btrim(coalesce(p_url_key, ''));
    if v_key = '' then
        raise exception 'url is required' using errcode = '22023';
    end if;
    if char_length(v_key) not between 20 and 1024 then
        raise exception 'url length out of range' using errcode = '22023';
    end if;
    -- 形式チェック: https:// + [a-z0-9.-]のみのホスト + "/" + クエリ/フラグメント/空白を含まないパス
    if v_key !~ '^https://[a-z0-9.-]+/[^?#[:space:]]+$' then
        raise exception 'url not normalized' using errcode = '22023';
    end if;
    if right(v_key, 1) = '/' then
        raise exception 'url not normalized' using errcode = '22023';
    end if;
    -- ホスト名の抽出（"https://" と最初の "/" の間だけ）→ 完全一致で判定
    v_host := substring(v_key from '^https://([a-z0-9.-]+)/');
    if v_host is null or v_host not in ('kickstarter.com', 'indiegogo.com', 'zeczec.com') then
        raise exception 'url not allowed' using errcode = '22023';
    end if;

    -- 4) 表示用の元URL --------------------------------------------------------
    v_src := btrim(coalesce(p_source_url, ''));
    if v_src = '' then
        v_src := v_key;
    end if;
    if char_length(v_src) not between 10 and 2048 then
        raise exception 'source url length out of range' using errcode = '22023';
    end if;
    if v_src !~ '^https?://' then
        raise exception 'source url not allowed' using errcode = '22023';
    end if;

    -- 5) 商品は insert-once。既にあれば一切更新しない -------------------------
    --    数値は形を確かめてから型変換する（壊れた入力で例外にしない）
    insert into public.products (
        url_key, source_url, platform, name, maker, genre,
        raised_jpy, raised_usd, backers,
        priority, confidence, analysis, contact,
        schema_version, last_analyzed_at, created_by
    )
    values (
        v_key,
        v_src,
        left(coalesce(p_product ->> 'platform',   ''),  50),
        left(coalesce(p_product ->> 'name',       ''), 500),
        left(coalesce(p_product ->> 'maker',      ''), 300),
        left(coalesce(p_product ->> 'genre',      ''), 200),
        case when p_product ->> 'raised_jpy' ~ '^[0-9]{1,15}$'
             then (p_product ->> 'raised_jpy')::bigint else 0 end,
        case when p_product ->> 'raised_usd' ~ '^[0-9]{1,10}(\.[0-9]{1,2})?$'
             then (p_product ->> 'raised_usd')::numeric else 0 end,
        case when p_product ->> 'backers' ~ '^[0-9]{1,8}$'
             then (p_product ->> 'backers')::integer else 0 end,
        case when p_product ->> 'priority' in ('A', 'B', 'C')
             then p_product ->> 'priority' else '' end,
        left(coalesce(p_product ->> 'confidence', ''), 100),
        coalesce(p_product -> 'analysis', '{}'::jsonb),
        coalesce(p_product -> 'contact',  '{}'::jsonb),
        case when p_product ->> 'schema_version' ~ '^[0-9]{1,3}$'
             then (p_product ->> 'schema_version')::integer else 2 end,
        now(),
        v_uid
    )
    on conflict (url_key) do nothing;

    -- 6) 完全一致で id を引く（5)が0行でも確実に取れる）------------------------
    select p.id into v_pid
      from public.products p
     where p.url_key = v_key;

    if v_pid is null then
        raise exception 'product not available' using errcode = '22023';
    end if;

    -- 7) 保存者は必ず auth.uid()。引数からは決まらない -------------------------
    insert into public.saved_items (user_id, product_id)
    values (v_uid, v_pid)
    on conflict (user_id, product_id) do nothing
    returning id into v_sid;

    if v_sid is null then
        v_dup := true;
        select s.id into v_sid
          from public.saved_items s
         where s.user_id = v_uid
           and s.product_id = v_pid;
    end if;

    -- 8) 返すのは3項目だけ ----------------------------------------------------
    --    他利用者の user_id / メール / 保存日時 / 活動メモ / ステータスは返さない。
    --    already_saved は「自分が既に保存していたか」であり他人の情報を含まない。
    return jsonb_build_object(
        'saved_item_id', v_sid,
        'product_id',    v_pid,
        'already_saved', v_dup
    );
end;
$$;

comment on function public.save_candidate(text, text, jsonb) is
  '候補保存の唯一の書き込み口。保存者は auth.uid() で決まり、引数では指定できない';

-- 作成直後にその場で権限を遮断する（0003を待たない）。
-- 最終的に authenticated への EXECUTE は 0003 で改めて GRANT する。
revoke execute on function public.save_candidate(text, text, jsonb) from public;
revoke execute on function public.save_candidate(text, text, jsonb) from anon;
revoke execute on function public.save_candidate(text, text, jsonb) from authenticated;


-- ── profiles の自動作成 ─────────────────────────────────────────────────────
-- 新しい利用者が作られたら profiles を1行作る。
-- email はここでだけ入る（本人には UPDATE 権限を与えない）。
create or replace function public.handle_new_user()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
    insert into public.profiles (user_id, email)
    values (
        new.id,
        left(coalesce(nullif(new.email, ''), 'unknown-' || new.id::text), 320)
    )
    on conflict (user_id) do update
        set email      = excluded.email,
            updated_at = now();
    return new;
end;
$$;

comment on function public.handle_new_user() is
  'auth.users への INSERT で profiles を自動作成する';

-- 内部専用。クライアントから直接実行できないようにする（トリガーは影響を受けない）。
revoke execute on function public.handle_new_user() from public;
revoke execute on function public.handle_new_user() from anon;
revoke execute on function public.handle_new_user() from authenticated;

drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created
    after insert on auth.users
    for each row execute function public.handle_new_user();


-- ── updated_at の自動更新 ───────────────────────────────────────────────────
-- 利用者に updated_at の UPDATE 権限を与えないため、DB側で必ず更新する。
-- 楽観ロック（読み込み時の updated_at と一致する行だけ更新）の土台になる。
create or replace function public.set_updated_at()
returns trigger
language plpgsql
security invoker
set search_path = ''
as $$
begin
    new.updated_at := now();
    return new;
end;
$$;

comment on function public.set_updated_at() is
  'BEFORE UPDATE で updated_at を現在時刻にする。利用者は直接変更できない';

-- 内部専用。クライアントから直接実行できないようにする（トリガーは影響を受けない）。
revoke execute on function public.set_updated_at() from public;
revoke execute on function public.set_updated_at() from anon;
revoke execute on function public.set_updated_at() from authenticated;

drop trigger if exists products_set_updated_at on public.products;
create trigger products_set_updated_at
    before update on public.products
    for each row execute function public.set_updated_at();

drop trigger if exists saved_items_set_updated_at on public.saved_items;
create trigger saved_items_set_updated_at
    before update on public.saved_items
    for each row execute function public.set_updated_at();

drop trigger if exists profiles_set_updated_at on public.profiles;
create trigger profiles_set_updated_at
    before update on public.profiles
    for each row execute function public.set_updated_at();

commit;
