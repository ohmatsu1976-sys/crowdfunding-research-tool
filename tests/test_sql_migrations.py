# -*- coding: utf-8 -*-
"""SQLマイグレーションの静的・決定論テスト

実行: python tests/test_sql_migrations.py

本物のPostgreSQL/Supabaseには接続しない。ファイルのテキストを解析して、
「危険な形にならないこと」を機械的に確認する。RLSが実際に効いているかは
本物のSupabaseでしか確認できないため、それはフェーズ3Eの実データ分離テスト
（12項目）で別途行う。ここでの確認は次の3種類:

  1. ファイルが壊れていないか（空でない・途中で切れていない・構文が対になっている）
  2. 権限遮断が「関数作成→その場でREVOKE」の順で書かれているか
     （0002単独の実行が終わった時点で、外部から関数を呼べないことの静的証拠）
  3. 対象ドメインの一覧が product_key.py（アプリ側）とSQL側で一致しているか
"""

import re
import sys
from pathlib import Path

from _harness import run  # noqa: E402
import product_key as pk  # noqa: E402

SQL_DIR = Path(__file__).resolve().parent.parent / "sql"
MIGRATIONS_DIR = SQL_DIR / "migrations"

MIGRATION_FILES = [
    "0001_tables.sql",
    "0002_functions.sql",
    "0003_rls_grants.sql",
    "0004_backfill_profiles.sql",
    "0006_sender_profiles.sql",
]
EXAMPLE_FILES = ["0005_admin.sql.example"]
ALL_SQL_FILES = (
    [MIGRATIONS_DIR / n for n in MIGRATION_FILES]
    + [MIGRATIONS_DIR / n for n in EXAMPLE_FILES]
    + [SQL_DIR / "verify.sql"]
)

FUNCTIONS_WITH_SIGNATURE = {
    "is_admin": "public.is_admin()",
    "save_candidate": "public.save_candidate(text, text, jsonb)",
    "handle_new_user": "public.handle_new_user()",
    "set_updated_at": "public.set_updated_at()",
}
# 最終的に authenticated へ EXECUTE を許可してよい関数だけ
PUBLIC_FACING_FUNCTIONS = {"is_admin", "save_candidate"}
INTERNAL_ONLY_FUNCTIONS = {"handle_new_user", "set_updated_at"}


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _strip_sql_comments(text: str) -> str:
    """-- コメントを取り除く（文字列リテラルの中の "--" は考慮しない簡易版）

    このテストが探しているキーワード（revoke/grant/create function 等）は
    コメント文中の説明にも同じ単語が出てくるため、誤検出を避けるために使う。
    """
    return "\n".join(re.sub(r"--.*$", "", line) for line in text.splitlines())


# ── 1. ファイルの健全性 ────────────────────────────────────────────────────────

def test_all_migration_files_exist():
    for name in MIGRATION_FILES + EXAMPLE_FILES:
        path = MIGRATIONS_DIR / name
        assert path.is_file(), f"{name} が無い"


def test_verify_and_readme_exist():
    assert (SQL_DIR / "verify.sql").is_file()
    assert (SQL_DIR / "README.md").is_file()


def test_no_sql_file_is_empty_or_truncated():
    """空ファイル・書きかけで切れたファイルが無いこと

    最後の非空行がセミコロンで終わっていることを確認する
    （API中断のような途中終了があれば、ここで検出できる）。
    """
    for path in ALL_SQL_FILES:
        text = _read(path)
        assert text.strip(), f"{path.name} が空"
        lines = [l for l in text.splitlines() if l.strip()]
        last = lines[-1].rstrip()
        assert last.endswith(";") or last.endswith("*/"), (
            f"{path.name} の最後の行がセミコロンで終わっていない: {last!r}"
        )


def test_migrations_are_wrapped_in_transactions():
    """0001〜0004 は begin; と commit; の対になっている（数が一致し1組以上）"""
    for name in MIGRATION_FILES:
        text = _read(MIGRATIONS_DIR / name).lower()
        begins = len(re.findall(r"\bbegin\s*;", text))
        commits = len(re.findall(r"\bcommit\s*;", text))
        assert begins >= 1, f"{name} に begin; が無い"
        assert begins == commits, f"{name} の begin/commit 数が不一致: {begins} vs {commits}"


