# -*- coding: utf-8 -*-
"""「商品を探す」画面の描画テスト（st.testing.v1.AppTest、外部通信なし）

実行: python tests/test_kickstarter_discovery_ui.py

検索ボタンを押していない限りAPIキーの読み込み自体が走らない（既存の検索
フォームと同じ方式）。ボタンを押すテストでは kickstarter_discovery.run_discovery
を偽物に差し替え、実Anthropic APIへは一切通信しない。
"""

import sys
from pathlib import Path

from _harness import run  # noqa: E402
import auth  # noqa: E402
import candidates_ui  # noqa: E402
import kickstarter_discovery as kd  # noqa: E402
import kickstarter_discovery_ui as kdui  # noqa: E402

from streamlit.testing.v1 import AppTest  # noqa: E402

APP = str(Path(__file__).resolve().parent.parent / "streamlit_app.py")
TIMEOUT = 60

FAKE_SECRETS = {"SUPABASE_URL": "https://example.supabase.co",
                "SUPABASE_PUBLISHABLE_KEY": "sb_publishable_dummy-not-a-real-key",
                "ANTHROPIC_API_KEY": "sk-ant-dummy-not-a-real-key"}


def _app(logged_in=True, role=None) -> AppTest:
    at = AppTest.from_file(APP, default_timeout=TIMEOUT)
    for name, value in FAKE_SECRETS.items():
        at.secrets[name] = value
    if logged_in:
        at.session_state[auth.USER_ID] = "test-user"
        at.session_state[auth.EMAIL] = "student@example.com"
        at.session_state[auth.ACCESS_TOKEN] = "dummy-access"
        at.session_state[auth.REFRESH_TOKEN] = "dummy-refresh"
        at.session_state[auth.MUST_CHANGE] = False
        if role is not None:
            at.session_state[auth.ROLE] = role
    return at


def _text(at: AppTest) -> str:
    parts = []
    for attr in ("markdown", "caption", "subheader", "title", "warning",
                 "error", "info", "success", "text"):
        for el in getattr(at, attr, []):
            parts.append(str(getattr(el, "value", "")))
    return "\n".join(parts)


def _goto_discover(at: AppTest) -> AppTest:
    at = at.run()
    radio = at.radio[0]
    assert kdui_label() in radio.options, radio.options
    return radio.set_value(kdui_label()).run()


def kdui_label() -> str:
    return candidates_ui._VIEW_LABELS[candidates_ui.VIEW_DISCOVER]


# ── 表示対象（通常版のみ）────────────────────────────────────────────────────

def test_discover_option_visible_for_logged_in_student():
    at = _app().run()
    assert not at.exception, str(at.exception)
    assert kdui_label() in list(at.radio[0].options)


def test_discover_hidden_for_summit_role():
    at = _app(role=auth.ROLE_SUMMIT).run()
    assert not at.exception, str(at.exception)
    assert list(at.radio) == [], "サミット版に画面切替が出ている"
    assert "商品を探す" not in _text(at)


def test_discover_hidden_when_logged_out():
    at = _app(logged_in=False).run()
    assert not at.exception, str(at.exception)
    assert list(at.radio) == []
    assert "商品を探す" not in _text(at)


def test_discover_screen_renders_without_calling_api():
    """画面を開いただけではAPIキー読み込み・API呼び出しが起きない"""
    at = _goto_discover(_app())
    assert not at.exception, str(at.exception)
    assert "商品を探す" in _text(at)
    assert "網羅する検索ではありません" in _text(at)


def test_default_statuses_are_all_checked():
    at = _goto_discover(_app())
    boxes = {cb.label: cb.value for cb in at.checkbox}
    assert boxes.get("ライブ") is True
    assert boxes.get("遅延プレッジ") is True
    assert boxes.get("終了済み") is True


# ── 入力検証（APIを呼ばずに拒否）───────────────────────────────────────────────

def test_empty_keyword_is_rejected_without_calling_api(monkeypatch=None):
    calls = []
    original = kd.run_discovery
    kd.run_discovery = lambda *a, **k: calls.append(1) or original(*a, **k)
    try:
        at = _goto_discover(_app())
        search_btn = [b for b in at.button if "検索する" in b.label][0]
        at = search_btn.click().run()
    finally:
        kd.run_discovery = original
    assert not at.exception, str(at.exception)
    assert not calls, "キーワード未入力でもAPIが呼ばれた"
    assert "キーワードを入力してください" in _text(at)


