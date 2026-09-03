-- =============================================================================
-- 0003_rls_grants.sql  —  フェーズ3A: RLS・ポリシー・GRANT / REVOKE
--
-- 【順序が重要】
-- PostgreSQL ではテーブルレベルの SELECT 権限が全列をカバーするため、
-- テーブルレベルの権限が残ったまま列を REVOKE しても効かない。
-- 必ず「① revoke all → ② 必要な列だけ grant」の順で書く。
-- Supabase は新規テーブルへ anon / authenticated の権限を既定で付与するので、
-- ①の revoke は省略できない。
--
-- 可視範囲:
--   products     自分が保存した商品のみ（＋管理者は全件）
--   saved_items  本人のみ（＋管理者は閲覧のみ）
--   profiles     本人のみ（＋管理者は閲覧のみ）
--   app_admins   誰も触れない（ポリシーも権限も与えない）
-- =============================================================================

begin;

-- ═══ ① まず全ロールから全権限を剥がす ═══════════════════════════════════════
revoke all on public.products    from anon, authenticated;
revoke all on public.saved_items from anon, authenticated;
revoke all on public.profiles    from anon, authenticated;
revoke all on public.app_admins  from anon, authenticated;


-- ═══ ② RLS を有効化 ════════════════════════════════════════════════════════
-- force row level security は使わない。SECURITY DEFINER 関数（テーブル所有者
-- として動く）が app_admins を読めなくなり、is_admin() が壊れるため。
alter table public.products    enable row level security;
alter table public.saved_items enable row level security;
alter table public.profiles    enable row level security;
alter table public.app_admins  enable row level security;


-- ═══ ③ ポリシー ════════════════════════════════════════════════════════════
-- auth.uid() は (select auth.uid()) と括る。行ごとの再評価が1回になり、
-- 100人規模でも一覧が遅くならない。

-- ── products ────────────────────────────────────────────────────────────────
-- 自分が保存した商品だけが見える。一覧走査で他人の調査対象を発見できない。
-- INSERT / UPDATE / DELETE のポリシーは作らない（保存は save_candidate のみ）。
drop policy if exists products_select_own_saved on public.products;
create policy products_select_own_saved on public.products
    for select to authenticated
    using (
        exists (
            select 1
              from public.saved_items si
             where si.product_id = products.id
               and si.user_id = (select auth.uid())
        )
        or public.is_admin()
    );

-- ── saved_items ─────────────────────────────────────────────────────────────
-- 管理者は閲覧のみ。UPDATE / DELETE に is_admin() を入れない。
drop policy if exists saved_items_select_own_or_admin on public.saved_items;
create policy saved_items_select_own_or_admin on public.saved_items
    for select to authenticated
    using (user_id = (select auth.uid()) or public.is_admin());

-- USING と WITH CHECK の両方を書く。USING だけだと自分の行の user_id を
-- 他人に付け替えて渡せてしまう。
drop policy if exists saved_items_update_own on public.saved_items;
create policy saved_items_update_own on public.saved_items
    for update to authenticated
    using      (user_id = (select auth.uid()))
    with check (user_id = (select auth.uid()));

drop policy if exists saved_items_delete_own on public.saved_items;
create policy saved_items_delete_own on public.saved_items
    for delete to authenticated
    using (user_id = (select auth.uid()));

-- INSERT ポリシーは作らない（save_candidate 経由のみ）

-- ── profiles ────────────────────────────────────────────────────────────────
drop policy if exists profiles_select_own_or_admin on public.profiles;
create policy profiles_select_own_or_admin on public.profiles
    for select to authenticated
    using (user_id = (select auth.uid()) or public.is_admin());

drop policy if exists profiles_update_own on public.profiles;
create policy profiles_update_own on public.profiles
    for update to authenticated
    using      (user_id = (select auth.uid()))
    with check (user_id = (select auth.uid()));

-- INSERT / DELETE ポリシーは作らない（作成はトリガー、削除は cascade のみ）

-- ── app_admins ──────────────────────────────────────────────────────────────
-- ポリシーを1つも作らない。RLS 有効かつポリシー0件なので、
-- 権限があったとしても1行も見えない。権限も①で剥がしてある。


-- ═══ ④ 列単位の GRANT（①の revoke の後に置くこと）═════════════════════════

-- ── products ────────────────────────────────────────────────────────────────
-- created_by と created_at は含めない＝誰がいつ登録したか分からない。
-- INSERT / UPDATE / DELETE は誰にも与えない（保存は save_candidate のみ）。
grant select (
    id, url_key, source_url, platform, name, maker, genre,
    raised_jpy, raised_usd, backers,
    priority, confidence, analysis, contact,
    schema_version, last_analyzed_at, updated_at
) on public.products to authenticated;

-- ── saved_items ─────────────────────────────────────────────────────────────
-- 更新できるのは利用者が操作する4列だけ。
-- user_id / product_id / saved_at / updated_at は含めない
-- （updated_at はトリガーが自動更新する）。
grant select on public.saved_items to authenticated;
grant update (memo, status, priority_override, archived)
    on public.saved_items to authenticated;
grant delete on public.saved_items to authenticated;
-- INSERT は与えない

-- ── profiles ────────────────────────────────────────────────────────────────
-- 本人が変更できるのは表示名だけ。email はトリガーが入れる。
grant select (user_id, email, display_name, updated_at)
    on public.profiles to authenticated;
grant update (display_name) on public.profiles to authenticated;
-- INSERT / DELETE は与えない

-- ── app_admins ──────────────────────────────────────────────────────────────
-- 権限を一切与えない。①の revoke all のまま。


-- ═══ ⑤ 関数の EXECUTE 権限 ═════════════════════════════════════════════════
-- REVOKE はすでに 0002_functions.sql の中で、各関数を作成した直後に
-- PUBLIC / anon / authenticated から行っている（0002単独の実行が終わった
-- 時点で、どの関数も外部から呼べない）。ここでは、アプリが実際に呼ぶ
-- 2つの関数だけを authenticated へ改めて GRANT する。
-- handle_new_user() と set_updated_at() は内部専用のため、ここでは
-- 一切 GRANT しない（トリガーとしてのみ動く）。
grant execute on function public.is_admin()                        to authenticated;
grant execute on function public.save_candidate(text, text, jsonb) to authenticated;

commit;