def test_dollar_quoting_is_balanced():
    """$$ ... $$ の数が偶数（関数本体が閉じている）"""
    for path in ALL_SQL_FILES:
        text = _read(path)
        count = text.count("$$")
        assert count % 2 == 0, f"{path.name} の $$ が対になっていない（{count}個）"


def test_parentheses_are_balanced_per_file():
    """丸括弧の対応が取れている（文字列・コメントを大まかに除いた簡易チェック）"""
    for path in ALL_SQL_FILES:
        text = _strip_sql_comments(_read(path))
        # ドル引用の本体は SQL 構文ではなく plpgsql 本体なので対象から外す
        text = re.sub(r"\$\$.*?\$\$", "", text, flags=re.S)
        assert text.count("(") == text.count(")"), f"{path.name} の括弧が不一致"


# ── 2. 秘密情報の非混入 ─────────────────────────────────────────────────────────

_UUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)
_JWT_RE = re.compile(r"eyJ[A-Za-z0-9_-]{10,}")


def test_no_real_uuid_or_jwt_in_sql_files():
    """実在しそうなUUID・JWTらしき文字列が含まれていない"""
    for path in ALL_SQL_FILES:
        text = _read(path)
        assert not _UUID_RE.search(text), f"{path.name} にUUIDらしき文字列がある"
        assert not _JWT_RE.search(text), f"{path.name} にJWTらしき文字列がある"


def test_admin_example_uses_placeholder_not_real_value():
    text = _read(MIGRATIONS_DIR / "0005_admin.sql.example")
    assert "<ここに管理者の user_id>" in text
    assert not _UUID_RE.search(text)


def test_no_service_role_or_secret_key_mentioned():
    for path in ALL_SQL_FILES:
        upper = _read(path).upper()
        assert "SERVICE_ROLE_KEY" not in upper
        assert "SB_SECRET" not in upper


# ── 3. 権限遮断：関数作成の直後にREVOKEがあること ────────────────────────────────

def test_0002_revokes_execute_immediately_after_each_function():
    """各SECURITY DEFINER/トリガー関数を作った直後、同じファイル内でREVOKEしている

    0003を待たずに0002単独の実行が終わった時点で、PUBLIC・anon・authenticated
    のいずれからも関数を呼べないことの静的な証拠。
    「作成 → コメント（あれば）→ REVOKE(public) → REVOKE(anon) → REVOKE(authenticated)」
    の順で、次の関数定義が始まるより前に現れることを確認する。
    """
    text = _strip_sql_comments(_read(MIGRATIONS_DIR / "0002_functions.sql"))

    # ファイル内の関数定義の出現位置
    positions = {}
    for name, sig in FUNCTIONS_WITH_SIGNATURE.items():
        m = re.search(
            r"create\s+or\s+replace\s+function\s+public\." + re.escape(name) + r"\s*\(",
            text, re.I)
        assert m, f"{name} の create or replace function が見つからない"
        positions[name] = m.start()

    ordered = sorted(positions.items(), key=lambda kv: kv[1])

    for i, (name, start) in enumerate(ordered):
        end = ordered[i + 1][1] if i + 1 < len(ordered) else len(text)
        segment = text[start:end]
        sig = FUNCTIONS_WITH_SIGNATURE[name]
        sig_pat = re.escape(sig)

        for role in ("public", "anon", "authenticated"):
            pattern = (r"revoke\s+execute\s+on\s+function\s+" + sig_pat
                      + r"\s+from\s+" + role + r"\s*;")
            assert re.search(pattern, segment, re.I), (
                f"{name}: 作成直後のブロック内に "
                f"'revoke execute ... from {role}' が見つからない"
            )


def test_0002_revoke_from_authenticated_appears_before_0003_would_grant():
    """0002が authenticated からも一度 REVOKE していること（0003任せにしない）"""
    text = _strip_sql_comments(_read(MIGRATIONS_DIR / "0002_functions.sql"))
    for name, sig in FUNCTIONS_WITH_SIGNATURE.items():
        pattern = (r"revoke\s+execute\s+on\s+function\s+" + re.escape(sig)
                  + r"\s+from\s+authenticated\s*;")
        assert re.search(pattern, text, re.I), (
            f"{name}: authenticated からのREVOKEが0002に無い"
        )


