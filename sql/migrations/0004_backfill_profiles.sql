-- =============================================================================
-- 0004_backfill_profiles.sql  —  フェーズ3A: 既存利用者の profiles 初期登録
--
-- 0002 のトリガーは「これから作られる利用者」にしか効かない。
-- すでに存在する利用者（管理者を含む）の profiles をここで作る。
--
-- 何度実行しても安全（冪等）。環境を作り直したときも同じ手順で再現できる。
-- service_role は使わない。SQL Editor（postgres ロール）で実行する。
-- =============================================================================

begin;

insert into public.profiles (user_id, email)
select u.id,
       left(coalesce(nullif(u.email, ''), 'unknown-' || u.id::text), 320)
  from auth.users u
on conflict (user_id) do update
    set email      = excluded.email,
        updated_at = now();

commit;

-- ── 確認 ────────────────────────────────────────────────────────────────────
-- 件数だけを見る。メールアドレスの実値は出さない。
-- users と profiles が同数になっていれば成功。
select (select count(*) from auth.users)      as users,
       (select count(*) from public.profiles) as profiles,
       (select count(*) from auth.users)
       = (select count(*) from public.profiles) as ok;
