-- =============================================================================
-- 0001_tables.sql  —  フェーズ3A: テーブル・制約・インデックス
--
-- 適用順序: 0001 → 0002 → 0003 → 0004 →（任意）0005
-- 何度実行しても壊れないように書いている（if not exists / do $$ ... $$）。
--
-- 設計の要点:
--   products     商品の共通情報。URLごとに1行。insert-once（後から自動更新しない）
--   saved_items  誰がどの商品を保存したか。活動メモ・ステータス
--   profiles     利用者のメールと表示名（管理者ビューで「誰の候補か」を出すため）
--   app_admins   管理者のuser_id。クライアントからは一切触れない
--
-- 可視範囲・権限は 0003_rls_grants.sql で設定する。
-- =============================================================================

begin;

-- ── products ────────────────────────────────────────────────────────────────
-- url_key は正規化済みURL。比較は必ず「=」の完全一致のみで行う
-- （LIKE / ILIKE / text_search には使わない）。
create table if not exists public.products (
    id               uuid        primary key default gen_random_uuid(),
    url_key          text        not null,
    source_url       text        not null,
    platform         text        not null default '',
    name             text        not null default '',
    maker            text        not null default '',
    genre            text        not null default '',
    raised_jpy       bigint      not null default 0,
    raised_usd       numeric(14,2) not null default 0,
    backers          integer     not null default 0,
    priority         text        not null default '',
    confidence       text        not null default '',
    analysis         jsonb       not null default '{}'::jsonb,
    contact          jsonb       not null default '{}'::jsonb,
    schema_version   integer     not null default 2,
    last_analyzed_at timestamptz not null default now(),
    created_by       uuid        not null references auth.users(id) on delete cascade,
    created_at       timestamptz not null default now(),
    updated_at       timestamptz not null default now(),

    -- 同じ商品の二重登録を防ぐ最後の砦（アプリの正規化にバグが出ても増えない）
    constraint products_url_key_unique unique (url_key),

    -- 利用者入力の現実的な上限。save_candidate() 側でも検証・切り詰める
    constraint products_url_key_len    check (char_length(url_key)    between 20 and 1024),
    constraint products_source_url_len check (char_length(source_url) between 10 and 2048),
    constraint products_platform_len   check (char_length(platform)   <= 50),
    constraint products_name_len       check (char_length(name)       <= 500),
    constraint products_maker_len      check (char_length(maker)      <= 300),
    constraint products_genre_len      check (char_length(genre)      <= 200),
    constraint products_confidence_len check (char_length(confidence) <= 100),
    constraint products_priority_ok    check (priority in ('A', 'B', 'C', '')),
    constraint products_raised_jpy_ok  check (raised_jpy between 0 and 999999999999999),
    constraint products_raised_usd_ok  check (raised_usd between 0 and 9999999999.99),
    constraint products_backers_ok     check (backers    between 0 and 100000000),
    constraint products_schema_ok      check (schema_version between 1 and 1000),
    -- jsonb は必ずオブジェクト。配列や数値を入れさせない
    constraint products_analysis_obj   check (jsonb_typeof(analysis) = 'object'),
    constraint products_contact_obj    check (jsonb_typeof(contact)  = 'object'),
    constraint products_analysis_size  check (octet_length(analysis::text) <= 100000),
    constraint products_contact_size   check (octet_length(contact::text)  <= 20000)
);

comment on table  public.products is
  '商品の共通情報。URLごとに1行。insert-once（フェーズ3では自動更新しない）';
comment on column public.products.url_key is
  '正規化済みURL。比較は「=」の完全一致のみ。LIKE/ILIKE/text_searchには使わない';
comment on column public.products.created_by is
  '最初に登録した利用者。誰の調査対象かを漏らさないため一般利用者には列ごと見せない';
comment on column public.products.last_analyzed_at is
  '分析日時。insert-onceのため「いつ時点の分析か」を画面に出す';


-- ── saved_items ─────────────────────────────────────────────────────────────
create table if not exists public.saved_items (
    id                uuid        primary key default gen_random_uuid(),
    user_id           uuid        not null references auth.users(id) on delete cascade,
    product_id        uuid        not null references public.products(id) on delete cascade,
    memo              text        not null default '',
    status            text        not null default '候補',
    priority_override text,
    archived          boolean     not null default false,
    saved_at          timestamptz not null default now(),
    updated_at        timestamptz not null default now(),

    -- 同じ人が同じ商品を二重保存できない
    constraint saved_items_user_product_unique unique (user_id, product_id),

    constraint saved_items_status_ok check (status in (
        '候補', '精査中', '連絡済み', '返信あり',
        '交渉中', '契約済み', '保留', '見送り')),
    constraint saved_items_priority_ok check (
        priority_override is null or priority_override in ('A', 'B', 'C')),
    constraint saved_items_memo_len check (char_length(memo) <= 5000)
);

comment on table  public.saved_items is
  '利用者ごとの候補。本人と管理者だけが閲覧でき、更新・削除は本人のみ';
comment on column public.saved_items.memo is
  '活動メモ。管理者がサポート目的で閲覧できることを受講生へ案内する';
comment on column public.saved_items.updated_at is
  'トリガーが自動更新する。利用者には UPDATE 権限を与えない（楽観ロックに使う）';

-- 一覧の既定の並び（自分の・未アーカイブ・新しい順）
create index if not exists saved_items_user_list_idx
    on public.saved_items (user_id, archived, saved_at desc);
-- products の RLS が使う EXISTS を速くする
create index if not exists saved_items_product_user_idx
    on public.saved_items (product_id, user_id);


-- ── profiles ────────────────────────────────────────────────────────────────
-- 管理者権限に関わる列は「置かない」。権限は app_admins にしか存在しない。
create table if not exists public.profiles (
    user_id      uuid        primary key references auth.users(id) on delete cascade,
    email        text        not null,
    display_name text        not null default '',
    updated_at   timestamptz not null default now(),

    constraint profiles_email_len        check (char_length(email)        between 3 and 320),
    constraint profiles_display_name_len check (char_length(display_name) <= 100)
);

comment on table  public.profiles is
  '利用者の表示情報。email はトリガーが auth.users から入れ、本人は変更できない';


-- ── app_admins ──────────────────────────────────────────────────────────────
-- クライアントからは到達できない（RLSポリシーを作らず、権限も与えない）。
-- 読めるのは SECURITY DEFINER 関数 public.is_admin() だけ。
create table if not exists public.app_admins (
    user_id    uuid        primary key references auth.users(id) on delete cascade,
    note       text        not null default '',
    created_at timestamptz not null default now(),

    constraint app_admins_note_len check (char_length(note) <= 200)
);

comment on table public.app_admins is
  '管理者のuser_id。ポリシーも権限も与えないため、クライアントからは読み書きできない';

commit;
