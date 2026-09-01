# -*- coding: utf-8 -*-
"""検索結果の保持（session_state）の決定論テスト

実行: python tests/test_search_state.py

search_state は Streamlit に依存しないため、session_state の代わりに
ただの dict を渡して検証できる。外部AI API・ネットワークは使わない。
"""

import sys
from datetime import datetime

from _harness import run  # noqa: E402
import search_state as sstate  # noqa: E402

ROWS_A = [{"商品名": "A商品", "優先度": "A"}]
ROWS_B = [{"商品名": "B商品", "優先度": "B"}, {"商品名": "C商品", "優先度": "C"}]
QUERY_A = ["https://www.kickstarter.com/projects/x/aaa"]
QUERY_B = ["https://www.kickstarter.com/projects/y/bbb",
           "https://www.kickstarter.com/projects/z/ccc"]


def _fresh() -> dict:
    state: dict = {}
    sstate.init_state(state)
    return state


# ── 1. 初期状態 ───────────────────────────────────────────────────────────────

def test_initial_state_has_no_results():
    """初回表示では検索結果が無い"""
    state = _fresh()
    assert sstate.has_results(state) is False
    assert sstate.get_results(state) == []
    assert sstate.get_error(state) == ""
    assert sstate.get_executed_at(state) is None


def test_init_state_does_not_overwrite():
    """再実行のたびに初期化しても既存の結果を壊さない"""
    state = _fresh()
    sstate.save_success(state, ROWS_A, QUERY_A)
    sstate.init_state(state)          # 再実行を模す
    assert sstate.get_results(state) == ROWS_A


# ── 2. 検索成功 ───────────────────────────────────────────────────────────────

def test_success_stores_results_and_context():
    """検索成功で結果・検索条件・日時・成功状態が保存される"""
    state = _fresh()
    assert sstate.save_success(state, ROWS_A, QUERY_A) is True
    assert sstate.get_results(state) == ROWS_A
    assert sstate.get_query(state) == QUERY_A
    assert isinstance(sstate.get_executed_at(state), datetime)
    assert state[sstate.STATUS] == sstate.STATUS_OK
    assert sstate.get_error(state) == ""


def test_results_survive_rerun():
    """Streamlitの再実行後も結果が残る（状態を読み直すだけで消えない）"""
    state = _fresh()
    sstate.save_success(state, ROWS_A, QUERY_A)
    for _ in range(3):                # 再実行を3回模す
        sstate.init_state(state)
        assert sstate.has_results(state) is True
    assert sstate.get_results(state) == ROWS_A


def test_results_survive_unrelated_interaction():
    """CSVダウンロードなど検索と無関係な操作でも結果が残る"""
    state = _fresh()
    sstate.save_success(state, ROWS_A, QUERY_A)
    state["some_widget_value"] = "ユーザーが別の入力欄を触った"
    sstate.init_state(state)
    assert sstate.get_results(state) == ROWS_A


def test_stored_results_are_independent_copy():
    """保存後に呼び出し側のリストを変更しても保持中の結果は変わらない"""
    state = _fresh()
    rows = list(ROWS_A)
    sstate.save_success(state, rows, QUERY_A)
    rows.append({"商品名": "後から追加", "優先度": "C"})
    assert len(sstate.get_results(state)) == 1


def test_new_success_replaces_previous():
    """新しい検索が成功したときだけ結果が置き換わる"""
    state = _fresh()
    sstate.save_success(state, ROWS_A, QUERY_A)
    sstate.save_success(state, ROWS_B, QUERY_B)
    assert sstate.get_results(state) == ROWS_B
    assert sstate.get_query(state) == QUERY_B
    assert sstate.is_showing_previous(state) is False


# ── 3. 検索失敗 ───────────────────────────────────────────────────────────────

def test_failure_keeps_previous_results():
    """検索失敗時に以前の正常な結果を消さない"""
    state = _fresh()
    sstate.save_success(state, ROWS_A, QUERY_A)
    sstate.record_failure(state, "Claude API 接続エラー")
    assert sstate.get_results(state) == ROWS_A, "以前の結果が消えている"
    assert sstate.get_query(state) == QUERY_A, "以前の検索条件が消えている"
    assert sstate.get_error(state) == "Claude API 接続エラー"