def test_0003_grants_only_the_two_public_facing_functions():
    """0003がauthenticatedへGRANTするのは is_admin と save_candidate だけ"""
    text = _strip_sql_comments(_read(MIGRATIONS_DIR / "0003_rls_grants.sql"))
    grants = re.findall(
        r"grant\s+execute\s+on\s+function\s+public\.(\w+)\([^)]*\)\s+to\s+authenticated",
        text, re.I)
    assert set(grants) == PUBLIC_FACING_FUNCTIONS, (
        f"0003のGRANT対象が想定と異なる: {set(grants)}"
    )


def test_0003_does_not_grant_internal_functions():
    text = _strip_sql_comments(_read(MIGRATIONS_DIR / "0003_rls_grants.sql")).lower()
    for name in INTERNAL_ONLY_FUNCTIONS:
        assert f"grant execute on function public.{name.lower()}" not in text, (
            f"0003が内部関数 {name} をGRANTしている"
        )


def test_0003_does_not_re_revoke_execute():
    """REVOKEは0002だけで完結し、0003で重複させない（役割を分ける）"""
    text = _strip_sql_comments(_read(MIGRATIONS_DIR / "0003_rls_grants.sql")).lower()
    assert "revoke execute" not in text, (
        "0003にEXECUTEのREVOKEが残っている。REVOKEは0002だけで行う設計"
    )


def test_no_function_grants_execute_to_public_or_anon():
    """全ファイルを通じて、EXECUTEをPUBLICやanonへGRANTしている箇所が無い"""
    for path in MIGRATIONS_DIR.glob("*.sql"):
        text = _strip_sql_comments(_read(path)).lower()
        assert not re.search(r"grant\s+execute\s+on\s+function\s+\S+\s+to\s+(public|anon)\b",
                             text), f"{path.name} が EXECUTE を public/anon へ許可している"


# ── 4. SECURITY DEFINER の約束 ──────────────────────────────────────────────────

def test_all_definer_functions_set_empty_search_path():
    text = _read(MIGRATIONS_DIR / "0002_functions.sql")
    for name in ("is_admin", "save_candidate", "handle_new_user"):
        m = re.search(
            r"create\s+or\s+replace\s+function\s+public\." + name
            + r".*?security\s+definer\s+set\s+search_path\s*=\s*''",
            text, re.I | re.S)
        assert m, f"{name} が SECURITY DEFINER + search_path='' になっていない"


def test_save_candidate_rejects_null_uid():
    text = _read(MIGRATIONS_DIR / "0002_functions.sql")
    assert "v_uid is null" in text
    assert "not authenticated" in text


def test_save_candidate_does_not_accept_user_id_argument():
    """save_candidate の引数に user_id が無い（保存者は auth.uid() でのみ決まる）"""
    text = _strip_sql_comments(_read(MIGRATIONS_DIR / "0002_functions.sql"))
    m = re.search(r"create\s+or\s+replace\s+function\s+public\.save_candidate\s*\((.*?)\)\s*\n?returns",
                  text, re.I | re.S)
    assert m, "save_candidate のシグネチャが見つからない"
    args = m.group(1).lower()
    assert "user_id" not in args
    assert "p_user" not in args


def test_no_function_overloads():
    """1関数につき1シグネチャ（オーバーロードを作らない）"""
    text = _strip_sql_comments(_read(MIGRATIONS_DIR / "0002_functions.sql"))
    for name in FUNCTIONS_WITH_SIGNATURE:
        count = len(re.findall(
            r"create\s+or\s+replace\s+function\s+public\." + name + r"\s*\(",
            text, re.I))
        assert count == 1, f"{name} が複数回定義されている（オーバーロード）"


def test_app_admins_gets_no_policy_and_no_grant():
    """app_admins にはポリシーも権限も一切与えない（クライアントから完全に遮断）"""
    rls_text = _strip_sql_comments(_read(MIGRATIONS_DIR / "0003_rls_grants.sql")).lower()
    assert not re.search(r"create policy[^;]*app_admins[^;]*;", rls_text, re.S), (
        "app_admins にポリシーが作られている"
    )
    assert not re.search(r"grant\s+\w+.*on\s+public\.app_admins\s+to\s+(anon|authenticated)",
                         rls_text), "app_admins に権限がGRANTされている"


