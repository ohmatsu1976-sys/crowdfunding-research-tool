# -*- coding: utf-8 -*-
"""案件管理（第1段階「返信獲得」）SQLマイグレーションの静的・決定論テスト

実行: python tests/test_cases_sql_migrations.py

本物のPostgreSQL/Supabaseには接続しない。ファイルのテキストを解析して、
「危険な形にならないこと」を機械的に確認する（test_sql_migrations.pyと同じ方針）。
対象は新規追加した 0007_cases_tables.sql / 0008_cases_functions.sql /
0009_cases_rls_grants.sql と、verify.sqlへ追記した検査ブロックだけで、
既存の0001〜0006・verify.sqlの既存部分には一切手を加えない
（既存の test_sql_migrations.py も無変更のまま通る）。
"""

import re
import sys
from pathlib import Path

from _harness import run  # noqa: E402

SQL_DIR = Path(__file__).resolve().parent.parent / "sql"
MIGRATIONS_DIR = SQL_DIR / "migrations"

CASES_MIGRATION_FILES = [
    "0007_cases_tables.sql",
    "0008_cases_functions.sql",
    "0009_cases_rls_grants.sql",
]
ALL_CASES_SQL_FILES = [MIGRATIONS_DIR / n for n in CASES_MIGRATION_FILES]

# 公開RPC（authenticatedへ最終的にEXECUTEを許可する6つ）とその正確な引数シグネチャ
PUBLIC_FUNCTIONS_WITH_SIGNATURE = {
    "create_case_from_candidate": "public.create_case_from_candidate(uuid)",
    "log_case_event": "public.log_case_event(uuid, text, text, text, timestamptz, text, text, text)",
    "void_case_event": "public.void_case_event(uuid, text)",
    "correct_case_event": "public.correct_case_event(uuid, text, text)",
    "redact_case_event": "public.redact_case_event(uuid)",
    "save_ai_assist_result": "public.save_ai_assist_result(uuid, jsonb)",
}
# 内部専用（EXECUTEを誰にも与えない）
INTERNAL_FUNCTIONS_WITH_SIGNATURE = {
    "_case_apply_event": "public._case_apply_event(text, text, text, timestamptz)",
    "_case_recompute": "public._case_recompute(uuid)",
}
ALL_FUNCTIONS_WITH_SIGNATURE = {
    **PUBLIC_FUNCTIONS_WITH_SIGNATURE, **INTERNAL_FUNCTIONS_WITH_SIGNATURE,
}


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _strip_sql_comments(text: str) -> str:
    """-- コメントを取り除く（test_sql_migrations.pyと同じ簡易版）"""
    return "\n".join(re.sub(r"--.*$", "", line) for line in text.splitlines())


# ── 1. ファイルの健全性 ────────────────────────────────────────────────────────

def test_all_cases_migration_files_exist():
    """0007/0008/0009が存在する"""
    for name in CASES_MIGRATION_FILES:
        assert (MIGRATIONS_DIR / name).is_file(), f"{name} が無い"


def test_no_cases_sql_file_is_empty_or_truncated():
    """空ファイル・書きかけで切れたファイルが無いこと"""
    for path in ALL_CASES_SQL_FILES:
        text = _read(path)
        assert text.strip(), f"{path.name} が空"
        lines = [l for l in text.splitlines() if l.strip()]
        last = lines[-1].rstrip()
        assert last.endswith(";") or last.endswith("*/"), (
            f"{path.name} の最後の行がセミコロンで終わっていない: {last!r}"
        )


def test_cases_migrations_are_wrapped_in_transactions():
    """0007〜0009はbegin;とcommit;が対になっている（1組以上）"""
    for name in CASES_MIGRATION_FILES:
        text = _read(MIGRATIONS_DIR / name).lower()
        begins = len(re.findall(r"\bbegin\s*;", text))
        commits = len(re.findall(r"\bcommit\s*;", text))
        assert begins >= 1, f"{name} に begin; が無い"
        assert begins == commits, f"{name} の begin/commit 数が不一致: {begins} vs {commits}"