def test_failure_is_flagged_as_previous_results():
    """失敗後は「以前の結果を表示中」と判定できる"""
    state = _fresh()
    sstate.save_success(state, ROWS_A, QUERY_A)
    sstate.record_failure(state, "エラー")
    assert sstate.is_showing_previous(state) is True


def test_empty_result_is_not_a_success():
    """0件の検索結果を正常な結果として保存しない"""
    state = _fresh()
    sstate.save_success(state, ROWS_A, QUERY_A)
    assert sstate.save_success(state, [], QUERY_B) is False
    assert sstate.get_results(state) == ROWS_A, "0件で以前の結果が上書きされた"
    assert sstate.get_query(state) == QUERY_A


def test_failure_without_previous_results():
    """以前の結果が無い状態で失敗しても壊れない"""
    state = _fresh()
    sstate.record_failure(state, "APIキーが設定されていません")
    assert sstate.has_results(state) is False
    assert sstate.get_error(state) == "APIキーが設定されていません"
    assert sstate.is_showing_previous(state) is False


# ── 4. クリア ─────────────────────────────────────────────────────────────────

def test_clear_removes_results_and_context():
    """クリアで結果・検索条件・日時・エラー表示が初期化される"""
    state = _fresh()
    sstate.save_success(state, ROWS_A, QUERY_A, failed_urls=["u"], log_lines=["l"])
    sstate.record_failure(state, "エラー")
    sstate.clear_results(state)
    assert sstate.has_results(state) is False
    assert sstate.get_query(state) == []
    assert sstate.get_executed_at(state) is None
    assert sstate.get_error(state) == ""
    assert state[sstate.STATUS] is None
    assert state[sstate.FAILED_URLS] == []


def test_clear_keeps_unrelated_state():
    """クリアは検索以外のセッション項目に触らない（入力欄の値など）"""
    state = _fresh()
    state["urls_text_widget"] = "https://example.com"
    sstate.save_success(state, ROWS_A, QUERY_A)
    sstate.clear_results(state)
    assert state["urls_text_widget"] == "https://example.com"


# ── 5. CSVの取り違え防止 ──────────────────────────────────────────────────────

def test_csv_timestamp_follows_stored_result():
    """CSVのファイル名が現在時刻ではなく結果の取得日時になる"""
    state = _fresh()
    fixed = datetime(2026, 1, 2, 3, 4)
    sstate.save_success(state, ROWS_A, QUERY_A, executed_at=fixed)
    assert sstate.timestamp_label(state) == "20260102_0304"


def test_csv_context_not_replaced_by_failed_search():
    """失敗した検索の条件でCSVの見出しが上書きされない"""
    state = _fresh()
    fixed = datetime(2026, 1, 2, 3, 4)
    sstate.save_success(state, ROWS_A, QUERY_A, executed_at=fixed)
    sstate.record_failure(state, "新しい検索が失敗")
    assert sstate.timestamp_label(state) == "20260102_0304"
    assert sstate.get_query(state) == QUERY_A
    assert sstate.get_results(state) == ROWS_A


def test_describe_query_reports_condition():
    """どの検索条件による結果かを説明できる"""
    state = _fresh()
    sstate.save_success(state, ROWS_B, QUERY_B)
    text = sstate.describe_query(state)
    assert "2件" in text, text


# ── 6. セッション独立 ─────────────────────────────────────────────────────────

def test_sessions_do_not_share_results():
    """別セッションの検索結果と混ざらない（状態はセッションごとに独立）"""
    session_a, session_b = _fresh(), _fresh()
    sstate.save_success(session_a, ROWS_A, QUERY_A)
    sstate.save_success(session_b, ROWS_B, QUERY_B)
    assert sstate.get_results(session_a) == ROWS_A
    assert sstate.get_results(session_b) == ROWS_B
    sstate.clear_results(session_a)
    assert sstate.get_results(session_b) == ROWS_B, "他セッションの結果まで消えた"


def test_module_holds_no_global_results():
    """結果をモジュール側（グローバル領域）に保持していない"""
    state = _fresh()
    sstate.save_success(state, ROWS_A, QUERY_A)
    for name in dir(sstate):
        value = getattr(sstate, name)
        if isinstance(value, (list, dict)) and not name.startswith("__"):
            assert value != ROWS_A, f"{name} に結果が残っている"


if __name__ == "__main__":
    sys.exit(run(dict(globals()), "検索結果の保持テスト（session_state）"))
