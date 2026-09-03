-- =============================================================================
-- verify.sql  —  フェーズ3A: 適用結果の点検（統合1テーブル版）
--
-- Supabase SQL Editor は複数の SELECT のうち最後の結果しか表示しないため、
-- すべての検査を UNION ALL で1つの結果表にまとめている。
-- 1検査＝1行。列は check_name / expected / actual / ok / detail の5列。
--
-- 実行後、この1つの結果表の ok 列を上から下まで確認する。
-- すべて true であれば意図した権限構成になっている。1つでも false があれば、
-- そのまま先へ進まないこと（末尾の「すべてのチェックが成功」行でも一括確認できる）。
--
-- 権限の確認には has_table_privilege() / has_column_privilege() /
-- has_function_privilege() を使う。これらは指定したロール名の実際のACLを
-- 直接調べる関数で、SQL Editor を実行している接続ロール（postgres）が
-- anon/authenticated のメンバーかどうかには左右されない
-- （information_schema の role_*_grants 系ビューは接続ロールのメンバーシップに
--   見え方が左右されうるため、ここでは使わない）。
--
-- 秘密情報は出力しない。メールアドレス・user_id・トークンの実値は返さず、
-- 件数や true/false・列名の一覧だけを返す。
-- =============================================================================

with checks as (

    -- ── 1. テーブルの存在 ────────────────────────────────────────────────────
    select 10 as seq,
        'テーブルが4つとも存在する' as check_name,
        '4' as expected,
        (select count(*) from (values
            (to_regclass('public.products')), (to_regclass('public.saved_items')),
            (to_regclass('public.profiles')), (to_regclass('public.app_admins'))
        ) t(oid) where oid is not null)::text as actual,
        (select count(*) from (values
            (to_regclass('public.products')), (to_regclass('public.saved_items')),
            (to_regclass('public.profiles')), (to_regclass('public.app_admins'))
        ) t(oid) where oid is not null) = 4 as ok,
        null::text as detail

    -- ── 2. RLS 有効 ──────────────────────────────────────────────────────────
    union all
    select 20, '4テーブルすべてでRLSが有効', '4',
        (select count(*) from pg_class
          where oid = any(array[to_regclass('public.products'), to_regclass('public.saved_items'),
                                to_regclass('public.profiles'), to_regclass('public.app_admins')])
            and relrowsecurity)::text,
        (select count(*) from pg_class
          where oid = any(array[to_regclass('public.products'), to_regclass('public.saved_items'),
                                to_regclass('public.profiles'), to_regclass('public.app_admins')])
            and relrowsecurity) = 4,
        null

    -- ── 3〜6. ポリシー数 ─────────────────────────────────────────────────────
    union all
    select 30, 'products のポリシー数', '1',
        (select count(*) from pg_policies where schemaname='public' and tablename='products')::text,
        (select count(*) from pg_policies where schemaname='public' and tablename='products') = 1,
        null
    union all
    select 31, 'saved_items のポリシー数', '3',
        (select count(*) from pg_policies where schemaname='public' and tablename='saved_items')::text,
        (select count(*) from pg_policies where schemaname='public' and tablename='saved_items') = 3,
        null
    union all
    select 32, 'profiles のポリシー数', '2',
        (select count(*) from pg_policies where schemaname='public' and tablename='profiles')::text,
        (select count(*) from pg_policies where schemaname='public' and tablename='profiles') = 2,
        null
    union all
    select 33, 'app_admins のポリシー数', '0',
        (select count(*) from pg_policies where schemaname='public' and tablename='app_admins')::text,
        (select count(*) from pg_policies where schemaname='public' and tablename='app_admins') = 0,
        null

    -- ── 7. INSERTポリシー0件 ─────────────────────────────────────────────────
    union all
    select 40, 'INSERTポリシーが0件（4テーブル合計）', '0',
        (select count(*) from pg_policies
          where schemaname='public'
            and tablename in ('products','saved_items','profiles','app_admins')
            and cmd = 'INSERT')::text,
        (select count(*) from pg_policies
          where schemaname='public'
            and tablename in ('products','saved_items','profiles','app_admins')
            and cmd = 'INSERT') = 0,
        null

    -- ── 8. anon が4テーブルの権限を一切持たない ──────────────────────────────
    -- テーブル単位の SELECT/INSERT/UPDATE/DELETE を4テーブル×4種で確認する。
    -- anon には0003で列単位の権限も一切与えていないため、この4種で十分。
    union all
    select 50, 'anon が4テーブルのSELECT/INSERT/UPDATE/DELETEを一切持たない', '0',
        (select count(*) from (values
            (has_table_privilege('anon','public.products','SELECT')),
            (has_table_privilege('anon','public.products','INSERT')),
            (has_table_privilege('anon','public.products','UPDATE')),
            (has_table_privilege('anon','public.products','DELETE')),
            (has_table_privilege('anon','public.saved_items','SELECT')),
            (has_table_privilege('anon','public.saved_items','INSERT')),
            (has_table_privilege('anon','public.saved_items','UPDATE')),
            (has_table_privilege('anon','public.saved_items','DELETE')),
            (has_table_privilege('anon','public.profiles','SELECT')),
            (has_table_privilege('anon','public.profiles','INSERT')),
            (has_table_privilege('anon','public.profiles','UPDATE')),
            (has_table_privilege('anon','public.profiles','DELETE')),
            (has_table_privilege('anon','public.app_admins','SELECT')),
            (has_table_privilege('anon','public.app_admins','INSERT')),
            (has_table_privilege('anon','public.app_admins','UPDATE')),
            (has_table_privilege('anon','public.app_admins','DELETE'))
        ) t(v) where v)::text,
        (select count(*) from (values
            (has_table_privilege('anon','public.products','SELECT')),
            (has_table_privilege('anon','public.products','INSERT')),
            (has_table_privilege('anon','public.products','UPDATE')),
            (has_table_privilege('anon','public.products','DELETE')),
            (has_table_privilege('anon','public.saved_items','SELECT')),
            (has_table_privilege('anon','public.saved_items','INSERT')),
            (has_table_privilege('anon','public.saved_items','UPDATE')),
            (has_table_privilege('anon','public.saved_items','DELETE')),
            (has_table_privilege('anon','public.profiles','SELECT')),
            (has_table_privilege('anon','public.profiles','INSERT')),
            (has_table_privilege('anon','public.profiles','UPDATE')),
            (has_table_privilege('anon','public.profiles','DELETE')),
            (has_table_privilege('anon','public.app_admins','SELECT')),
            (has_table_privilege('anon','public.app_admins','INSERT')),
            (has_table_privilege('anon','public.app_admins','UPDATE')),
            (has_table_privilege('anon','public.app_admins','DELETE'))
        ) t(v) where v) = 0,
        null

    -- ── 9. authenticated が products/saved_items へ直接INSERTできない ───────
    union all
    select 51, 'authenticated が products/saved_items へ直接INSERTできない', '0',
        (select count(*) from (values
            (has_table_privilege('authenticated','public.products','INSERT')),
            (has_table_privilege('authenticated','public.saved_items','INSERT'))
        ) t(v) where v)::text,
        (select count(*) from (values
            (has_table_privilege('authenticated','public.products','INSERT')),
            (has_table_privilege('authenticated','public.saved_items','INSERT'))
        ) t(v) where v) = 0,
        null

    -- ── 10. products にテーブル全体SELECT権限がない ─────────────────────────
    union all
    select 52, 'products にテーブル全体SELECT権限がない', 'false',
        has_table_privilege('authenticated', 'public.products', 'SELECT')::text,
        not has_table_privilege('authenticated', 'public.products', 'SELECT'),
        null

    -- ── 11. products.created_by / created_at を authenticated が取得できない
    union all
    select 53, 'products の created_by/created_at をauthenticatedが取得できない', '0',
        (select count(*) from (values
            (has_column_privilege('authenticated','public.products','created_by','SELECT')),
            (has_column_privilege('authenticated','public.products','created_at','SELECT'))
        ) t(v) where v)::text,
        (select count(*) from (values
            (has_column_privilege('authenticated','public.products','created_by','SELECT')),
            (has_column_privilege('authenticated','public.products','created_at','SELECT'))
        ) t(v) where v) = 0,
        null

    -- ── 12. saved_items のUPDATE可能列は4つだけ ──────────────────────────────
    -- id を含む全列を検査し、UPDATE可能な列名だけを列挙する。
    -- 一覧が意図した4列とちょうど一致すれば「それだけ」であることの証拠になる。
    union all
    select 54, 'saved_items で更新できるのはmemo/status/priority_override/archivedだけ',
        'memo,status,priority_override,archived',
        concat_ws(',',
            case when has_column_privilege('authenticated','public.saved_items','memo','UPDATE')
                 then 'memo' end,
            case when has_column_privilege('authenticated','public.saved_items','status','UPDATE')
                 then 'status' end,
            case when has_column_privilege('authenticated','public.saved_items','priority_override','UPDATE')
                 then 'priority_override' end,
            case when has_column_privilege('authenticated','public.saved_items','archived','UPDATE')
                 then 'archived' end,
            case when has_column_privilege('authenticated','public.saved_items','id','UPDATE')
                 then 'id(想定外)' end,
            case when has_column_privilege('authenticated','public.saved_items','user_id','UPDATE')
                 then 'user_id(想定外)' end,
            case when has_column_privilege('authenticated','public.saved_items','product_id','UPDATE')
                 then 'product_id(想定外)' end,
            case when has_column_privilege('authenticated','public.saved_items','saved_at','UPDATE')
                 then 'saved_at(想定外)' end,
            case when has_column_privilege('authenticated','public.saved_items','updated_at','UPDATE')
                 then 'updated_at(想定外)' end
        ),
        concat_ws(',',
            case when has_column_privilege('authenticated','public.saved_items','memo','UPDATE')
                 then 'memo' end,
            case when has_column_privilege('authenticated','public.saved_items','status','UPDATE')
                 then 'status' end,
            case when has_column_privilege('authenticated','public.saved_items','priority_override','UPDATE')
                 then 'priority_override' end,
            case when has_column_privilege('authenticated','public.saved_items','archived','UPDATE')
                 then 'archived' end,
            case when has_column_privilege('authenticated','public.saved_items','id','UPDATE')
                 then 'id(想定外)' end,
            case when has_column_privilege('authenticated','public.saved_items','user_id','UPDATE')
                 then 'user_id(想定外)' end,
            case when has_column_privilege('authenticated','public.saved_items','product_id','UPDATE')
                 then 'product_id(想定外)' end,
            case when has_column_privilege('authenticated','public.saved_items','saved_at','UPDATE')
                 then 'saved_at(想定外)' end,
            case when has_column_privilege('authenticated','public.saved_items','updated_at','UPDATE')
                 then 'updated_at(想定外)' end
        ) = 'memo,status,priority_override,archived',
        null

    -- ── 13. saved_items の管理列を直接更新できない ───────────────────────────
    -- saved_items に created_at 列は無い（監査用の日時列は saved_at）ため、
    -- user_id / product_id / saved_at / updated_at の4列で確認する
    -- （12の「id(想定外)」等と重複するが、依頼の項目一覧に対応させるため
    --   独立した行としても残す）。
    union all
    select 55, 'saved_items のuser_id/product_id/saved_at/updated_atを直接更新できない', '0',
        (select count(*) from (values
            (has_column_privilege('authenticated','public.saved_items','user_id','UPDATE')),
            (has_column_privilege('authenticated','public.saved_items','product_id','UPDATE')),
            (has_column_privilege('authenticated','public.saved_items','saved_at','UPDATE')),
            (has_column_privilege('authenticated','public.saved_items','updated_at','UPDATE'))
        ) t(v) where v)::text,
        (select count(*) from (values
            (has_column_privilege('authenticated','public.saved_items','user_id','UPDATE')),
            (has_column_privilege('authenticated','public.saved_items','product_id','UPDATE')),
            (has_column_privilege('authenticated','public.saved_items','saved_at','UPDATE')),
            (has_column_privilege('authenticated','public.saved_items','updated_at','UPDATE'))
        ) t(v) where v) = 0,
        'saved_items に created_at 列は無いため saved_at で確認（依頼文の created_at は products の列）'

    -- ── 14. profiles のUPDATE可能列はdisplay_nameだけ ────────────────────────
    union all
    select 56, 'profiles で更新できるのはdisplay_nameだけ', 'display_name',
        concat_ws(',',
            case when has_column_privilege('authenticated','public.profiles','user_id','UPDATE')
                 then 'user_id(想定外)' end,
            case when has_column_privilege('authenticated','public.profiles','email','UPDATE')
                 then 'email(想定外)' end,
            case when has_column_privilege('authenticated','public.profiles','display_name','UPDATE')
                 then 'display_name' end,
            case when has_column_privilege('authenticated','public.profiles','updated_at','UPDATE')
                 then 'updated_at(想定外)' end
        ),
        concat_ws(',',
            case when has_column_privilege('authenticated','public.profiles','user_id','UPDATE')
                 then 'user_id(想定外)' end,
            case when has_column_privilege('authenticated','public.profiles','email','UPDATE')
                 then 'email(想定外)' end,
            case when has_column_privilege('authenticated','public.profiles','display_name','UPDATE')
                 then 'display_name' end,
            case when has_column_privilege('authenticated','public.profiles','updated_at','UPDATE')
                 then 'updated_at(想定外)' end
        ) = 'display_name',
        null

    -- ── 15. app_admins をanon/authenticatedが直接操作できない ────────────────
    union all
    select 57, 'app_admins をanon/authenticatedが直接操作できない', '0',
        (select count(*) from (values
            (has_table_privilege('anon','public.app_admins','SELECT')),
            (has_table_privilege('anon','public.app_admins','INSERT')),
            (has_table_privilege('anon','public.app_admins','UPDATE')),
            (has_table_privilege('anon','public.app_admins','DELETE')),
            (has_table_privilege('authenticated','public.app_admins','SELECT')),
            (has_table_privilege('authenticated','public.app_admins','INSERT')),
            (has_table_privilege('authenticated','public.app_admins','UPDATE')),
            (has_table_privilege('authenticated','public.app_admins','DELETE'))
        ) t(v) where v)::text,
        (select count(*) from (values
            (has_table_privilege('anon','public.app_admins','SELECT')),
            (has_table_privilege('anon','public.app_admins','INSERT')),
            (has_table_privilege('anon','public.app_admins','UPDATE')),
            (has_table_privilege('anon','public.app_admins','DELETE')),
            (has_table_privilege('authenticated','public.app_admins','SELECT')),
            (has_table_privilege('authenticated','public.app_admins','INSERT')),
            (has_table_privilege('authenticated','public.app_admins','UPDATE')),
            (has_table_privilege('authenticated','public.app_admins','DELETE'))
        ) t(v) where v) = 0,
        null

    -- ── 16. authenticatedが実行できるのはis_admin/save_candidateだけ ─────────
    -- has_function_privilege はPUBLICへの残存GRANTがあってもtrueを返す
    -- （どのロールもPUBLICの権限を暗黙に受け継ぐため）。したがって、この4関数
    -- すべてで正しい結果が出ていれば、PUBLICにも余計なEXECUTEが残っていない
    -- ことの間接証拠にもなる。
    union all
    select 60, 'authenticatedが実行できるのはis_admin/save_candidateだけ',
        'is_admin,save_candidate',
        concat_ws(',',
            case when has_function_privilege('authenticated','public.is_admin()','EXECUTE')
                 then 'is_admin' end,
            case when has_function_privilege('authenticated',
                      'public.save_candidate(text,text,jsonb)','EXECUTE')
                 then 'save_candidate' end,
            case when has_function_privilege('authenticated','public.handle_new_user()','EXECUTE')
                 then 'handle_new_user(想定外)' end,
            case when has_function_privilege('authenticated','public.set_updated_at()','EXECUTE')
                 then 'set_updated_at(想定外)' end
        ),
        concat_ws(',',
            case when has_function_privilege('authenticated','public.is_admin()','EXECUTE')
                 then 'is_admin' end,
            case when has_function_privilege('authenticated',
                      'public.save_candidate(text,text,jsonb)','EXECUTE')
                 then 'save_candidate' end,
            case when has_function_privilege('authenticated','public.handle_new_user()','EXECUTE')
                 then 'handle_new_user(想定外)' end,
            case when has_function_privilege('authenticated','public.set_updated_at()','EXECUTE')
                 then 'set_updated_at(想定外)' end
        ) = 'is_admin,save_candidate',
        null

    -- ── 17. handle_new_user/set_updated_atをauthenticatedが直接実行できない ─
    union all
    select 61, 'handle_new_user/set_updated_atをauthenticatedが実行できない', '0',
        (select count(*) from (values
            (has_function_privilege('authenticated','public.handle_new_user()','EXECUTE')),
            (has_function_privilege('authenticated','public.set_updated_at()','EXECUTE'))
        ) t(v) where v)::text,
        (select count(*) from (values
            (has_function_privilege('authenticated','public.handle_new_user()','EXECUTE')),
            (has_function_privilege('authenticated','public.set_updated_at()','EXECUTE'))
        ) t(v) where v) = 0,
        null

    -- ── 18. anonが4関数を実行できない ────────────────────────────────────────
    union all
    select 62, 'anonが4関数のいずれも実行できない', '0',
        (select count(*) from (values
            (has_function_privilege('anon','public.is_admin()','EXECUTE')),
            (has_function_privilege('anon','public.save_candidate(text,text,jsonb)','EXECUTE')),
            (has_function_privilege('anon','public.handle_new_user()','EXECUTE')),
            (has_function_privilege('anon','public.set_updated_at()','EXECUTE'))
        ) t(v) where v)::text,
        (select count(*) from (values
            (has_function_privilege('anon','public.is_admin()','EXECUTE')),
            (has_function_privilege('anon','public.save_candidate(text,text,jsonb)','EXECUTE')),
            (has_function_privilege('anon','public.handle_new_user()','EXECUTE')),
            (has_function_privilege('anon','public.set_updated_at()','EXECUTE'))
        ) t(v) where v) = 0,
        null

    -- ── 19. SECURITY DEFINER 設定 ────────────────────────────────────────────
    union all
    select 70, '3関数がSECURITY DEFINER（is_admin/save_candidate/handle_new_user）', '3',
        (select count(*) from pg_proc p join pg_namespace n on n.oid=p.pronamespace
          where n.nspname='public' and p.prosecdef
            and p.proname in ('is_admin','save_candidate','handle_new_user'))::text,
        (select count(*) from pg_proc p join pg_namespace n on n.oid=p.pronamespace
          where n.nspname='public' and p.prosecdef
            and p.proname in ('is_admin','save_candidate','handle_new_user')) = 3,
        null

    -- ── 20. 4関数すべてsearch_path固定 ───────────────────────────────────────
    union all
    select 71, '4関数すべてでsearch_pathが固定されている', '4',
        (select count(*) from pg_proc p join pg_namespace n on n.oid=p.pronamespace
          where n.nspname='public'
            and p.proname in ('is_admin','save_candidate','handle_new_user','set_updated_at')
            and exists (select 1 from unnest(coalesce(p.proconfig,'{}'::text[])) cfg
                         where cfg like 'search_path=%'))::text,
        (select count(*) from pg_proc p join pg_namespace n on n.oid=p.pronamespace
          where n.nspname='public'
            and p.proname in ('is_admin','save_candidate','handle_new_user','set_updated_at')
            and exists (select 1 from unnest(coalesce(p.proconfig,'{}'::text[])) cfg
                         where cfg like 'search_path=%')) = 4,
        null

    -- ── 21. トリガー4本 ──────────────────────────────────────────────────────
    union all
    select 80, 'トリガーが4本存在する', '4',
        (select count(*) from pg_trigger
          where not tgisinternal
            and tgname in ('on_auth_user_created','products_set_updated_at',
                           'saved_items_set_updated_at','profiles_set_updated_at'))::text,
        (select count(*) from pg_trigger
          where not tgisinternal
            and tgname in ('on_auth_user_created','products_set_updated_at',
                           'saved_items_set_updated_at','profiles_set_updated_at')) = 4,
        null

    -- ── 22. インデックス2本 ──────────────────────────────────────────────────
    union all
    select 81, 'saved_itemsのインデックスが2本存在する', '2',
        (select count(*) from pg_indexes
          where schemaname='public'
            and indexname in ('saved_items_user_list_idx','saved_items_product_user_idx'))::text,
        (select count(*) from pg_indexes
          where schemaname='public'
            and indexname in ('saved_items_user_list_idx','saved_items_product_user_idx')) = 2,
        null

    -- ── 23. 一意制約2件 ──────────────────────────────────────────────────────
    union all
    select 82, '一意制約が2件存在する（url_key / user_id+product_id）', '2',
        (select count(*) from pg_constraint
          where conname in ('products_url_key_unique','saved_items_user_product_unique'))::text,
        (select count(*) from pg_constraint
          where conname in ('products_url_key_unique','saved_items_user_product_unique')) = 2,
        null

    -- ── 24. profiles件数とauth.users件数が一致 ───────────────────────────────
    -- 件数だけを比較する。メールアドレスやuser_idの実値は出さない。
    union all
    select 90, 'profiles件数がauth.users件数と一致する',
        (select count(*)::text from auth.users),
        (select count(*)::text from public.profiles),
        (select count(*) from auth.users) = (select count(*) from public.profiles),
        null

    -- ── 25. app_adminsに管理者が1名以上登録されている ────────────────────────
    -- 件数だけを返す。管理者のuser_idは出さない。
    union all
    select 91, 'app_adminsに管理者が1名以上登録されている', '1以上',
        (select count(*)::text from public.app_admins),
        (select count(*) from public.app_admins) >= 1,
        null
)

-- ── 総合結果（すべての行のok列を一括確認する用）─────────────────────────────
-- 出力は check_name / expected / actual / ok / detail の5列。
-- 個別チェックは seq 順、「すべてのチェックが成功」の行は必ず最後に表示する
-- （seq 自体は最終結果には出さない。並び替えにだけ使う）。
select check_name, expected, actual, ok, detail
  from (
      select seq, check_name, expected, actual, ok, detail from checks
      union all
      select 999, 'すべてのチェックが成功', 'true',
             (select bool_and(ok) from checks)::text,
             (select bool_and(ok) from checks),
             '上記すべての行のokがtrueならtrue。1つでもfalseがあればfalse'
  ) all_rows
 order by seq;
