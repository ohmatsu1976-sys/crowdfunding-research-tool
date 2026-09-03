-- =============================================================================
-- verify.sql  —  フェーズ3A: 適用結果の点検
--
-- 0001〜0004 を適用したあとに SQL Editor で実行する。
-- 「ok」がすべて true になれば、意図した権限構成になっている。
-- 1つでも false があれば、そのまま先へ進まないこと。
--
-- 秘密情報は出力しない（user_id・メールアドレス・トークンは表示しない）。
-- =============================================================================

-- ── A. 総合チェック（ok がすべて true であること）───────────────────────────
select * from (

    -- RLS が有効か -----------------------------------------------------------
    select 1 as seq, 'RLS: products'     as check_name,
           (select relrowsecurity from pg_class
             where oid = 'public.products'::regclass) as ok
    union all
    select 2, 'RLS: saved_items',
           (select relrowsecurity from pg_class
             where oid = 'public.saved_items'::regclass)
    union all
    select 3, 'RLS: profiles',
           (select relrowsecurity from pg_class
             where oid = 'public.profiles'::regclass)
    union all
    select 4, 'RLS: app_admins',
           (select relrowsecurity from pg_class
             where oid = 'public.app_admins'::regclass)

    -- ポリシー数 -------------------------------------------------------------
    union all
    select 10, 'ポリシー数 products = 1',
           (select count(*) = 1 from pg_policies
             where schemaname = 'public' and tablename = 'products')
    union all
    select 11, 'ポリシー数 saved_items = 3',
           (select count(*) = 3 from pg_policies
             where schemaname = 'public' and tablename = 'saved_items')
    union all
    select 12, 'ポリシー数 profiles = 2',
           (select count(*) = 2 from pg_policies
             where schemaname = 'public' and tablename = 'profiles')
    union all
    select 13, 'ポリシー数 app_admins = 0',
           (select count(*) = 0 from pg_policies
             where schemaname = 'public' and tablename = 'app_admins')
    union all
    select 14, 'INSERTポリシーが1つも無い',
           (select count(*) = 0 from pg_policies
             where schemaname = 'public'
               and tablename in ('products','saved_items','profiles','app_admins')
               and cmd = 'INSERT')

    -- anon には何も無い ------------------------------------------------------
    union all
    select 20, 'anon: products に権限なし',
           not has_any_column_privilege('anon', 'public.products',
                                        'SELECT,INSERT,UPDATE,REFERENCES')
    union all
    select 21, 'anon: saved_items に権限なし',
           not has_any_column_privilege('anon', 'public.saved_items',
                                        'SELECT,INSERT,UPDATE,REFERENCES')
    union all
    select 22, 'anon: profiles に権限なし',
           not has_any_column_privilege('anon', 'public.profiles',
                                        'SELECT,INSERT,UPDATE,REFERENCES')
    union all
    select 23, 'anon: app_admins に権限なし',
           not has_any_column_privilege('anon', 'public.app_admins',
                                        'SELECT,INSERT,UPDATE,REFERENCES')
    union all
    select 24, 'anon: DELETE 権限なし',
           not has_table_privilege('anon', 'public.products',    'DELETE')
       and not has_table_privilege('anon', 'public.saved_items', 'DELETE')
       and not has_table_privilege('anon', 'public.profiles',    'DELETE')
       and not has_table_privilege('anon', 'public.app_admins',  'DELETE')

    -- app_admins は authenticated からも触れない -----------------------------
    union all
    select 30, 'authenticated: app_admins に権限なし',
           not has_any_column_privilege('authenticated', 'public.app_admins',
                                        'SELECT,INSERT,UPDATE,REFERENCES')
       and not has_table_privilege('authenticated', 'public.app_admins', 'DELETE')

    -- INSERT はどのテーブルにも与えていない -----------------------------------
    union all
    select 31, 'authenticated: INSERT 権限なし（4テーブル）',
           not has_any_column_privilege('authenticated', 'public.products',    'INSERT')
       and not has_any_column_privilege('authenticated', 'public.saved_items', 'INSERT')
       and not has_any_column_privilege('authenticated', 'public.profiles',    'INSERT')
       and not has_any_column_privilege('authenticated', 'public.app_admins',  'INSERT')

    -- products は列単位 SELECT のみ ------------------------------------------
    union all
    select 40, 'products: テーブル全体の SELECT は無い',
           not has_table_privilege('authenticated', 'public.products', 'SELECT')
    union all
    select 41, 'products: 公開列は読める',
           has_column_privilege('authenticated', 'public.products', 'id',      'SELECT')
       and has_column_privilege('authenticated', 'public.products', 'url_key', 'SELECT')
       and has_column_privilege('authenticated', 'public.products', 'analysis','SELECT')
    union all
    select 42, 'products: created_by / created_at は読めない',
           not has_column_privilege('authenticated', 'public.products', 'created_by', 'SELECT')
       and not has_column_privilege('authenticated', 'public.products', 'created_at', 'SELECT')
    union all
    select 43, 'products: UPDATE / DELETE 権限なし',
           not has_any_column_privilege('authenticated', 'public.products', 'UPDATE')
       and not has_table_privilege('authenticated', 'public.products', 'DELETE')

    -- saved_items の UPDATE 可能列 -------------------------------------------
    union all
    select 50, 'saved_items: 更新できるのは4列だけ',
           has_column_privilege('authenticated', 'public.saved_items', 'memo',              'UPDATE')
       and has_column_privilege('authenticated', 'public.saved_items', 'status',            'UPDATE')
       and has_column_privilege('authenticated', 'public.saved_items', 'priority_override', 'UPDATE')
       and has_column_privilege('authenticated', 'public.saved_items', 'archived',          'UPDATE')
    union all
    select 51, 'saved_items: user_id / product_id / saved_at / updated_at は更新できない',
           not has_column_privilege('authenticated', 'public.saved_items', 'user_id',    'UPDATE')
       and not has_column_privilege('authenticated', 'public.saved_items', 'product_id', 'UPDATE')
       and not has_column_privilege('authenticated', 'public.saved_items', 'saved_at',   'UPDATE')
       and not has_column_privilege('authenticated', 'public.saved_items', 'updated_at', 'UPDATE')
    union all
    select 52, 'saved_items: SELECT と DELETE はできる',
           has_table_privilege('authenticated', 'public.saved_items', 'SELECT')
       and has_table_privilege('authenticated', 'public.saved_items', 'DELETE')

    -- profiles ---------------------------------------------------------------
    union all
    select 60, 'profiles: 更新できるのは display_name だけ',
           has_column_privilege('authenticated', 'public.profiles', 'display_name', 'UPDATE')
       and not has_column_privilege('authenticated', 'public.profiles', 'email',   'UPDATE')
       and not has_column_privilege('authenticated', 'public.profiles', 'user_id', 'UPDATE')
    union all
    select 61, 'profiles: DELETE 権限なし',
           not has_table_privilege('authenticated', 'public.profiles', 'DELETE')

    -- 関数 -------------------------------------------------------------------
    union all
    select 70, '関数: SECURITY DEFINER が3つ（is_admin/save_candidate/handle_new_user）',
           (select count(*) = 3 from pg_proc p
             join pg_namespace n on n.oid = p.pronamespace
            where n.nspname = 'public' and p.prosecdef
              and p.proname in ('is_admin','save_candidate','handle_new_user'))
    union all
    select 71, '関数: 4関数すべてに search_path が固定されている',
           (select bool_and(exists (
                       select 1 from unnest(coalesce(p.proconfig, '{}'::text[])) cfg
                        where cfg like 'search_path=%'))
              from pg_proc p
              join pg_namespace n on n.oid = p.pronamespace
             where n.nspname = 'public'
               and p.proname in ('is_admin','save_candidate',
                                 'handle_new_user','set_updated_at'))
    union all
    select 72, '関数: オーバーロードが無い（1関数1シグネチャ）',
           (select bool_and(c = 1) from (
                select count(*) as c from pg_proc p
                  join pg_namespace n on n.oid = p.pronamespace
                 where n.nspname = 'public'
                   and p.proname in ('is_admin','save_candidate',
                                     'handle_new_user','set_updated_at')
                 group by p.proname) t)
    union all
    select 73, '関数: anon / public は EXECUTE できない',
           not has_function_privilege('anon', 'public.is_admin()', 'EXECUTE')
       and not has_function_privilege('anon',
                 'public.save_candidate(text,text,jsonb)', 'EXECUTE')
    union all
    select 74, '関数: authenticated は必要な2つだけ EXECUTE できる',
           has_function_privilege('authenticated', 'public.is_admin()', 'EXECUTE')
       and has_function_privilege('authenticated',
                 'public.save_candidate(text,text,jsonb)', 'EXECUTE')
       and not has_function_privilege('authenticated',
                 'public.handle_new_user()', 'EXECUTE')
       and not has_function_privilege('authenticated',
                 'public.set_updated_at()', 'EXECUTE')

    -- トリガー・インデックス・制約 -------------------------------------------
    union all
    select 80, 'トリガー: profiles 自動作成',
           (select count(*) = 1 from pg_trigger
             where tgname = 'on_auth_user_created' and not tgisinternal)
    union all
    select 81, 'トリガー: updated_at 自動更新が3つ',
           (select count(*) = 3 from pg_trigger
             where tgname in ('products_set_updated_at',
                              'saved_items_set_updated_at',
                              'profiles_set_updated_at')
               and not tgisinternal)
    union all
    select 82, 'インデックス: saved_items の2本',
           (select count(*) = 2 from pg_indexes
             where schemaname = 'public'
               and indexname in ('saved_items_user_list_idx',
                                 'saved_items_product_user_idx'))
    union all
    select 83, '制約: 一意制約（url_key / user_id+product_id）',
           (select count(*) = 2 from pg_constraint
             where conname in ('products_url_key_unique',
                               'saved_items_user_product_unique'))
    union all
    select 84, '制約: ステータスは8値',
           (select count(*) = 1 from pg_constraint
             where conname = 'saved_items_status_ok')

    -- データ -----------------------------------------------------------------
    union all
    select 90, 'profiles: 既存利用者ぶんが登録されている',
           (select count(*) from auth.users) = (select count(*) from public.profiles)

) checks
order by seq;


