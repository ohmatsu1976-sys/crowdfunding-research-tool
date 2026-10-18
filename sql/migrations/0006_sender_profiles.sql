-- =============================================================================
-- 0006_sender_profiles.sql  —  送信者プロフィール（氏名・会社名の英語表記）
--
-- 営業メール生成に使う氏名・会社名を、利用者ごとに複数保存し、画面で選べる
-- ようにするための新規テーブル。既存の products/saved_items/profiles/
-- app_admins とは独立しており、認証・権限判定（app_metadata.role による
-- summitモード判定・is_admin() RPCによる管理者判定）には一切関わらない。
--
-- 保存・更新・削除は saved_items の更新・削除と同じ考え方で、RLSで
-- 「本人の行だけ」に絞る。save_candidate() のような専用RPCは使わず、
-- クライアントから直接 INSERT/UPDATE/DELETE できるようにする
-- （書き込み内容が単純な自己申告テキストで、products のような
-- insert-once・重複検出・ドメイン検証といった複雑な制御が不要なため）。
--
-- 適用順序: 0001〜0005のあとに、この0006を実行する。
-- 何度実行しても壊れないように書いている（if not exists / drop ... if exists）。
-- =============================================================================

begin;

-- ── テーブル ────────────────────────────────────────────────────────────────
create table if not exists public.sender_profiles (
    id               uuid        primary key default gen_random_uuid(),
    user_id          uuid        not null references auth.users(id) on delete cascade,
    sender_name_en   text        not null,
    company_name_en  text        not null default '',
    is_default       boolean     not null default false,
    last_used_at     timestamptz,
    created_at       timestamptz not null default now(),
    updated_at       timestamptz not null default now(),

    -- 利用者入力の現実的な上限（アプリ側のsender_profiles.pyでも同じ値を検証する）
    constraint sender_profiles_name_len    check (char_length(sender_name_en)   between 1 and 200),
    constraint sender_profiles_company_len check (char_length(company_name_en) <= 200),

    -- 同じ利用者が「氏名＋会社名」の完全一致を重複登録できない
    -- （二重クリック・Streamlitの再実行による重複もここで最終的に防ぐ）
    constraint sender_profiles_unique_per_user unique (user_id, sender_name_en, company_name_en)
);

comment on table public.sender_profiles is
  '送信者プロフィール（営業メール用の氏名・会社名の英語表記）。本人のみ閲覧・登録・編集・削除できる。認証・権限判定には使わない';
comment on column public.sender_profiles.is_default is
  '次回表示時に自動選択する既定プロフィール。1利用者につき実質0または1件（アプリ側で他の行をfalseに戻してから設定する）';
comment on column public.sender_profiles.last_used_at is
  '最後に営業メール生成へ使われた日時。自動選択の判定に使う（is_defaultが無い場合の代替基準）';

create index if not exists sender_profiles_user_idx
    on public.sender_profiles (user_id, last_used_at desc);

-- 同一利用者がis_default=trueを複数持てないことをDB側でも保証する
-- （アプリ側の_clear_other_defaultsは「先に他をfalseへ戻す」ベストエフォートの
-- 事前処理で、同時更新・複数タブからの競合までは防げない。この一意索引に
-- 違反した場合、sender_profiles.pyは「氏名・会社名の重複」とは別の
-- DEFAULT_CONFLICT文言を返す）。
create unique index if not exists sender_profiles_one_default_per_user
    on public.sender_profiles (user_id)
    where is_default;

-- updated_at の自動更新（0002_functions.sql で作成済みの set_updated_at() を再利用）
drop trigger if exists sender_profiles_set_updated_at on public.sender_profiles;
create trigger sender_profiles_set_updated_at
    before update on public.sender_profiles
    for each row execute function public.set_updated_at();


-- ── RLS ────────────────────────────────────────────────────────────────────
-- Supabase は新規テーブルへ anon/authenticated の権限を既定で付与するため、
-- 列単位GRANTの前に必ず revoke all しておく（0003_rls_grants.sqlと同じ順序）。
revoke all on public.sender_profiles from anon, authenticated;

alter table public.sender_profiles enable row level security;

drop policy if exists sender_profiles_select_own on public.sender_profiles;
create policy sender_profiles_select_own on public.sender_profiles
    for select to authenticated
    using (user_id = (select auth.uid()));

drop policy if exists sender_profiles_insert_own on public.sender_profiles;
create policy sender_profiles_insert_own on public.sender_profiles
    for insert to authenticated
    with check (user_id = (select auth.uid()));

-- USING と WITH CHECK の両方を書く。USING だけだと本人の行のuser_idを
-- 他人へ付け替えて渡せてしまう（saved_items_update_own と同じ理由）。
drop policy if exists sender_profiles_update_own on public.sender_profiles;
create policy sender_profiles_update_own on public.sender_profiles
    for update to authenticated
    using      (user_id = (select auth.uid()))
    with check (user_id = (select auth.uid()));

drop policy if exists sender_profiles_delete_own on public.sender_profiles;
create policy sender_profiles_delete_own on public.sender_profiles
    for delete to authenticated
    using (user_id = (select auth.uid()));


-- ── 列単位の GRANT（revoke all の後に置くこと）───────────────────────────────
-- SELECTは本人の行の全列（id/created_at/updated_atを含む。他人には見えないため非敏感）。
grant select (
    id, user_id, sender_name_en, company_name_en, is_default, last_used_at,
    created_at, updated_at
) on public.sender_profiles to authenticated;

-- INSERTはuser_idを含めて許可するが、WITH CHECKでauth.uid()と一致しない値を拒否する
-- （他人のuser_idを指定して登録できないことの最終防御はRLS側で行う）。
grant insert (user_id, sender_name_en, company_name_en, is_default)
    on public.sender_profiles to authenticated;

-- UPDATEはプロフィール本体の3列＋last_used_atだけ。user_id/id/created_at/updated_atは
-- 対象外（updated_atはトリガーが自動更新する）。
grant update (sender_name_en, company_name_en, is_default, last_used_at)
    on public.sender_profiles to authenticated;

grant delete on public.sender_profiles to authenticated;

commit;