def test_too_long_keyword_is_rejected_without_calling_api():
    calls = []
    original = kd.run_discovery
    kd.run_discovery = lambda *a, **k: calls.append(1) or original(*a, **k)
    try:
        at = _goto_discover(_app())
        at.text_input[0].set_value("x" * 201).run()
        search_btn = [b for b in at.button if "検索する" in b.label][0]
        at = search_btn.click().run()
    finally:
        kd.run_discovery = original
    assert not at.exception, str(at.exception)
    assert not calls, "200文字超でもAPIが呼ばれた"
    assert "最大200文字" in _text(at)


def test_no_status_selected_is_rejected_without_calling_api():
    calls = []
    original = kd.run_discovery
    kd.run_discovery = lambda *a, **k: calls.append(1) or original(*a, **k)
    try:
        at = _goto_discover(_app())
        at.text_input[0].set_value("調理器具").run()
        for cb in at.checkbox:
            cb.uncheck()
        at = at.run()
        search_btn = [b for b in at.button if "検索する" in b.label][0]
        at = search_btn.click().run()
    finally:
        kd.run_discovery = original
    assert not at.exception, str(at.exception)
    assert not calls, "ステータス未選択でもAPIが呼ばれた"
    assert "ステータスを1つ以上選択してください" in _text(at)


# ── 検索実行（run_discoveryを偽物に差し替え、実APIへは通信しない）────────────────

def _fake_result_with_one_live_product() -> kd.DiscoveryResult:
    product = kd.Product(
        name="テスト商品", url="https://www.kickstarter.com/projects/x/test-item",
        summary_ja="テスト用の商品説明です。", maker="テストメーカー", country="US",
        raised_amount=50000, currency="USD", goal_amount=10000, backers=500,
        retrieved_at="2026-10-19T06:00:00+00:00", source_url="https://www.kickstarter.com/projects/x/test-item",
        search_status=kd.STATUS_LIVE, status=kd.STATUS_LIVE,
        percent_funded=500.0, days_remaining=10,
        raised_amount_jpy=7500000.0, jpy_rate_date="2026-10-19",
    )
    result = kd.DiscoveryResult()
    result.by_status[kd.STATUS_LIVE] = [product]
    result.usage = {"search_calls": 1, "fetch_calls": 1, "input_tokens": 100,
                    "output_tokens": 20, "search_elapsed_sec": 1.0, "fetch_elapsed_sec": 1.0}
    result.jpy_rate_date = "2026-10-19"
    return result


def test_search_button_invokes_run_discovery_once_and_shows_result():
    calls = []
    original = kd.run_discovery

    def _fake(client, keyword, statuses, **kwargs):
        calls.append((keyword, list(statuses)))
        return _fake_result_with_one_live_product()

    kd.run_discovery = _fake
    try:
        at = _goto_discover(_app())
        at.text_input[0].set_value("アウトドア調理器具").run()
        search_btn = [b for b in at.button if "検索する" in b.label][0]
        at = search_btn.click().run()
    finally:
        kd.run_discovery = original

    assert not at.exception, str(at.exception)
    assert len(calls) == 1, f"run_discoveryの呼び出し回数が想定外: {len(calls)}"
    assert calls[0][0] == "アウトドア調理器具"
    body = _text(at)
    assert "テスト商品" in body
    assert "テストメーカー" in body


def test_rerun_without_new_click_does_not_call_api_again():
    """結果表示中に無関係な操作をしても、検索ボタンを押さない限り再実行しない"""
    calls = []
    original = kd.run_discovery

    def _fake(client, keyword, statuses, **kwargs):
        calls.append(1)
        return _fake_result_with_one_live_product()

    kd.run_discovery = _fake
    try:
        at = _goto_discover(_app())
        at.text_input[0].set_value("アウトドア調理器具").run()
        search_btn = [b for b in at.button if "検索する" in b.label][0]
        at = search_btn.click().run()
        at = at.checkbox[0].run()  # 無関係な再描画
    finally:
        kd.run_discovery = original

    assert not at.exception, str(at.exception)
    assert len(calls) == 1, "再描画だけでAPIが再実行された"
    assert "テスト商品" in _text(at)