def test_cases_dollar_quoting_is_balanced():
    for path in ALL_CASES_SQL_FILES:
        text = _read(path)
        count = text.count("$$")
        assert count % 2 == 0, f"{path.name} の $$ が対になっていない（{count}個）"


def test_cases_parentheses_are_balanced_per_file():
    for path in ALL_CASES_SQL_FILES:
        text = _strip_sql_comments(_read(path))
        text = re.sub(r"\$\$.*?\$\$", "", text, flags=re.S)
        assert text.count("(") == text.count(")"), f"{path.name} の括弧が不一致"


def test_cases_migrations_use_if_not_exists_guards():
    """create table/index は if not exists 付きで、複数回実行しても壊れない"""
    text = _strip_sql_comments(_read(MIGRATIONS_DIR / "0007_cases_tables.sql"))
    for m in re.finditer(r"create\s+(unique\s+index|index|table)\s+(\S+)", text, re.I):
        assert m.group(2).lower() == "if", (
            f"ガード(if not exists)の無い CREATE が見つかった: {m.group(0)!r}"
        )


def test_cases_triggers_have_drop_if_exists_before_create():
    """0008のCREATE TRIGGERには直前にDROP TRIGGER IF EXISTSがある（再実行対応）"""
    text = _strip_sql_comments(_read(MIGRATIONS_DIR / "0008_cases_functions.sql"))
    for name, table in [("cases_set_updated_at", "cases"),
                        ("case_targets_set_updated_at", "case_targets")]:
        drop_pattern = re.compile(
            rf"drop\s+trigger\s+if\s+exists\s+{re.escape(name)}\s+on\s+public\.{table}", re.I)
        assert drop_pattern.search(text), f"{name} の前に drop trigger if exists が無い"


# ── 2. 秘密情報の非混入 ─────────────────────────────────────────────────────────

_UUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)
_JWT_RE = re.compile(r"eyJ[A-Za-z0-9_-]{10,}")


def test_no_real_uuid_or_jwt_in_cases_sql_files():
    for path in ALL_CASES_SQL_FILES:
        text = _read(path)
        assert not _UUID_RE.search(text), f"{path.name} にUUIDらしき文字列がある"
        assert not _JWT_RE.search(text), f"{path.name} にJWTらしき文字列がある"


def test_no_service_role_or_secret_key_in_cases_sql_files():
    for path in ALL_CASES_SQL_FILES:
        upper = _read(path).upper()
        assert "SERVICE_ROLE_KEY" not in upper
        assert "SB_SECRET" not in upper


# ── 3. 権限遮断：0008の関数作成の直後にREVOKEがあること ─────────────────────────

def test_0008_revokes_execute_immediately_after_each_function():
    """8関数（公開6＋内部2）すべて、作成直後に public/anon/authenticated からREVOKE"""
    text = _strip_sql_comments(_read(MIGRATIONS_DIR / "0008_cases_functions.sql"))

    positions = {}
    for name in ALL_FUNCTIONS_WITH_SIGNATURE:
        m = re.search(
            r"create\s+or\s+replace\s+function\s+public\." + re.escape(name) + r"\s*\(",
            text, re.I)
        assert m, f"{name} の create or replace function が見つからない"
        positions[name] = m.start()

    ordered = sorted(positions.items(), key=lambda kv: kv[1])
    for i, (name, start) in enumerate(ordered):
        end = ordered[i + 1][1] if i + 1 < len(ordered) else len(text)
        segment = text[start:end]
        # シグネチャが複数行に折り返されていても一致するよう、空白の連なりを先に
        # 分割してから \s+ で結合し直す（re.escape後に空白を置換すると、Pythonの
        # バージョンによって空白自体がエスケープされ、置換結果が壊れることがある）。
        # 開き括弧の直後にも \s* を許す（"function_name(\n    uuid, ..." のような
        # 折り返しに対応する）。
        sig_pat = r"\s+".join(re.escape(part) for part in
                              re.split(r"\s+", ALL_FUNCTIONS_WITH_SIGNATURE[name]))
        sig_pat = sig_pat.replace(r"\(", r"\(\s*")
        for role in ("public", "anon", "authenticated"):
            pattern = (r"revoke\s+execute\s+on\s+function\s+" + sig_pat
                      + r"\s+from\s+" + role + r"\s*;")
            assert re.search(pattern, segment, re.I), (
                f"{name}: 作成直後のブロック内に 'revoke execute ... from {role}' が見つからない"
            )


