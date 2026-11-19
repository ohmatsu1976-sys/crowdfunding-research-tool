-- =============================================================================
-- 0009_cases_rls_grants.sql  —  案件管理（第1段階「返信獲得」）: RLS・列単位GRANT
--
-- 適用順序: 0007 → 0008 → この0009。
--
-- 可視範囲:
--   cases         本人の行のみ（閲覧・一部列の更新・削除）。INSERTは一切許可しない
--                 （create_case_from_candidate() RPC経由のみ）。
--   case_events   本人の行のみ閲覧。INSERT/UPDATE/DELETEは一切許可しない
--                 （log_case_event / void_case_event / correct_case_event /
--                   redact_case_event / save_ai_assist_result のRPC経由のみ）。
--   case_targets  本人の行のみ（閲覧・登録・一部列の更新・削除）。単純な自己申告の
--                 数値のため、sender_profilesと同じ方針で直接CRUDを許可する。
--
-- Supabase は新規テーブルへ anon/authenticated の権限を既定で付与するため、
-- 列単位GRANTの前に必ず revoke all しておく（0003/0006と同じ順序）。
-- =============================================================================

begin;

-- ── revoke all（列単位GRANTより前に置くこと） ────────────────────────────────
revoke all on public.cases        from anon, authenticated;
revoke all on public.case_events  from anon, authenticated;
revoke all on public.case_targets from anon, authenticated;

alter table public.cases        enable row level security;
alter table public.case_events  enable row level security;
alter table public.case_targets enable row level security;


-- ── cases: 本人の行のみ。INSERTポリシーは作らない ────────────────────────────
drop policy if exists cases_select_own on public.cases;
create policy cases_select_own on public.cases
    for select to authenticated
    using (user_id = (select auth.uid()));

-- USING と WITH CHECK の両方を書く。USINGだけでは本人の行のuser_idを
-- 他人へ付け替えて渡せてしまう（saved_items_update_ownと同じ理由）。
drop policy if exists cases_update_own on public.cases;
create policy cases_update_own on public.cases
    for update to authenticated
    using      (user_id = (select auth.uid()))
    with check (user_id = (select auth.uid()));

drop policy if exists cases_delete_own on public.cases;
create policy cases_delete_own on public.cases
    for delete to authenticated
    using (user_id = (select auth.uid()));

-- SELECTは本人の行の全列（他人には見えないため非敏感）。
grant select on public.cases to authenticated;

-- UPDATEは状態機械に関わらない5列だけ。status/has_reply/first_sent_at/
-- last_reply_at/advanced_atはlog_case_event()経由のみ（直接UPDATE権限を与えない）。
grant update (
    memo, next_action_at, next_action_label, archived, primary_channel
) on public.cases to authenticated;

-- DELETEは許可する（候補リストのsaved_itemsと同じ考え方。見送りは通常archivedで
-- 十分だが、誤って作った案件そのものを消せるようにする）。
grant delete on public.cases to authenticated;


-- ── case_events: 本人の行をSELECTのみ。書き込みはRPC経由のみ ────────────────
drop policy if exists case_events_select_own on public.case_events;
create policy case_events_select_own on public.case_events
    for select to authenticated
    using (user_id = (select auth.uid()));

grant select on public.case_events to authenticated;
-- INSERT/UPDATE/DELETEは一切GRANTしない（save_candidate()と同じ考え方で、
-- 書き込みはlog_case_event/void_case_event/correct_case_event/
-- redact_case_event/save_ai_assist_resultのRPC以外に経路が無い状態にする）。


-- ── case_targets: 本人の行のみ。単純な自己申告データのため直接CRUDを許可 ─────
drop policy if exists case_targets_select_own on public.case_targets;
create policy case_targets_select_own on public.case_targets
    for select to authenticated
    using (user_id = (select auth.uid()));

drop policy if exists case_targets_insert_own on public.case_targets;
create policy case_targets_insert_own on public.case_targets
    for insert to authenticated
    with check (user_id = (select auth.uid()));

drop policy if exists case_targets_update_own on public.case_targets;
create policy case_targets_update_own on public.case_targets
    for update to authenticated
    using      (user_id = (select auth.uid()))
    with check (user_id = (select auth.uid()));

drop policy if exists case_targets_delete_own on public.case_targets;
create policy case_targets_delete_own on public.case_targets
    for delete to authenticated
    using (user_id = (select auth.uid()));

grant select on public.case_targets to authenticated;
grant insert (user_id, target_month, approach_target, reply_target)
    on public.case_targets to authenticated;
-- UPDATEはapproach_target/reply_targetだけ。user_id/target_month/updated_atは
-- 対象外（updated_atはトリガーが自動更新する。月をまたぐ変更はinsertで新しい
-- 行を作る、これにより過去月の目標が上書きされない）。
grant update (approach_target, reply_target) on public.case_targets to authenticated;
grant delete on public.case_targets to authenticated;


-- ── RPCの実行権限（0008で全ロールからREVOKEされているものを、利用者向けの
--    6関数だけ改めてGRANTする。内部専用の_case_apply_event/_case_recomputeは
--    一切GRANTしない）────────────────────────────────────────────────────────
grant execute on function public.create_case_from_candidate(uuid)
    to authenticated;
grant execute on function public.log_case_event(
    uuid, text, text, text, timestamptz, text, text, text)
    to authenticated;
grant execute on function public.void_case_event(uuid, text)
    to authenticated;
grant execute on function public.correct_case_event(uuid, text, text)
    to authenticated;
grant execute on function public.redact_case_event(uuid)
    to authenticated;
grant execute on function public.save_ai_assist_result(uuid, jsonb)
    to authenticated;

commit;
