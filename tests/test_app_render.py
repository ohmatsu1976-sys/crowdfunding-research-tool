# -*- coding: utf-8 -*-
"""アプリ画面の描画テスト（Streamlit組み込みの AppTest を使用）

実行: python tests/test_app_render.py

streamlit に同梱されている st.testing.v1.AppTest でアプリを実行するため、
追加パッケージもブラウザも不要。検索は実行しないので外部AI APIも呼ばない。
（検索ボタンを押さない限り APIキーの読み込み自体が走らない）
"""

import sys
from datetime import datetime
from pathlib import Path

from _harness import run  # noqa: E402
import auth  # noqa: E402
import result_schema as rschema  # noqa: E402
import search_state as sstate  # noqa: E402

from streamlit.testing.v1 import AppTest  # noqa: E402

APP = str(Path(__file__).resolve().parent.parent / "streamlit_app.py")
TIMEOUT = 60

# 認証テスト用のダミー値（本物の接続先でも鍵でもない）
FAKE_SECRETS = {"SUPABASE_URL": "https://example.supabase.co",
                "SUPABASE_PUBLISHABLE_KEY": "sb_publishable_dummy-not-a-real-key"}


def _app(with_secrets=True, logged_in=True, must_change=False) -> AppTest:
    """アプリを用意する（既定はログイン済み・パスワード変更済み）"""
    at = AppTest.from_file(APP, default_timeout=TIMEOUT)
    if with_secrets:
        for name, value in FAKE_SECRETS.items():
            at.secrets[name] = value
    if logged_in:
        at.session_state[auth.USER_ID] = "test-user"
        at.session_state[auth.EMAIL] = "student@example.com"
        at.session_state[auth.ACCESS_TOKEN] = "dummy-access"
        at.session_state[auth.REFRESH_TOKEN] = "dummy-refresh"
        at.session_state[auth.MUST_CHANGE] = must_change
    return at

ROWS = [{col: "" for col in ()}]  # 後で本物の列を入れる


def _row(name: str, priority: str) -> dict:
    """CSV_FIELDS と同じ列を持つ1行を作る"""
    import research_crowdfunding as r
    row = {col: "" for col in r.CSV_FIELDS}
    row.update({"商品名": name, "優先度": priority, "メーカー名": "テスト社",
                "プラットフォーム": "Kickstarter", "調達額(円)": 1000000,
                "調達額(USD)": 6666, "日本で売れそうな理由": "テスト理由",
                "掲載URL": "https://example.com/p", "判定の確度": "データ取得済み",
                "メールアドレス": "未確認", "問い合わせフォームURL": "未確認",
                "Facebook": "未確認", "Instagram": "未確認", "LinkedIn": "未確認",
                "公式サイトURL": ""})
    return row


def _seeded_app(rows, query, status=sstate.STATUS_OK, error="") -> AppTest:
    at = _app()
    at.session_state[sstate.RESULTS] = rows
    at.session_state[sstate.QUERY] = query
    at.session_state[sstate.EXECUTED_AT] = datetime(2026, 1, 2, 3, 4)
    at.session_state[sstate.STATUS] = status
    at.session_state[sstate.ERROR] = error
    at.session_state[sstate.FAILED_URLS] = []
    at.session_state[sstate.LOG] = []
    at.session_state[sstate.SCHEMA] = rschema.SCHEMA_VERSION
    at.session_state[sstate.NOTICE] = ""
    return at.run()


def _text(at: AppTest) -> str:
    """画面に出ている文字列をまとめて返す"""
    parts = []
    for attr in ("markdown", "caption", "subheader", "title", "warning",
                 "error", "info", "success"):
        for el in getattr(at, attr, []):
            parts.append(str(getattr(el, "value", "")))
    return "\n".join(parts)


def test_app_starts_without_error():
    """アプリが例外なく起動する"""
    at = _app().run()
    assert not at.exception, str(at.exception)


def test_initial_view_has_no_results():
    """初回表示では検索結果セクションが出ない"""
    at = _app().run()
    assert not at.exception, str(at.exception)
    assert "Step 3" not in _text(at), "結果が無いのに結果セクションが出ている"


def test_stored_results_are_rendered():
    """session_state に結果があれば、検索せずに結果が描画される"""
    at = _seeded_app([_row("保持された商品", "A")], ["https://example.com/p"])
    assert not at.exception, str(at.exception)
    body = _text(at)
    assert "Step 3" in body, "結果セクションが描画されていない"
    assert "保持された商品" in body, "保持した結果が表示されていない"