def test_0009_grants_only_the_six_public_facing_functions():
    """0009がauthenticatedへGRANTするのは公開6RPCだけ（内部2関数は対象外）"""
    text = _strip_sql_comments(_read(MIGRATIONS_DIR / "0009_cases_rls_grants.sql"))
    grants = re.findall(
        r"grant\s+execute\s+on\s+function\s+public\.(\w+)\s*\(", text, re.I)
    assert set(grants) == set(PUBLIC_FUNCTIONS_WITH_SIGNATURE), (
        f"0009のGRANT対象が想定と異なる: {set(grants)}"
    )
    for internal_name in INTERNAL_FUNCTIONS_WITH_SIGNATURE:
        assert f"public.{internal_name}" not in text.lower() or (
            f"grant execute on function public.{internal_name.lower()}" not in text.lower()
        ), f"0009が内部関数 {internal_name} をGRANTしている"


def test_0009_does_not_re_revoke_execute():
    """REVOKEは0008だけで完結し、0009で重複させない"""
    text = _strip_sql_comments(_read(MIGRATIONS_DIR / "0009_cases_rls_grants.sql")).lower()
    assert "revoke execute" not in text, (
        "0009にEXECUTEのREVOKEが残っている。REVOKEは0008だけで行う設計"
    )


def test_no_cases_function_grants_execute_to_public_or_anon():
    for path in ALL_CASES_SQL_FILES:
        text = _strip_sql_comments(_read(path)).lower()
        assert not re.search(r"grant\s+execute\s+on\s+function\s+\S+\s+to\s+(public|anon)\b",
                             text), f"{path.name} が EXECUTE を public/anon へ許可している"


def test_no_function_overloads_in_cases_functions():
    """1関数につき1シグネチャ（オーバーロードを作らない）"""
    text = _strip_sql_comments(_read(MIGRATIONS_DIR / "0008_cases_functions.sql"))
    for name in ALL_FUNCTIONS_WITH_SIGNATURE:
        count = len(re.findall(
            r"create\s+or\s+replace\s+function\s+public\." + re.escape(name) + r"\s*\(",
            text, re.I))
        assert count == 1, f"{name} が複数回定義されている（オーバーロード）"


# ── 4. SECURITY DEFINER / 本人確認の約束 ────────────────────────────────────────

def test_all_public_rpcs_are_security_definer_with_empty_search_path():
    text = _read(MIGRATIONS_DIR / "0008_cases_functions.sql")
    for name in PUBLIC_FUNCTIONS_WITH_SIGNATURE:
        m = re.search(
            r"create\s+or\s+replace\s+function\s+public\." + re.escape(name)
            + r".*?security\s+definer\s+set\s+search_path\s*=\s*''",
            text, re.I | re.S)
        assert m, f"{name} が SECURITY DEFINER + search_path='' になっていない"


def test_all_public_rpcs_reject_null_uid():
    text = _strip_sql_comments(_read(MIGRATIONS_DIR / "0008_cases_functions.sql"))
    for name in PUBLIC_FUNCTIONS_WITH_SIGNATURE:
        m = re.search(
            r"create\s+or\s+replace\s+function\s+public\." + re.escape(name) + r"\s*\(.*?"
            r"(?=create\s+or\s+replace\s+function|\Z)",
            text, re.I | re.S)
        assert m, f"{name} の本体が見つからない"
        body = m.group(0)
        assert "v_uid is null" in body, f"{name} が v_uid is null を確認していない"
        assert "not authenticated" in body, f"{name} が未認証時の拒否文言を持たない"


