-- =============================================================================
-- 0007_cases_tables.sql  —  案件管理（第1段階「返信獲得」）: テーブル・制約・索引
--
-- 適用順序: 0001〜0006 のあとに、0007 → 0008 → 0009 の順に実行する。
-- 何度実行しても壊れないように書いている（if not exists）。
--
-- 設計の要点:
--   cases         商品案件。1利用者×1商品につき1行。商品名・メーカー・連絡先候補は
--                 作成時点のスナップショットを持つ（saved_items/productsが後から
--                 削除・変更されても、案件に必要な情報は消えない）。
--   case_events   アプローチ履歴（送信・フォロー・返信・条件確認/回答・状態変更・
--                 メモ）を1本のタイムラインとして記録する。追記型。訂正・伏せ字化・
--                 取消しは voided_at/redacted_at/superseded_by の列で表現し、
--                 物理削除はしない。
--   case_targets  利用者ごと・月ごとのアプローチ目標数・返信目標数。
--
-- 既存の products / saved_items / profiles / app_admins / sender_profiles、
-- および 0001〜0006 のマイグレーションには一切変更を加えない。
--
-- 書き込みの経路は 0008_cases_functions.sql のRPCに限定する。
-- cases・case_events への直接INSERTは許可しない（0009で権限を確認する）。
-- =============================================================================

begin;

-- ── cases ──────────────────────────────────────────────────────────────────
-- status の7状態・phase の4区分は 0008 のRPC（状態機械）でのみ遷移させる。
-- 直接UPDATEを許可する列は memo / next_action_at / next_action_label / archived /
-- primary_channel の5つだけ（0009で列単位GRANTを設定する）。
create table if not exists public.cases (
    id               uuid        primary key default gen_random_uuid(),
    user_id          uuid        not null references auth.users(id) on delete cascade,
    product_id       uuid        not null references public.products(id) on delete restrict,
    saved_item_id    uuid        references public.saved_items(id) on delete set null,

    -- 作成時点のスナップショット（products/saved_itemsの削除・変更に左右されない）
    platform         text        not null default '',
    product_name     text        not null default '',
    maker_name       text        not null default '',
    source_url       text        not null default '',
    contact_snapshot jsonb       not null default '{}'::jsonb,

    phase            text        not null default 'reply',
    status           text        not null default 'not_contacted',
    primary_channel  text        not null default 'none',

    first_sent_at    timestamptz,
    last_reply_at    timestamptz,
    advanced_at      timestamptz,
    has_reply        boolean     not null default false,

    next_action_at    date,
    next_action_label text       not null default '',

    memo             text        not null default '',
    archived         boolean     not null default false,

    created_at       timestamptz not null default now(),
    updated_at       timestamptz not null default now(),

    -- 同じ利用者が同じ商品で二重に案件を作れない（create_case_from_candidate()の
    -- on conflict do nothingと組み合わせて、二重クリックでも重複しない）
    constraint cases_user_product_unique unique (user_id, product_id),

    constraint cases_platform_len          check (char_length(platform)          <= 50),
    constraint cases_product_name_len      check (char_length(product_name)      <= 500),
    constraint cases_maker_name_len        check (char_length(maker_name)        <= 300),
    constraint cases_source_url_len        check (char_length(source_url)        <= 2048),
    constraint cases_memo_len              check (char_length(memo)              <= 5000),
    constraint cases_next_action_label_len check (char_length(next_action_label) <= 100),

    constraint cases_phase_ok check (phase in ('reply', 'deal', 'plan', 'launch')),
    constraint cases_status_ok check (status in (
        'not_contacted', 'awaiting_first_reply', 'awaiting_our_reply',
        'awaiting_next_reply', 'advanced', 'on_hold', 'lost')),
    constraint cases_channel_ok check (primary_channel in (
        'email', 'facebook', 'instagram', 'form', 'linkedin', 'official', 'listing', 'none')),

    -- jsonbは必ずオブジェクト。create_case_from_candidate()がコピー対象のキーを
    -- 明示的に制限するため、ここでは型とサイズだけを守る
    constraint cases_contact_snapshot_obj  check (jsonb_typeof(contact_snapshot) = 'object'),
    constraint cases_contact_snapshot_size check (octet_length(contact_snapshot::text) <= 20000)
);

comment on table  public.cases is
  '商品案件。1利用者×1商品につき1行。商品情報・連絡先候補は作成時点のスナップショット';
comment on column public.cases.contact_snapshot is
  'create_case_from_candidate()が products.contact から許可リストのキーだけをコピーしたもの';
comment on column public.cases.status is
  '0008_cases_functions.sqlのRPC（状態機械）でのみ遷移する。直接UPDATEは許可しない';