def test_saved_items_update_grant_excludes_ownership_columns():
    """saved_items のUPDATE可能列に user_id / product_id / saved_at / updated_at が無い"""
    text = _strip_sql_comments(_read(MIGRATIONS_DIR / "0003_rls_grants.sql"))
    m = re.search(
        r"grant\s+update\s*\(([^)]*)\)\s*\n?\s*on\s+public\.saved_items", text, re.I)
    assert m, "saved_items のGRANT UPDATEが見つからない"
    columns = [c.strip() for c in m.group(1).split(",")]
    assert set(columns) == {"memo", "status", "priority_override", "archived"}, columns


def test_profiles_update_grant_is_display_name_only():
    text = _strip_sql_comments(_read(MIGRATIONS_DIR / "0003_rls_grants.sql"))
    m = re.search(
        r"grant\s+update\s*\(([^)]*)\)\s*on\s+public\.profiles", text, re.I)
    assert m, "profiles のGRANT UPDATEが見つからない"
    columns = [c.strip() for c in m.group(1).split(",")]
    assert columns == ["display_name"], columns


def test_products_select_grant_excludes_created_by_and_created_at():
    text = _strip_sql_comments(_read(MIGRATIONS_DIR / "0003_rls_grants.sql"))
    m = re.search(
        r"grant\s+select\s*\(([^)]*)\)\s*\n?\s*on\s+public\.products", text, re.I | re.S)
    assert m, "products のGRANT SELECTが見つからない"
    columns = {c.strip() for c in m.group(1).replace("\n", " ").split(",")}
    assert "created_by" not in columns
    assert "created_at" not in columns
    assert "analysis" in columns and "url_key" in columns


def test_no_insert_grant_or_policy_on_products_or_saved_items():
    """保存はsave_candidate経由のみ。INSERT権限・INSERTポリシーを一切与えない"""
    grants_text = _strip_sql_comments(_read(MIGRATIONS_DIR / "0003_rls_grants.sql")).lower()
    for table in ("products", "saved_items", "profiles", "app_admins"):
        assert not re.search(r"grant\s+insert.*on\s+public\." + table, grants_text), (
            f"{table} にINSERT権限がGRANTされている"
        )
        assert not re.search(r"for\s+insert.*public\." + table, grants_text, re.S), (
            f"{table} にINSERTポリシーがある"
        )


def test_revoke_all_happens_before_column_grants():
    """revoke all が column-level grant より前に書かれている（順序がテストの主眼）"""
    text = _strip_sql_comments(_read(MIGRATIONS_DIR / "0003_rls_grants.sql")).lower()
    revoke_pos = text.find("revoke all")
    grant_pos = text.find("grant select (")
    assert revoke_pos != -1 and grant_pos != -1
    assert revoke_pos < grant_pos, "revoke all より前に列GRANTが書かれている（順序ミス）"


# ── 5. ドメイン検証：アプリとDBの一致 ────────────────────────────────────────────

def test_sql_domain_allowlist_matches_product_key():
    """save_candidate() のドメイン一覧が product_key.ALLOWED_HOSTS と一致する"""
    text = _read(MIGRATIONS_DIR / "0002_functions.sql")
    m = re.search(r"v_host\s+not\s+in\s*\(([^)]+)\)", text, re.I)
    assert m, "ドメイン許可リストが見つからない"
    sql_hosts = {h.strip().strip("'") for h in m.group(1).split(",")}
    assert sql_hosts == set(pk.ALLOWED_HOSTS), (
        f"SQLとアプリでドメイン一覧が食い違っている: SQL={sql_hosts} / "
        f"product_key.py={set(pk.ALLOWED_HOSTS)}"
    )


def test_sql_uses_hostname_extraction_not_substring_match():
    """ドメイン判定が部分一致（LIKE等）ではなく、ホスト名の切り出し＋完全一致であること"""
    text = _strip_sql_comments(_read(MIGRATIONS_DIR / "0002_functions.sql")).lower()
    assert "substring(v_key from" in text, (
        "ホスト名を切り出すsubstring(...)が見つからない"
    )
    assert " not in (" in text, "完全一致（IN句）でのホスト比較が見つからない"
    # ドメインチェックの周辺で LIKE/ILIKE によるワイルドカード一致を使っていないこと
    assert "ilike '%" not in text
    assert "like '%" not in text