def test_no_public_rpc_accepts_user_id_argument():
    """6つの公開RPCはいずれもuser_idを引数に取らない（保存者・操作対象はauth.uid()のみ）"""
    text = _strip_sql_comments(_read(MIGRATIONS_DIR / "0008_cases_functions.sql"))
    for name in PUBLIC_FUNCTIONS_WITH_SIGNATURE:
        m = re.search(
            r"create\s+or\s+replace\s+function\s+public\." + re.escape(name)
            + r"\s*\((.*?)\)\s*\nreturns",
            text, re.I | re.S)
        assert m, f"{name} のシグネチャが見つからない"
        args = m.group(1).lower()
        assert "user_id" not in args, f"{name} の引数に user_id がある"
        assert "p_user" not in args, f"{name} の引数に p_user* がある"


def test_each_public_rpc_confirms_ownership_before_mutating():
    """他人のcase_id/event_id/saved_item_idを渡しても操作できないこと（本人確認の条件句）"""
    text = _strip_sql_comments(_read(MIGRATIONS_DIR / "0008_cases_functions.sql"))

    assert re.search(r"from\s+public\.saved_items\s+s\s*\n\s*where\s+s\.id\s*=\s*p_saved_item_id"
                     r"\s*\n\s*and\s+s\.user_id\s*=\s*v_uid", text, re.I), (
        "create_case_from_candidateがsaved_itemsをuser_idで絞っていない"
    )
    for fn in ("log_case_event",):
        assert "c.user_id = v_uid" in text, f"{fn}がcasesをuser_idで絞っていない"
    assert text.count("e.user_id = v_uid") >= 4, (
        "void/correct/redact/save_ai_assist_resultのいずれかがcase_eventsをuser_idで絞っていない"
    )


# ── 5. 状態機械・重複防止の構造的な証拠 ─────────────────────────────────────────

def test_initial_followup_sequence_uses_label_guard_to_prevent_duplicates():
    """フォロー1/フォロー2は、直前のラベルを確認してから進める（重複防止の構造）"""
    text = _strip_sql_comments(_read(MIGRATIONS_DIR / "0008_cases_functions.sql"))
    assert "p_label <> '1回目フォロー'" in text, "followup1がラベルを確認していない"
    assert "p_label <> '2回目フォロー'" in text, "followup2がラベルを確認していない"


def test_reply_clears_follow_up_and_moves_to_awaiting_our_reply():
    text = _read(MIGRATIONS_DIR / "0008_cases_functions.sql")
    assert "'awaiting_our_reply'::text, null::date, ''::text" in text, (
        "reply/condition_answerがフォロー期限を解除してawaiting_our_replyへ"
        "遷移していない"
    )


def test_our_reply_moves_to_awaiting_next_reply():
    text = _read(MIGRATIONS_DIR / "0008_cases_functions.sql")
    assert "'awaiting_next_reply'::text, null::date, ''::text" in text, (
        "our_reply/condition_checkがawaiting_next_replyへ遷移していない"
    )


def test_advanced_requires_awaiting_reply_states():
    """「商談へ進んだ」はこちらの回答待ち／再返信待ちからの手動操作だけに限る"""
    text = _strip_sql_comments(_read(MIGRATIONS_DIR / "0008_cases_functions.sql"))
    assert re.search(
        r"p_new_status\s*=\s*'advanced'\s*\n\s*and\s+v_case\.status\s+not\s+in\s*"
        r"\('awaiting_our_reply'\s*,\s*'awaiting_next_reply'\)",
        text, re.I), "advancedへの遷移条件が見つからない"


def test_case_creation_prevents_duplicate_on_double_click():
    """二重クリックでも同一利用者・同一商品の案件が重複しない（一意制約＋on conflict）"""
    tables_text = _read(MIGRATIONS_DIR / "0007_cases_tables.sql")
    assert "cases_user_product_unique unique (user_id, product_id)" in tables_text
    functions_text = _read(MIGRATIONS_DIR / "0008_cases_functions.sql")
    assert "on conflict (user_id, product_id) do nothing" in functions_text