def test_zero_results_shows_honest_message_not_nonexistence_claim():
    original = kd.run_discovery
    kd.run_discovery = lambda client, keyword, statuses, **kwargs: kd.DiscoveryResult(
        usage={"search_calls": 1, "fetch_calls": 0, "input_tokens": 1, "output_tokens": 1,
              "search_elapsed_sec": 0.1, "fetch_elapsed_sec": 0.0})
    try:
        at = _goto_discover(_app())
        at.text_input[0].set_value("存在しないであろう商品").run()
        search_btn = [b for b in at.button if "検索する" in b.label][0]
        at = search_btn.click().run()
    finally:
        kd.run_discovery = original

    assert not at.exception, str(at.exception)
    body = _text(at)
    assert "存在しないという意味ではありません" in body


# ── 既存リサーチ画面への接続 ───────────────────────────────────────────────────

def test_use_this_product_button_hands_off_url_and_switches_view():
    original = kd.run_discovery
    kd.run_discovery = lambda client, keyword, statuses, **kwargs: \
        _fake_result_with_one_live_product()
    try:
        at = _goto_discover(_app())
        at.text_input[0].set_value("アウトドア調理器具").run()
        search_btn = [b for b in at.button if "検索する" in b.label][0]
        at = search_btn.click().run()
        use_btn = [b for b in at.button if "詳しくリサーチ" in b.label][0]
        at = use_btn.click().run()
    finally:
        kd.run_discovery = original

    assert not at.exception, str(at.exception)
    assert at.session_state[candidates_ui.VIEW] == candidates_ui.VIEW_SEARCH
    assert "https://www.kickstarter.com/projects/x/test-item" in \
        at.session_state["url_input_text"]
    assert kdui.PENDING_HANDOFF_URL not in at.session_state.filtered_state


def test_handoff_does_not_duplicate_existing_url():
    at = _app()
    at.session_state["url_input_text"] = "https://www.kickstarter.com/projects/x/test-item"
    at.session_state[kdui.PENDING_HANDOFF_URL] = "https://www.kickstarter.com/projects/x/test-item"
    at = at.run()
    assert not at.exception, str(at.exception)
    assert at.session_state["url_input_text"].count(
        "https://www.kickstarter.com/projects/x/test-item") == 1


def test_discover_view_has_no_db_save_without_pressing_use_button():
    """結果表示だけではDBへ保存しない（保存は既存の保存パネル経由のみ）"""
    original = kd.run_discovery
    kd.run_discovery = lambda client, keyword, statuses, **kwargs: \
        _fake_result_with_one_live_product()
    try:
        at = _goto_discover(_app())
        at.text_input[0].set_value("アウトドア調理器具").run()
        search_btn = [b for b in at.button if "検索する" in b.label][0]
        at = search_btn.click().run()
    finally:
        kd.run_discovery = original
    assert not at.exception, str(at.exception)
    assert "マイ候補リストに保存しました" not in _text(at)


# ── ログアウト時のクリア ─────────────────────────────────────────────────────

def test_logout_clears_kd_state():
    original = kd.run_discovery
    kd.run_discovery = lambda client, keyword, statuses, **kwargs: \
        _fake_result_with_one_live_product()
    try:
        at = _goto_discover(_app())
        at.text_input[0].set_value("アウトドア調理器具").run()
        search_btn = [b for b in at.button if "検索する" in b.label][0]
        at = search_btn.click().run()
    finally:
        kd.run_discovery = original

    logout = [b for b in at.button if "ログアウト" in b.label][0]
    at = logout.click().run()
    assert not at.exception, str(at.exception)
    for key in at.session_state.filtered_state:
        assert not str(key).startswith("kd_"), f"{key} が残っている"


# ── コスト上限 ────────────────────────────────────────────────────────────────

def test_search_and_fetch_limits_are_not_changed_by_ui():
    assert kd.MAX_SEARCH_CALLS == 3
    assert kd.MAX_FETCH_CALLS == 10


if __name__ == "__main__":
    sys.exit(run(dict(globals()), "「商品を探す」画面テスト（外部通信なし）"))