def test_sql_zeczec_domain_is_present():
    text = _read(MIGRATIONS_DIR / "0002_functions.sql")
    assert "zeczec.com" in text


def test_readme_documents_three_platforms():
    text = _read(SQL_DIR / "README.md")
    for domain in ("kickstarter.com", "indiegogo.com", "zeczec.com"):
        assert domain in text, f"README に {domain} の記載が無い"


def test_readme_states_0002_alone_blocks_external_calls():
    """READMEが「0002と0003の間は呼べる」という古い説明のままになっていない"""
    text = _read(SQL_DIR / "README.md")
    assert "0002単独の実行後でも外部から関数を呼べない" in text or \
           "0002単独の実行が完了した時点" in text, (
        "READMEが0002単独での権限遮断について更新されていない"
    )
    assert "流すまでは" not in text, (
        "古い説明（0003を流すまでanonから呼べる）がREADMEに残っている"
    )


# ── 6. verify.sql 自体の健全性 ──────────────────────────────────────────────────

def test_verify_sql_checks_all_four_tables_rls():
    text = _read(SQL_DIR / "verify.sql")
    for table in ("products", "saved_items", "profiles", "app_admins"):
        assert table in text


def test_verify_sql_checks_public_facing_functions_execute():
    text = _read(SQL_DIR / "verify.sql")
    assert "is_admin" in text and "save_candidate" in text


def test_verify_sql_returns_a_single_unified_result_set():
    """複数のSELECTではなく、UNION ALLで1つの結果表にまとめている

    Supabase SQL Editor は複数の SELECT のうち最後の結果しか表示しないため、
    個別の SELECT 文を並べる形（前回の設計）に戻さないことを保証する。
    """
    text = _strip_sql_comments(_read(SQL_DIR / "verify.sql"))
    # トップレベルの文はセミコロンで区切られる。空文でない文が複数あれば
    # 「複数の独立したSELECT結果」に戻ってしまっている。
    statements = [s.strip() for s in text.split(";") if s.strip()]
    assert len(statements) == 1, (
        f"verify.sql の実行文が複数ある（{len(statements)}件）。"
        "1つの統合結果だけを返す設計に反する"
    )
    assert text.lower().count("union all") >= 24, "検査がUNION ALLで統合されていない"


def test_verify_sql_output_columns_are_check_name_expected_actual_ok():
    """最終結果の列が check_name / expected / actual / ok / detail であること"""
    text = _strip_sql_comments(_read(SQL_DIR / "verify.sql"))
    # 最後の（最も外側の）select リストを確認する
    m = re.search(
        r"select\s+check_name\s*,\s*expected\s*,\s*actual\s*,\s*ok\s*,\s*detail\b",
        text, re.I)
    assert m, "最終結果の列が check_name/expected/actual/ok/detail になっていない"


def test_verify_sql_has_summary_row():
    """全体の合否を1行で確認できる集約行がある"""
    text = _read(SQL_DIR / "verify.sql")
    assert "すべてのチェックが成功" in text
    assert "bool_and(ok)" in text


def test_verify_sql_covers_all_25_requested_checks():
    """依頼された25項目に対応する検査がそれぞれ含まれている"""
    text = _read(SQL_DIR / "verify.sql")
    required_fragments = [
        "テーブルが4つとも存在する",
        "RLSが有効",
        "products のポリシー数",
        "saved_items のポリシー数",
        "profiles のポリシー数",
        "app_admins のポリシー数",
        "INSERTポリシーが0件",
        "anon が4テーブル",
        "authenticated が products/saved_items へ直接INSERTできない",
        "products にテーブル全体SELECT権限がない",
        "created_by/created_at",
        "saved_items で更新できるのは",
        "saved_items のuser_id/product_id/saved_at/updated_atを直接更新できない",
        "profiles で更新できるのは",
        "app_admins をanon/authenticatedが直接操作できない",
        "authenticatedが実行できるのはis_admin/save_candidateだけ",
        "handle_new_user/set_updated_atをauthenticatedが実行できない",
        "anonが4関数のいずれも実行できない",
        "SECURITY DEFINER",
        "search_pathが固定",
        "トリガーが4本",
        "インデックスが2本",
        "一意制約が2件",
        "profiles件数がauth.users件数と一致する",
        "app_adminsに管理者が1名以上登録されている",
    ]
    for fragment in required_fragments:
        assert fragment in text, f"必須検査が見つからない: {fragment!r}"