def test_cases_product_id_is_not_null_so_unique_constraint_cannot_be_bypassed():
    """cases.product_idはNOT NULLである

    PostgreSQLのUNIQUE制約はNULL同士を「異なる値」として扱うため、
    product_idがNULLを許す列だと、同じ利用者が同じ候補から
    unique(user_id, product_id)をすり抜けて複数の案件を作れてしまう
    （NULL, NULLの行を何行でも挿入できる）。product_idがNOT NULLであれば、
    この迂回経路自体が構造的に存在しない。
    """
    text = _strip_sql_comments(_read(MIGRATIONS_DIR / "0007_cases_tables.sql"))
    m = re.search(r"product_id\s+uuid\s+(not\s+null)?[^,]*,", text, re.I)
    assert m, "casesのproduct_id列定義が見つからない"
    assert m.group(1) is not None, (
        "cases.product_idがNOT NULLではない。unique(user_id, product_id)だけでは"
        "NULL同士の重複を防げないため、saved_item_idを含めた重複防止が別途必要になる"
    )


def test_create_case_from_candidate_never_inserts_with_null_product_id():
    """create_case_from_candidate()は、商品が見つからない場合にINSERTへ進まない

    v_product.id が null のときは例外を発生させて処理を止めており、
    NULLのproduct_idでcasesへINSERTしようとする経路が無い
    （cases.product_idがNOT NULLであることの運用側の裏付け）。
    """
    text = _strip_sql_comments(_read(MIGRATIONS_DIR / "0008_cases_functions.sql"))
    m = re.search(
        r"create\s+or\s+replace\s+function\s+public\.create_case_from_candidate.*?"
        r"(?=create\s+or\s+replace\s+function|\Z)", text, re.I | re.S)
    assert m, "create_case_from_candidateの本体が見つからない"
    body = m.group(0)

    guard_pos = body.find("if v_product.id is null then")
    insert_pos = body.find("insert into public.cases")
    assert guard_pos != -1, "v_product.id is null の確認が見つからない"
    assert insert_pos != -1, "casesへのINSERT文が見つからない"
    assert guard_pos < insert_pos, (
        "product存在確認がINSERTより後にある（NULLのproduct_idで"
        "INSERTを試みる経路が生じる）"
    )
    # 同一利用者・同一商品の重複防止はこのNOT NULL前提のうえで
    # unique(user_id, product_id) + on conflict do nothing が担う
    # （test_case_creation_prevents_duplicate_on_double_clickで確認済み）。
    # saved_item_id自体には一意制約を追加していない（1つの候補が複数の
    # 商品案件に紐づく経路は無く、重複防止の単位は常にuser_id×product_idで
    # 十分なため）。
    assert "cases_user_product_unique" in _read(MIGRATIONS_DIR / "0007_cases_tables.sql")


def test_void_case_event_recomputes_and_lets_conflict_propagate():
    """取消し後は_case_recompute()で再計算し、矛盾があれば例外を伝播させる
    （BEGIN/EXCEPTIONで握りつぶさないこと＝部分更新を残さない構造）"""
    text = _strip_sql_comments(_read(MIGRATIONS_DIR / "0008_cases_functions.sql"))
    m = re.search(
        r"create\s+or\s+replace\s+function\s+public\.void_case_event.*?"
        r"(?=create\s+or\s+replace\s+function|\Z)", text, re.I | re.S)
    assert m, "void_case_eventの本体が見つからない"
    body = m.group(0)
    assert "_case_recompute(v_ev.case_id)" in body
    assert "exception when" not in body.lower(), (
        "void_case_eventがBEGIN/EXCEPTIONブロックで例外を捕まえている"
        "（矛盾時にトランザクション全体を失敗させる設計に反する。"
        "'raise exception ...' 自体は正常なエラー送出なので対象外）"
    )