def test_search_context_is_shown():
    """どの検索条件・いつの結果かが画面に出る"""
    at = _seeded_app([_row("商品", "A")], ["https://example.com/p"])
    body = _text(at)
    assert "2026-01-02 03:04" in body, "検索日時が表示されていない"
    assert "検索条件" in body, "検索条件が表示されていない"


def test_summary_table_is_rendered_from_state():
    """保持した結果からサマリー表とCSVセクションまで描画できる

    CSVのファイル名が結果の取得日時になることは
    test_search_state.py の test_csv_timestamp_follows_stored_result で検証している
    （AppTest にダウンロードボタンのアクセサが無いため、ここでは描画のみ確認する）。
    """
    at = _seeded_app([_row("商品", "A")], ["https://example.com/p"])
    assert not at.exception, str(at.exception)
    body = _text(at)
    assert "サマリー（主要項目）" in body, "サマリー表まで到達していない"


def test_failure_keeps_showing_previous_results():
    """検索失敗時もエラーを出しつつ以前の結果を表示し続ける"""
    at = _seeded_app([_row("以前の商品", "B")], ["https://example.com/p"],
                     status=sstate.STATUS_ERROR, error="Claude API 接続エラー")
    assert not at.exception, str(at.exception)
    body = _text(at)
    assert "Claude API 接続エラー" in body, "エラーが表示されていない"
    assert "以前の商品" in body, "以前の結果が消えている"
    assert "前回" in body, "以前の結果である旨の表示が無い"


def test_clear_button_removes_results():
    """「検索結果をクリア」で結果が消える"""
    at = _seeded_app([_row("消える商品", "A")], ["https://example.com/p"])
    clear = [b for b in at.button if "クリア" in getattr(b, "label", "")]
    assert clear, "クリアボタンが見つからない"
    at = clear[0].click().run()
    assert not at.exception, str(at.exception)
    assert at.session_state[sstate.RESULTS] == [], "結果が消えていない"
    assert at.session_state[sstate.QUERY] == [], "検索条件が消えていない"
    assert at.session_state[sstate.EXECUTED_AT] is None, "検索日時が消えていない"
    assert "消える商品" not in _text(at), "画面に結果が残っている"


def test_clear_keeps_url_input():
    """クリアしても入力欄のURLは残る（現在のUXを維持）"""
    at = _app()
    at.session_state[sstate.RESULTS] = [_row("商品", "A")]
    at.session_state[sstate.QUERY] = ["https://example.com/p"]
    at.session_state[sstate.EXECUTED_AT] = datetime(2026, 1, 2, 3, 4)
    at.session_state[sstate.STATUS] = sstate.STATUS_OK
    at.run()
    at.text_area[0].set_value("https://www.kickstarter.com/projects/a/b").run()
    clear = [b for b in at.button if "クリア" in getattr(b, "label", "")][0]
    at = clear.click().run()
    assert at.text_area[0].value == "https://www.kickstarter.com/projects/a/b", \
        "入力欄のURLまで消えている"


def test_unrelated_interaction_keeps_results():
    """検索と無関係な入力操作をしても結果が残る"""
    at = _seeded_app([_row("残る商品", "A")], ["https://example.com/p"])
    at.text_input[0].set_value("Taro Yamada").run()
    assert not at.exception, str(at.exception)
    assert "残る商品" in _text(at), "無関係な操作で結果が消えた"


# ── 旧形式セッションからの復帰 ────────────────────────────────────────────────

def _old_session(rows, schema=None) -> AppTest:
    """列構成が変わる前のセッションを模す（スキーマ版を記録していない状態）"""
    at = _app()
    at.session_state[sstate.RESULTS] = rows
    at.session_state[sstate.QUERY] = ["https://example.com/p"]
    at.session_state[sstate.EXECUTED_AT] = datetime(2026, 1, 2, 3, 4)
    at.session_state[sstate.STATUS] = sstate.STATUS_OK
    at.session_state[sstate.ERROR] = ""
    at.session_state[sstate.FAILED_URLS] = []
    at.session_state[sstate.LOG] = []
    if schema is not None:
        at.session_state[sstate.SCHEMA] = schema
    return at.run()