def test_verify_sql_does_not_output_secret_looking_values():
    """検査結果に実値（メール・UUID・JWT）を出す列選択が無い

    すべて count(*) や真偽値・列名の一覧だけを返し、email や user_id の
    値そのものを select していないことを確認する。
    """
    text = _strip_sql_comments(_read(SQL_DIR / "verify.sql")).lower()
    assert "select email" not in text
    assert "select user_id" not in text
    assert not re.search(r"select\s+[\w.]*\bemail\b", text)
    assert not _UUID_RE.search(_read(SQL_DIR / "verify.sql"))
    assert not _JWT_RE.search(_read(SQL_DIR / "verify.sql"))


def test_verify_sql_uses_privilege_functions_not_role_scoped_views():
    """role_table_grants / role_routine_grants など、接続ロールの
    メンバーシップに見え方が左右されうるビューに依存していない"""
    text = _strip_sql_comments(_read(SQL_DIR / "verify.sql")).lower()
    assert "role_table_grants" not in text
    assert "role_routine_grants" not in text
    assert "column_privileges" not in text or "has_column_privilege" in text


# ── 7. sender_profiles（送信者プロフィール・0006）───────────────────────────────

def test_sender_profiles_table_has_rls_enabled():
    text = _strip_sql_comments(_read(MIGRATIONS_DIR / "0006_sender_profiles.sql")).lower()
    assert re.search(r"alter\s+table\s+public\.sender_profiles\s+enable\s+row\s+level\s+security",
                     text), "sender_profilesでRLSが有効化されていない"


def test_sender_profiles_revokes_all_before_column_grants():
    """0001〜0005と同じ順序: revoke all → 列単位のGRANT"""
    text = _strip_sql_comments(_read(MIGRATIONS_DIR / "0006_sender_profiles.sql")).lower()
    revoke_pos = text.find("revoke all")
    grant_pos = text.find("grant select (")
    assert revoke_pos != -1 and grant_pos != -1
    assert revoke_pos < grant_pos, "revoke all より前に列GRANTが書かれている（順序ミス）"


def test_sender_profiles_has_four_own_row_policies():
    """select/insert/update/delete の4ポリシーがすべてuser_id=auth.uid()で絞られている"""
    text = _strip_sql_comments(_read(MIGRATIONS_DIR / "0006_sender_profiles.sql"))
    policies = re.findall(r"create\s+policy\s+(\w+)\s+on\s+public\.sender_profiles", text, re.I)
    assert len(policies) == 4, f"ポリシー数が4ではない: {policies}"
    assert text.lower().count("auth.uid()") >= 4, "auth.uid()で絞っている箇所が少ない"


def test_sender_profiles_insert_policy_checks_auth_uid():
    """他人のuser_idで登録できないよう、INSERTにWITH CHECKがある"""
    text = _strip_sql_comments(_read(MIGRATIONS_DIR / "0006_sender_profiles.sql"))
    m = re.search(
        r"create\s+policy\s+sender_profiles_insert_own\s+on\s+public\.sender_profiles"
        r"\s+for\s+insert\s+to\s+authenticated\s+with\s+check\s*\(([^)]*auth\.uid\(\)[^)]*)\)",
        text, re.I | re.S)
    assert m, "sender_profiles_insert_ownにauth.uid()とのWITH CHECKが見つからない"


def test_sender_profiles_update_grant_excludes_ownership_columns():
    """UPDATE可能列にuser_id/id/created_at/updated_atが無い（saved_itemsと同じ発想）"""
    text = _strip_sql_comments(_read(MIGRATIONS_DIR / "0006_sender_profiles.sql"))
    m = re.search(
        r"grant\s+update\s*\(([^)]*)\)\s*\n?\s*on\s+public\.sender_profiles", text, re.I)
    assert m, "sender_profilesのGRANT UPDATEが見つからない"
    columns = {c.strip() for c in m.group(1).split(",")}
    assert columns == {"sender_name_en", "company_name_en", "is_default", "last_used_at"}, columns