def test_correct_case_event_voids_original_and_links_superseded_by():
    text = _strip_sql_comments(_read(MIGRATIONS_DIR / "0008_cases_functions.sql"))
    m = re.search(
        r"create\s+or\s+replace\s+function\s+public\.correct_case_event.*?"
        r"(?=create\s+or\s+replace\s+function|\Z)", text, re.I | re.S)
    assert m, "correct_case_eventの本体が見つからない"
    body = m.group(0)
    assert "voided_reason = 'corrected'" in body
    assert "superseded_by = v_new_id" in body
    assert "_case_recompute(v_ev.case_id)" in body


def test_redact_case_event_clears_text_and_ai_assist_irreversibly():
    """伏せ字化はmessage_text/translated_text/ai_assistをすべて消す

    本文を基に生成されたai_assist（AI支援結果）が、伏せ字化後も残って
    しまわないことをUPDATE文の対象列として明示的に確認する（単なる文字列の
    存在確認ではなく、同じUPDATE文の中で ai_assist = null になっていること）。
    """
    text = _strip_sql_comments(_read(MIGRATIONS_DIR / "0008_cases_functions.sql"))
    m = re.search(
        r"create\s+or\s+replace\s+function\s+public\.redact_case_event.*?"
        r"(?=create\s+or\s+replace\s+function|\Z)", text, re.I | re.S)
    assert m, "redact_case_eventの本体が見つからない"
    body = m.group(0)

    update_m = re.search(
        r"update\s+public\.case_events\s*\n\s*set\s+(.*?)\s+where\s+id\s*=\s*p_event_id",
        body, re.I | re.S)
    assert update_m, "redact_case_eventのUPDATE文が見つからない"
    set_clause = update_m.group(1)

    assert re.search(r"message_text\s*=\s*'[^']*'", set_clause), (
        "message_textをUPDATEしていない"
    )
    assert re.search(r"translated_text\s*=\s*'[^']*'", set_clause), (
        "translated_textをUPDATEしていない"
    )
    assert re.search(r"ai_assist\s*=\s*null\b", set_clause, re.I), (
        "同じUPDATE文の中でai_assistをnullにしていない"
        "（本文を基に生成されたAI支援結果が伏せ字化後も残ってしまう）"
    )


def test_correct_case_event_new_row_does_not_carry_over_ai_assist():
    """訂正で作られる新しい行に、訂正前のai_assistを引き継がない

    correct_case_event()のINSERT文の列一覧にai_assistが含まれていないことを
    確認する。case_events.ai_assist列はDEFAULTを持たないため、列一覧から
    除外されていれば新しい行は必ずNULLから始まる（0007_cases_tables.sql側の
    デフォルト未設定もあわせて確認する）。
    """
    functions_text = _strip_sql_comments(_read(MIGRATIONS_DIR / "0008_cases_functions.sql"))
    m = re.search(
        r"create\s+or\s+replace\s+function\s+public\.correct_case_event.*?"
        r"(?=create\s+or\s+replace\s+function|\Z)", functions_text, re.I | re.S)
    assert m, "correct_case_eventの本体が見つからない"
    body = m.group(0)

    insert_m = re.search(
        r"insert\s+into\s+public\.case_events\s*\(([^)]*)\)\s*\n?\s*values",
        body, re.I | re.S)
    assert insert_m, "correct_case_eventのINSERT文が見つからない"
    columns = {c.strip() for c in insert_m.group(1).replace("\n", " ").split(",")}
    assert "ai_assist" not in columns, (
        "correct_case_eventのINSERT列一覧にai_assistが含まれている"
        "（訂正前の値を引き継いでしまう恐れがある）"
    )

    tables_text = _read(MIGRATIONS_DIR / "0007_cases_tables.sql")
    assert re.search(r"ai_assist\s+jsonb,\s*\n", tables_text), (
        "case_events.ai_assist列の定義が見つからない、またはDEFAULTが"
        "設定されている（列一覧から外しても別の値で埋まってしまう）"
    )