-- ── B. ポリシー一覧（目視確認用）────────────────────────────────────────────
select tablename, policyname, cmd, roles::text,
       qual        as using_expr,
       with_check  as with_check_expr
  from pg_policies
 where schemaname = 'public'
   and tablename in ('products', 'saved_items', 'profiles', 'app_admins')
 order by tablename, cmd, policyname;


-- ── C. 列単位権限の一覧（目視確認用）────────────────────────────────────────
select table_name, grantee, privilege_type,
       string_agg(column_name, ', ' order by column_name) as columns
  from information_schema.column_privileges
 where table_schema = 'public'
   and table_name in ('products', 'saved_items', 'profiles', 'app_admins')
   and grantee in ('anon', 'authenticated')
 group by table_name, grantee, privilege_type
 order by table_name, grantee, privilege_type;


-- ── D. 関数の設定（目視確認用）──────────────────────────────────────────────
-- security definer と search_path の実際の値を確認する。
-- search_path は空文字に固定していること（search_path="" と表示される）。
select p.proname                                as function_name,
       p.prosecdef                              as security_definer,
       coalesce(p.proconfig, '{}'::text[])::text as config,
       pg_get_function_identity_arguments(p.oid) as arguments
  from pg_proc p
  join pg_namespace n on n.oid = p.pronamespace
 where n.nspname = 'public'
   and p.proname in ('is_admin', 'save_candidate',
                     'handle_new_user', 'set_updated_at')
 order by p.proname;