def test_sender_profiles_has_unique_constraint_on_name_and_company():
    """同一利用者が氏名+会社名の完全一致を重複登録できない一意制約がある"""
    text = _strip_sql_comments(_read(MIGRATIONS_DIR / "0006_sender_profiles.sql"))
    assert re.search(
        r"constraint\s+sender_profiles_unique_per_user\s+unique\s*\("
        r"\s*user_id\s*,\s*sender_name_en\s*,\s*company_name_en\s*\)",
        text, re.I), "一意制約(user_id, sender_name_en, company_name_en)が見つからない"


def test_sender_profiles_does_not_grant_insert_delete_to_anon():
    """anonへの権限付与が一切無い"""
    text = _strip_sql_comments(_read(MIGRATIONS_DIR / "0006_sender_profiles.sql")).lower()
    assert not re.search(r"to\s+anon\b", text), "anonへの権限付与がある"


def test_sender_profiles_not_used_for_auth_or_role_decisions():
    """送信者プロフィールのテーブル・ポリシーが、role判定・is_admin判定に使われていない

    説明コメント中の言及（「app_metadata.roleとは無関係」等）は対象外にする。
    実際のコードで app_metadata / is_admin を参照していないことだけを見る。
    """
    text = _strip_sql_comments(_read(MIGRATIONS_DIR / "0006_sender_profiles.sql")).lower()
    for forbidden in ("app_metadata", "is_admin"):
        assert forbidden not in text, f"{forbidden} が0006のコードに含まれている（権限判定との混在の疑い）"


def test_sender_profiles_has_one_default_per_user_partial_unique_index():
    """同一利用者がis_default=trueを複数持てないことをDB側（部分一意索引）でも保証する

    アプリ側の_clear_other_defaultsは「先に他をfalseへ戻す」事前処理であり、
    同時更新・複数タブからの競合は防げない。DB側の部分一意索引が最終防御になる。
    """
    text = _strip_sql_comments(_read(MIGRATIONS_DIR / "0006_sender_profiles.sql"))
    assert re.search(
        r"create\s+unique\s+index\s+if\s+not\s+exists\s+sender_profiles_one_default_per_user"
        r"\s+on\s+public\.sender_profiles\s*\(\s*user_id\s*\)\s*where\s+is_default",
        text, re.I), "is_defaultの部分一意索引(user_id)が見つからない"


def test_sender_profiles_migration_statements_are_all_safely_rerunnable():
    """0006を複数回実行しても壊れない（既存テーブルへの追加実行も含む）ことの静的証拠

    create table/index/trigger/policy が、ガード（if not exists /
    drop ... if exists の直後）なしに書かれていないことを確認する。
    一度本番へ適用した後に、このファイルへ追記したものを含めて再実行しても
    エラーにならないという保証の根拠になる。
    """
    text = _strip_sql_comments(_read(MIGRATIONS_DIR / "0006_sender_profiles.sql"))

    # bare CREATE TABLE / CREATE INDEX / CREATE UNIQUE INDEX は禁止（ifnotexists必須）
    for m in re.finditer(r"create\s+(unique\s+index|index|table)\s+(\S+)", text, re.I):
        assert m.group(2).lower() == "if", (
            f"ガード(if not exists)の無い CREATE が見つかった: {m.group(0)!r}"
        )

    # CREATE POLICY / CREATE TRIGGER は、同名の DROP ... IF EXISTS が直前にあること
    for kind, name in re.findall(
            r"create\s+(policy|trigger)\s+(\w+)\s+on\s+public\.sender_profiles",
            text, re.I):
        drop_pattern = re.compile(
            rf"drop\s+{kind}\s+if\s+exists\s+{re.escape(name)}\s+on\s+public\.sender_profiles",
            re.I)
        assert drop_pattern.search(text), (
            f"{kind} {name} の前に drop ... if exists が無い（再実行時に失敗する）"
        )


if __name__ == "__main__":
    sys.exit(run(dict(globals()), "SQLマイグレーション静的テスト"))