def test_save_ai_assist_result_restricts_to_inbound_and_unredacted_unvoided():
    text = _strip_sql_comments(_read(MIGRATIONS_DIR / "0008_cases_functions.sql"))
    m = re.search(
        r"create\s+or\s+replace\s+function\s+public\.save_ai_assist_result.*?"
        r"(?=create\s+or\s+replace\s+function|\Z)", text, re.I | re.S)
    assert m, "save_ai_assist_resultの本体が見つからない"
    body = m.group(0)
    assert "not an inbound message" in body
    assert "'reply', 'condition_answer'" in body
    assert "event voided" in body
    assert "event redacted" in body


def test_create_case_from_candidate_copies_only_allowlisted_contact_keys():
    """connact_snapshotは許可リストのキーだけをコピーする（不要なデータを保存しない）"""
    text = _strip_sql_comments(_read(MIGRATIONS_DIR / "0008_cases_functions.sql"))
    m = re.search(r"kv\.key\s+in\s*\(([^)]+)\)", text, re.I)
    assert m, "contact_snapshotのキー許可リストが見つからない"
    keys = {k.strip().strip("'") for k in m.group(1).split(",")}
    assert keys == {"email", "facebook", "instagram", "contact_form",
                    "linkedin", "official_url"}, keys


# ── 6. テーブル制約・RLS・GRANTの構造 ───────────────────────────────────────────

def test_cases_table_has_no_insert_policy():
    text = _strip_sql_comments(_read(MIGRATIONS_DIR / "0009_cases_rls_grants.sql"))
    assert not re.search(r"create\s+policy\s+\w+\s+on\s+public\.cases\s+for\s+insert",
                         text, re.I), "casesにINSERTポリシーがある"


def test_case_events_table_has_only_select_policy():
    text = _strip_sql_comments(_read(MIGRATIONS_DIR / "0009_cases_rls_grants.sql"))
    policies = re.findall(r"create\s+policy\s+(\w+)\s+on\s+public\.case_events\s+for\s+(\w+)",
                          text, re.I)
    assert len(policies) == 1, f"case_eventsのポリシーが1つではない: {policies}"
    assert policies[0][1].lower() == "select"


def test_revoke_all_happens_before_column_grants_in_0009():
    text = _strip_sql_comments(_read(MIGRATIONS_DIR / "0009_cases_rls_grants.sql")).lower()
    revoke_pos = text.find("revoke all")
    grant_pos = text.find("grant update (")
    assert revoke_pos != -1 and grant_pos != -1
    assert revoke_pos < grant_pos, "revoke all より前に列GRANTが書かれている（順序ミス）"


def test_cases_update_grant_excludes_state_machine_columns():
    """casesのUPDATE可能列にstatus/has_reply/first_sent_at等が無い（RPC経由のみ）"""
    text = _strip_sql_comments(_read(MIGRATIONS_DIR / "0009_cases_rls_grants.sql"))
    m = re.search(r"grant\s+update\s*\(([^)]*)\)\s*\n?\s*on\s+public\.cases", text, re.I)
    assert m, "casesのGRANT UPDATEが見つからない"
    columns = {c.strip() for c in m.group(1).replace("\n", " ").split(",")}
    assert columns == {"memo", "next_action_at", "next_action_label",
                       "archived", "primary_channel"}, columns


def test_case_targets_update_grant_excludes_ownership_and_month_columns():
    text = _strip_sql_comments(_read(MIGRATIONS_DIR / "0009_cases_rls_grants.sql"))
    m = re.search(r"grant\s+update\s*\(([^)]*)\)\s*\n?\s*on\s+public\.case_targets",
                  text, re.I)
    assert m, "case_targetsのGRANT UPDATEが見つからない"
    columns = {c.strip() for c in m.group(1).split(",")}
    assert columns == {"approach_target", "reply_target"}, columns