create index if not exists cases_user_list_idx
    on public.cases (user_id, archived, status, next_action_at);
create index if not exists cases_saved_item_idx
    on public.cases (saved_item_id);


-- ── case_events ───────────────────────────────────────────────────────────
-- 追記型のタイムライン。直接INSERT/UPDATE/DELETEは許可しない
-- （0008のRPC経由のみ。0009でSELECTだけをGRANTする）。
-- 訂正・伏せ字化・取消しは行を書き換える／新しい行を足すだけで、物理削除はしない。
create table if not exists public.case_events (
    id             uuid        primary key default gen_random_uuid(),
    case_id        uuid        not null references public.cases(id) on delete cascade,
    user_id        uuid        not null references auth.users(id) on delete cascade,

    kind           text        not null,
    channel        text        not null default '',
    recipient      text        not null default '',
    occurred_at    timestamptz not null,

    message_text    text       not null default '',
    translated_text text       not null default '',
    -- AI返信アシスタントの結果（翻訳・意図整理・質問抽出・前向き度・返信案）。
    -- 受信イベント（reply/condition_answer）だけが対象。保存は save_ai_assist_result()
    -- のRPC経由のみ（0008参照）。伏せ字化すると null に戻す。
    ai_assist      jsonb,

    -- kind='status_change' の時だけ使う（void_case_event()の再計算に必要なため、
    -- 遷移先を列として保持する。advanced/on_hold/lost 以外は入らない）
    target_status  text,

    -- 訂正・取消しの印（物理削除はしない）
    voided_at      timestamptz,
    voided_reason  text        not null default '',
    superseded_by  uuid        references public.case_events(id) on delete set null,
    -- 伏せ字化の印。message_text/translated_text/ai_assistが復元不能な固定文言に
    -- 置き換えられたことを示す
    redacted_at    timestamptz,

    created_at     timestamptz not null default now(),

    constraint case_events_kind_ok check (kind in (
        'initial', 'followup1', 'followup2', 'reply', 'our_reply',
        'condition_check', 'condition_answer', 'status_change', 'note')),
    constraint case_events_channel_ok check (channel in (
        '', 'email', 'facebook', 'instagram', 'form', 'linkedin',
        'official', 'listing', 'other')),
    constraint case_events_target_status_ok check (
        target_status is null or target_status in ('advanced', 'on_hold', 'lost')),

    constraint case_events_recipient_len      check (char_length(recipient)      <= 500),
    constraint case_events_message_len        check (char_length(message_text)   <= 20000),
    constraint case_events_translated_len     check (char_length(translated_text) <= 20000),
    constraint case_events_voided_reason_len  check (char_length(voided_reason)  <= 300),

    constraint case_events_ai_assist_obj  check (
        ai_assist is null or jsonb_typeof(ai_assist) = 'object'),
    constraint case_events_ai_assist_size check (
        octet_length(coalesce(ai_assist::text, '')) <= 20000)
);

comment on table  public.case_events is
  'アプローチ履歴（送信・フォロー・返信・条件確認/回答・状態変更・メモ）の追記型タイムライン。'
  '直接の書き込み経路は無く、0008_cases_functions.sqlのRPCだけが書き込む';
comment on column public.case_events.voided_at is
  '取消し・訂正済みの印（物理削除はしない）。nullなら有効なイベント';
comment on column public.case_events.redacted_at is
  '伏せ字化済みの印。message_text/translated_text/ai_assistは復元不能な固定文言になる';

create index if not exists case_events_case_idx
    on public.case_events (case_id, occurred_at);
create index if not exists case_events_user_idx
    on public.case_events (user_id, occurred_at desc);


-- ── case_targets ──────────────────────────────────────────────────────────
-- 利用者ごと・月ごとのアプローチ目標数・返信目標数。過去月は上書きされない。
-- target_month は常にJST基準の月初日（例: 2026-11-01）に正規化する。
create table if not exists public.case_targets (
    user_id          uuid        not null references auth.users(id) on delete cascade,
    target_month     date        not null,
    approach_target  integer     not null default 0,
    reply_target     integer     not null default 0,
    updated_at       timestamptz not null default now(),

    primary key (user_id, target_month),

    constraint case_targets_month_is_first_day check (
        target_month = date_trunc('month', target_month)::date),
    constraint case_targets_approach_ok check (approach_target between 0 and 100000),
    constraint case_targets_reply_ok    check (reply_target    between 0 and 100000)
);

comment on table public.case_targets is
  '利用者ごと・月ごとのアプローチ目標数・返信目標数。target_monthはJST基準の月初日';

commit;