def test_old_format_session_does_not_crash():
    """列が足りない旧形式の結果が残っていても KeyError で落ちない"""
    row = _row("旧形式の商品", "A")
    row.pop("判定の確度")
    row.pop("LinkedIn")
    at = _old_session([row], schema=1)
    assert not at.exception, str(at.exception)
    body = _text(at)
    assert "旧形式の商品" in body, "旧形式の結果が表示されていない"
    assert "判定の確度" in body, "補った項目が利用者に伝わっていない"


def test_broken_session_is_reset_with_explanation():
    """移行できない結果は、白紙やKeyErrorではなく説明を出して初期化する"""
    at = _old_session(["これは行ではない"], schema=1)
    assert not at.exception, str(at.exception)
    body = _text(at)
    assert "初期化" in body, "初期化した理由が説明されていない"
    assert at.session_state[sstate.RESULTS] == [], "結果が残っている"


def test_migration_notice_is_not_repeated():
    """移行の説明は一度出したら繰り返さない"""
    row = _row("旧形式の商品", "A")
    row.pop("判定の確度")
    at = _old_session([row], schema=1)
    at = at.text_input[0].set_value("Taro Yamada").run()
    assert not at.exception, str(at.exception)
    assert "そろえました" not in _text(at), "移行の説明が出続けている"
    assert "旧形式の商品" in _text(at), "移行後の結果が消えた"


# ── 認証ゲート ────────────────────────────────────────────────────────────────

def test_logged_out_shows_only_login():
    """未ログインでは検索フォームにもAI処理にも到達しない"""
    at = _app(logged_in=False).run()
    assert not at.exception, str(at.exception)
    body = _text(at)
    assert "Step 1" not in body and "Step 2" not in body, "検索画面が見えている"
    assert "Step 3" not in body, "結果セクションが見えている"
    assert at.text_area == [], "URL入力欄が表示されている"
    labels = [getattr(b, "label", "") for b in at.button]
    assert labels == ["ログイン"], f"ログイン以外のボタンが出ている: {labels}"


def test_login_screen_has_no_signup_or_reset():
    """新規登録・パスワード再設定メールのボタンを出さない"""
    at = _app(logged_in=False).run()
    labels = [getattr(b, "label", "") for b in at.button]
    for word in ("新規登録", "サインアップ", "再設定", "リセット", "招待"):
        assert not any(word in label for label in labels), f"{word} のボタンがある"
    body = _text(at)
    assert "管理者に再発行を依頼" in body, "再発行の案内が出ていない"


def test_login_screen_does_not_leak_secrets():
    """ログイン画面に接続情報の実値を出さない"""
    body = _text(_app(logged_in=False).run())
    for value in FAKE_SECRETS.values():
        assert value not in body, "Secretsの値が画面に出ている"


def test_missing_secrets_shows_setup_message():
    """Secrets が無いときは設定不足を日本語で伝え、トレースを出さない"""
    at = _app(with_secrets=False, logged_in=False).run()
    assert not at.exception, str(at.exception)
    body = _text(at)
    assert "設定" in body and "SUPABASE_PUBLISHABLE_KEY" in body
    assert "Traceback" not in body and "Error:" not in body
    assert "Step 1" not in body


def test_first_login_shows_only_password_change():
    """初回ログインでは検索画面へ進ませず、パスワード変更画面だけを出す"""
    at = _app(must_change=True).run()
    assert not at.exception, str(at.exception)
    body = _text(at)
    assert "初回ログイン" in body, "初回ログインの案内が無い"
    assert "Step 1" not in body and "Step 2" not in body, "検索画面へ進めている"
    assert at.text_area == [], "URL入力欄が表示されている"
    labels = [getattr(b, "label", "") for b in at.button]
    assert any("パスワードを変更" in label for label in labels), f"変更ボタンが無い: {labels}"
    assert not any("リサーチ" in label for label in labels), "検索ボタンが出ている"


def test_logged_in_user_sees_search_and_logout():
    """ログイン済みなら検索画面と、ログアウト・パスワード変更の導線が出る"""
    at = _app().run()
    body = _text(at)
    assert "Step 1" in body and "Step 2" in body, "検索画面が出ていない"
    assert "student@example.com" in body, "ログイン中の表示が無い"
    labels = [getattr(b, "label", "") for b in at.button]
    assert any("ログアウト" in label for label in labels), "ログアウトが無い"


def test_tokens_are_not_rendered():
    """トークンを画面に出さない"""
    body = _text(_app().run())
    assert "dummy-access" not in body and "dummy-refresh" not in body


if __name__ == "__main__":
    sys.exit(run(dict(globals()), "アプリ描画テスト（AppTest・AI API不使用）"))