def test_no_insert_update_delete_grant_on_case_events():
    text = _strip_sql_comments(_read(MIGRATIONS_DIR / "0009_cases_rls_grants.sql")).lower()
    for verb in ("insert", "update", "delete"):
        assert not re.search(rf"grant\s+{verb}.*on\s+public\.case_events", text), (
            f"case_eventsに{verb.upper()}権限がGRANTされている"
        )


def test_no_insert_grant_on_cases():
    text = _strip_sql_comments(_read(MIGRATIONS_DIR / "0009_cases_rls_grants.sql")).lower()
    assert not re.search(r"grant\s+insert.*on\s+public\.cases\b", text), (
        "casesにINSERT権限がGRANTされている"
    )


def test_cases_target_month_is_normalized_to_first_of_month():
    text = _read(MIGRATIONS_DIR / "0007_cases_tables.sql")
    assert "target_month = date_trunc('month', target_month)::date" in text


def test_case_targets_rejects_negative_and_excessive_values():
    text = _read(MIGRATIONS_DIR / "0007_cases_tables.sql")
    assert "approach_target between 0 and 100000" in text
    assert "reply_target    between 0 and 100000" in text or \
           "reply_target between 0 and 100000" in text


def test_case_targets_primary_key_is_user_and_month():
    text = _strip_sql_comments(_read(MIGRATIONS_DIR / "0007_cases_tables.sql"))
    assert re.search(r"primary\s+key\s*\(\s*user_id\s*,\s*target_month\s*\)", text, re.I)


# ── 7. verify.sqlへの追記内容 ───────────────────────────────────────────────────

def test_verify_sql_still_returns_a_single_unified_result_set():
    """案件管理の検査を追記した後もverify.sqlは1つの統合結果表のまま"""
    text = _strip_sql_comments(_read(SQL_DIR / "verify.sql"))
    statements = [s.strip() for s in text.split(";") if s.strip()]
    assert len(statements) == 1, (
        f"verify.sql の実行文が複数ある（{len(statements)}件）"
    )


def test_verify_sql_covers_cases_checks():
    """案件管理の検査フラグメントがverify.sqlに含まれている"""
    text = _read(SQL_DIR / "verify.sql")
    required_fragments = [
        "cases/case_events/case_targetsの3テーブルが存在する",
        "3テーブルすべてでRLSが有効",
        "casesのポリシー数",
        "case_eventsのポリシー数",
        "case_targetsのポリシー数",
        "casesにINSERTポリシーが0件",
        "case_eventsにINSERT/UPDATE/DELETEポリシーが0件",
        "anonがcases/case_events/case_targetsの権限を一切持たない",
        "authenticatedがcasesへ直接INSERTできない",
        "authenticatedがcase_eventsへ直接INSERT/UPDATE/DELETEできない",
        "casesで更新できるのは",
        "case_targetsで更新できるのは",
        "authenticatedが実行できるのは案件管理の新規6RPCだけ",
        "内部専用の2関数をauthenticatedが実行できない",
        "anonが案件管理の新規関数のいずれも実行できない",
        "案件管理の公開6RPCすべてがSECURITY DEFINER",
        "案件管理の公開6RPCすべてでsearch_pathが固定されている",
        "updated_at自動更新トリガーが2本存在する",
        "casesに(user_id,product_id)の一意制約がある",
        "case_targetsに主キー制約が存在する",
        "case_eventsにvoided_at/redacted_at/ai_assist列が存在する",
        "case_targetsのapproach_target/reply_targetに範囲チェック制約がある",
        "案件管理の新規6RPCがいずれもuser_idを引数に取らない",
    ]
    for fragment in required_fragments:
        assert fragment in text, f"必須検査が見つからない: {fragment!r}"


def test_verify_sql_does_not_output_secret_looking_values_for_cases_checks():
    text = _strip_sql_comments(_read(SQL_DIR / "verify.sql")).lower()
    assert "select email" not in text
    assert "select user_id" not in text
    assert not re.search(r"select\s+[\w.]*\bemail\b", text)


if __name__ == "__main__":
    sys.exit(run(dict(globals()), "案件管理SQLマイグレーション静的テスト"))
