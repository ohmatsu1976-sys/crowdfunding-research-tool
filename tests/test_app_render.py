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

import supabase as supabase_module  # noqa: E402

from _harness import run  # noqa: E402
import auth  # noqa: E402
import candidates_ui  # noqa: E402
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
                 "error", "info", "success", "text"):
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


# ── 候補保存パネル（フェーズ3B）───────────────────────────────────────────────

_KS_URL = "https://www.kickstarter.com/projects/x/candidate-panel-test"


class _FakeCandAuth:
    """set_session だけ持つ最小限の偽auth（フェーズ2Aのfakeと同じ考え方）"""

    def set_session(self, access_token, refresh_token):
        pass


class _FakeCandResponse:
    def __init__(self, data):
        self.data = data


class _FakeCandRpcBuilder:
    def __init__(self, calls, fn, params, already_saved=False, fail=False):
        self._calls = calls
        self._fn = fn
        self._params = params
        self._already_saved = already_saved
        self._fail = fail

    def execute(self):
        self._calls.append((self._fn, self._params))
        if self._fail:
            raise RuntimeError("boom (rpc failed)")
        return _FakeCandResponse({
            "saved_item_id": "sid-1", "product_id": "pid-1",
            "already_saved": self._already_saved,
        })


class _FakeCandClient:
    """Supabase クライアントを模す。実SDKへは一切触れない"""

    def __init__(self, already_saved=False, fail=False):
        self.auth = _FakeCandAuth()
        self.calls = []
        self._already_saved = already_saved
        self._fail = fail

    def rpc(self, fn, params):
        return _FakeCandRpcBuilder(self.calls, fn, params,
                                   already_saved=self._already_saved, fail=self._fail)


def _patched_create_client(fake_client):
    """supabase.create_client を差し替える。呼び出し側で必ず finally で戻すこと"""
    original = supabase_module.create_client
    supabase_module.create_client = lambda url, key: fake_client
    return original


def _element_index(at: AppTest, predicate) -> int:
    for i, element in enumerate(at.main):
        try:
            if predicate(element):
                return i
        except Exception:
            continue
    return -1


def _ks_row(name="候補パネル用商品", url=_KS_URL) -> dict:
    row = _row(name, "A")
    row["掲載URL"] = url
    return row


def test_logged_out_has_no_save_panel():
    """未ログインでは保存パネル（チェックボックス・保存ボタン）が存在しない"""
    at = _old_session([_ks_row()], schema=rschema.SCHEMA_VERSION)
    at.session_state[auth.ACCESS_TOKEN] = ""
    at.session_state[auth.USER_ID] = ""
    at = at.run()
    assert list(at.checkbox) == []
    assert not any("保存" in b.label for b in at.button)


def test_save_panel_absent_when_no_results():
    """検索結果が無ければ保存パネルも出ない"""
    at = _app().run()
    assert not at.exception, str(at.exception)
    assert list(at.checkbox) == []
    assert "候補リストへ保存" not in _text(at)


def test_save_panel_shows_name_and_url_per_product():
    """商品名とURLを確認してから保存できるよう、両方を表示する"""
    at = _seeded_app([_ks_row("SonarPen 2")], ["https://example.com"])
    assert not at.exception, str(at.exception)
    checkboxes = list(at.checkbox)
    assert len(checkboxes) == 1
    assert "SonarPen 2" in checkboxes[0].label
    assert _KS_URL in _text(at)


def test_save_panel_shows_one_checkbox_per_product():
    """複数件あれば商品ごとにチェックボックスが分かれる（一括保存にしない）"""
    rows = [_ks_row("商品A", _KS_URL + "-a"), _ks_row("商品B", _KS_URL + "-b")]
    at = _seeded_app(rows, ["https://example.com"])
    assert not at.exception, str(at.exception)
    checkboxes = list(at.checkbox)
    assert len(checkboxes) == 2
    assert all(cb.value is False for cb in checkboxes), "既定でチェック済みになっている"


def test_save_panel_is_between_summary_and_full_table():
    """保存パネルがサマリー表の直下・「全カラムを表示」の直前にある"""
    at = _seeded_app([_ks_row()], ["https://example.com"])
    assert not at.exception, str(at.exception)

    summary_idx = _element_index(
        at, lambda el: type(el).__name__ == "Markdown"
                       and "サマリー（主要項目）" in str(getattr(el, "value", "")))
    panel_idx = _element_index(
        at, lambda el: type(el).__name__ == "Markdown"
                       and "候補リストへ保存" in str(getattr(el, "value", "")))
    table_idx = _element_index(
        at, lambda el: type(el).__name__ == "Expander"
                       and str(getattr(el, "label", "")) == "全カラムを表示")

    assert summary_idx != -1, "サマリー表が見つからない"
    assert panel_idx != -1, "保存パネルが見つからない"
    assert table_idx != -1, "全カラムを表示が見つからない"
    assert summary_idx < panel_idx < table_idx, (summary_idx, panel_idx, table_idx)


def test_already_saved_checkbox_is_disabled_and_labeled():
    """保存済みの商品はチェックボックスを無効化し、その旨を表示する"""
    at = _seeded_app([_ks_row("保存済み商品")], ["https://example.com"])
    at.session_state[candidates_ui.SAVED_URLS] = {_KS_URL}
    at = at.run()
    checkboxes = list(at.checkbox)
    assert len(checkboxes) == 1
    assert checkboxes[0].disabled is True
    assert "保存済み" in checkboxes[0].label


def test_end_to_end_save_success_via_fake_client():
    """チェック→送信の一連の操作で、実際にRPCが呼ばれ成功が表示される

    supabase.create_client を偽クライアントへ差し替えるため、
    このテストでも外部（実Supabase）へは一切通信しない。
    """
    fake = _FakeCandClient(already_saved=False)
    original = _patched_create_client(fake)
    try:
        at = _seeded_app([_ks_row("エンドツーエンド商品")], ["https://example.com"])
        assert not at.exception, str(at.exception)
        at.checkbox[0].check()
        at = at.run()
        save_button = [b for b in at.button if "保存" in b.label][0]
        at = save_button.click().run()
    finally:
        supabase_module.create_client = original

    assert not at.exception, str(at.exception)
    assert fake.calls, "RPCが呼ばれていない"
    fn, params = fake.calls[0]
    assert fn == "save_candidate"
    assert set(params) == {"p_url_key", "p_source_url", "p_product"}
    assert "user_id" not in params and "user_id" not in params["p_product"]
    assert _KS_URL in at.session_state[candidates_ui.SAVED_URLS]
    assert "マイ候補リストに保存しました" in _text(at)


def test_end_to_end_already_saved_via_fake_client():
    """already_saved=true のときは「すでに保存されています」と表示する"""
    fake = _FakeCandClient(already_saved=True)
    original = _patched_create_client(fake)
    try:
        at = _seeded_app([_ks_row("既存商品")], ["https://example.com"])
        at.checkbox[0].check()
        at = at.run()
        save_button = [b for b in at.button if "保存" in b.label][0]
        at = save_button.click().run()
    finally:
        supabase_module.create_client = original

    assert not at.exception, str(at.exception)
    assert "すでにマイ候補リストに保存されています" in _text(at)


def test_end_to_end_failure_does_not_leak_exception_text():
    """RPCが例外を投げても、例外の内容を画面へ出さない"""
    fake = _FakeCandClient(fail=True)
    original = _patched_create_client(fake)
    try:
        at = _seeded_app([_ks_row("失敗商品")], ["https://example.com"])
        at.checkbox[0].check()
        at = at.run()
        save_button = [b for b in at.button if "保存" in b.label][0]
        at = save_button.click().run()
    finally:
        supabase_module.create_client = original

    assert not at.exception, str(at.exception)
    body = _text(at)
    assert "boom" not in body and "RuntimeError" not in body and "Traceback" not in body
    assert "保存できませんでした" in body


def test_submit_without_selection_shows_warning():
    """何も選択せずに送信すると警告が出て、RPCは呼ばれない"""
    fake = _FakeCandClient()
    original = _patched_create_client(fake)
    try:
        at = _seeded_app([_ks_row("未選択商品")], ["https://example.com"])
        save_button = [b for b in at.button if "保存" in b.label][0]
        at = save_button.click().run()
    finally:
        supabase_module.create_client = original

    assert not at.exception, str(at.exception)
    assert not fake.calls, "何も選択していないのにRPCが呼ばれた"
    assert "選択されていません" in _text(at)


def test_logout_clears_cand_state():
    """ログアウトで cand_ の状態も消える（保存済み記録の見た目上のキャッシュを含む）"""
    at = _seeded_app([_ks_row("ログアウト確認商品")], ["https://example.com"])
    at.session_state[candidates_ui.SAVED_URLS] = {_KS_URL}
    at.session_state[candidates_ui.LAST_MESSAGE] = "残っていたら不具合"
    at = at.run()
    logout = [b for b in at.button if "ログアウト" in b.label][0]
    at = logout.click().run()
    for key in at.session_state.filtered_state:
        assert not str(key).startswith("cand_"), f"{key} が残っている"


# ── マイ候補リスト画面（フェーズ3C）───────────────────────────────────────────

import candidates  # noqa: E402


class _FakeListQuery:
    """saved_items の select/update/delete をチェーン可能に模す（インメモリDB）"""

    def __init__(self, store, kind, payload=None):
        self._store = store
        self._kind = kind
        self._payload = payload
        self._filters = {}
        self._order = None
        self._select_cols = None

    def select(self, columns):
        self._select_cols = columns
        return self

    def eq(self, column, value):
        self._filters[column] = value
        return self

    def order(self, column, desc=False):
        self._order = (column, desc)
        return self

    def _matches(self, row):
        return all(row.get(k) == v for k, v in self._filters.items())

    def execute(self):
        rows = self._store["saved_items"]
        matched = [r for r in rows if self._matches(r)]

        if self._kind == "select":
            if self._order:
                col, desc = self._order
                matched = sorted(matched, key=lambda r: r.get(col, ""), reverse=desc)
            out = []
            for row in matched:
                item = dict(row)
                item["products"] = self._store["products"].get(row["product_id"], {})
                out.append(item)
            return _FakeListResponse(out)

        if self._kind == "update":
            for row in matched:
                row.update(self._payload or {})
            return _FakeListResponse([dict(r) for r in matched])

        if self._kind == "delete":
            self._store["saved_items"] = [r for r in rows if not self._matches(r)]
            return _FakeListResponse([dict(r) for r in matched])

        raise AssertionError(f"未対応: {self._kind}")


class _FakeListResponse:
    def __init__(self, data):
        self.data = data


class _FakeListTable:
    def __init__(self, store):
        self._store = store

    def select(self, columns):
        return _FakeListQuery(self._store, "select").select(columns)

    def update(self, payload):
        return _FakeListQuery(self._store, "update", payload)

    def delete(self):
        return _FakeListQuery(self._store, "delete")


class _FakeListClient:
    def __init__(self, saved_items, products):
        self._store = {"saved_items": [dict(r) for r in saved_items],
                       "products": {p["id"]: p for p in products}}
        self.auth = _FakeCandAuth()

    def table(self, name):
        assert name == "saved_items", name
        return _FakeListTable(self._store)


def _list_fixture():
    products = [{"id": "pid-1", "source_url": _KS_URL, "platform": "Kickstarter",
                "name": "一覧確認商品", "maker": "テストメーカー", "priority": "A"}]
    saved_items = [{"id": "sid-1", "user_id": "test-user", "product_id": "pid-1",
                   "memo": "初期メモ", "status": "候補", "priority_override": None,
                   "archived": False, "saved_at": "2026-01-02T03:04:00",
                   "updated_at": "2026-01-02T03:04:00"}]
    return saved_items, products


def test_logged_out_cannot_reach_candidate_list():
    """未ログインでは画面切替そのものが出ず、マイ候補リストに到達できない"""
    at = _app(logged_in=False).run()
    assert not at.exception, str(at.exception)
    assert list(at.radio) == []
    assert "マイ候補リスト" not in _text(at)


def test_default_view_after_login_is_search():
    """ログイン直後は従来の商品リサーチ画面（マイ候補リストではない）"""
    at = _app().run()
    assert not at.exception, str(at.exception)
    assert at.session_state[candidates_ui.VIEW] == candidates_ui.VIEW_SEARCH
    assert "Step 1" in _text(at)


def test_sidebar_switches_to_candidate_list():
    """サイドバーの切替で「マイ候補リスト」画面へ移れる"""
    saved_items, products = _list_fixture()
    fake = _FakeListClient(saved_items, products)
    original = supabase_module.create_client
    supabase_module.create_client = lambda url, key: fake
    try:
        at = _app().run()
        assert list(at.radio), "画面切替の radio が見つからない"
        at = at.radio[0].set_value(candidates_ui.VIEW_LIST).run()
    finally:
        supabase_module.create_client = original

    assert not at.exception, str(at.exception)
    body = _text(at)
    assert "マイ候補リスト" in body
    assert "Step 1" not in body and "Step 2" not in body


def test_candidate_list_view_does_not_show_search_steps():
    """マイ候補リスト表示中は検索フォーム・AI分析画面が出ない"""
    saved_items, products = _list_fixture()
    fake = _FakeListClient(saved_items, products)
    original = supabase_module.create_client
    supabase_module.create_client = lambda url, key: fake
    try:
        at = _app().run()
        at = at.radio[0].set_value(candidates_ui.VIEW_LIST).run()
    finally:
        supabase_module.create_client = original

    assert not at.exception, str(at.exception)
    assert not any("URL一覧" in ta.label for ta in at.text_area), "検索用のURL入力欄が出ている"


def test_candidate_list_end_to_end_shows_item_and_updates():
    """一覧表示→活動メモ更新までを、偽クライアント経由の一連の操作で確認する

    supabase.create_client を差し替えるため実Supabaseへは通信しない。
    """
    saved_items, products = _list_fixture()
    fake = _FakeListClient(saved_items, products)
    original = supabase_module.create_client
    supabase_module.create_client = lambda url, key: fake
    try:
        at = _app().run()
        at = at.radio[0].set_value(candidates_ui.VIEW_LIST).run()
        assert not at.exception, str(at.exception)
        body = _text(at)
        assert any("一覧確認商品" in e.label for e in at.expander), "商品名が見出しに無い"
        assert _KS_URL in body
        assert "テストメーカー" in body
        assert "2026年1月2日 12:04" in body, "保存日時が日本時間で表示されていない"
        assert "2026-01-02T03:04:00" not in body, "保存日時がUTCのISO文字列のまま出ている"
        assert [ta.value for ta in at.text_area] == ["初期メモ"]

        memo_box = [ta for ta in at.text_area if ta.value == "初期メモ"][0]
        at = memo_box.set_value("更新後のメモ").run()
        update_btn = [b for b in at.button if b.label == "更新する"][0]
        at = update_btn.click().run()
    finally:
        supabase_module.create_client = original

    assert not at.exception, str(at.exception)
    assert "候補情報を更新しました" in _text(at)
    assert fake._store["saved_items"][0]["memo"] == "更新後のメモ"


def test_candidate_list_archive_round_trip():
    """アーカイブ→一覧から消える→「アーカイブ済みも表示」で再表示→解除できる"""
    saved_items, products = _list_fixture()
    fake = _FakeListClient(saved_items, products)
    original = supabase_module.create_client
    supabase_module.create_client = lambda url, key: fake
    try:
        at = _app().run()
        at = at.radio[0].set_value(candidates_ui.VIEW_LIST).run()
        archive_btn = [b for b in at.button if "アーカイブする" in b.label][0]
        at = archive_btn.click().run()
        assert not at.exception, str(at.exception)
        assert "候補をアーカイブしました" in _text(at)
        assert fake._store["saved_items"][0]["archived"] is True
        assert not any("一覧確認商品" in e.label for e in at.expander), (
            "アーカイブ済みが通常一覧に残っている"
        )

        show_archived = [c for c in at.checkbox if "アーカイブ済みも表示" in c.label][0]
        at = show_archived.check().run()
        assert any("一覧確認商品" in e.label for e in at.expander), (
            "アーカイブ済みも表示で出てこない"
        )

        unarchive_btn = [b for b in at.button if "アーカイブを解除" in b.label][0]
        at = unarchive_btn.click().run()
        assert "候補を一覧に戻しました" in _text(at)
        assert fake._store["saved_items"][0]["archived"] is False
    finally:
        supabase_module.create_client = original


def test_candidate_list_delete_requires_confirmation():
    """削除前に確認チェックが必須（1回のクリックだけで即時削除しない）"""
    saved_items, products = _list_fixture()
    fake = _FakeListClient(saved_items, products)
    original = supabase_module.create_client
    supabase_module.create_client = lambda url, key: fake
    try:
        at = _app().run()
        at = at.radio[0].set_value(candidates_ui.VIEW_LIST).run()
        delete_btn = [b for b in at.button if b.label == "削除する"][0]
        assert delete_btn.disabled is True, "確認前なのに削除ボタンが押せる"

        confirm = [c for c in at.checkbox if "を削除する" in c.label][0]
        at = confirm.check().run()
        delete_btn = [b for b in at.button if b.label == "削除する"][0]
        assert delete_btn.disabled is False

        at = delete_btn.click().run()
        assert not at.exception, str(at.exception)
        assert "候補リストから削除しました" in _text(at)
        assert fake._store["saved_items"] == [], "saved_itemsが削除されていない"
        assert "pid-1" in fake._store["products"], "productsの共有行まで消えている"
    finally:
        supabase_module.create_client = original


def test_candidate_list_view_and_widgets_clear_on_logout():
    """画面切替・入力中の値・削除確認状態も、ログアウトですべて消える"""
    saved_items, products = _list_fixture()
    fake = _FakeListClient(saved_items, products)
    original = supabase_module.create_client
    supabase_module.create_client = lambda url, key: fake
    try:
        at = _app().run()
        at = at.radio[0].set_value(candidates_ui.VIEW_LIST).run()
        assert not at.exception, str(at.exception)
        at.session_state["cand_delconfirm_sid-1"] = True
        at = at.run()
        logout = [b for b in at.button if "ログアウト" in b.label]
        assert logout, "ログアウトボタンが見つからない"
        at = logout[0].click().run()
    finally:
        supabase_module.create_client = original

    assert not at.exception, str(at.exception)
    for key in at.session_state.filtered_state:
        assert not str(key).startswith("cand_"), f"{key} が残っている"


# ── 管理者ビュー（フェーズ3D）───────────────────────────────────────────────────

class _FakeAdminResponse:
    def __init__(self, data, count=None):
        self.data = data
        self.count = count


class _FakeAdminQuery:
    def __init__(self, store, table):
        self._store = store
        self._table = table
        self._count_mode = None
        self._filters = {}
        self._order = None
        self._range = None

    def select(self, columns, count=None):
        self._count_mode = count
        return self

    def eq(self, column, value):
        self._filters[column] = value
        return self

    def order(self, column, desc=False):
        self._order = (column, desc)
        return self

    def range(self, start, end):
        self._range = (start, end)
        return self

    def _matches(self, row):
        return all(row.get(k) == v for k, v in self._filters.items())

    def execute(self):
        if self._store.get("fail_tables"):
            raise RuntimeError("connection refused: db.example.internal:5432")

        rows = self._store["tables"][self._table]
        matched = [r for r in rows if self._matches(r)]

        if self._table == "profiles":
            return _FakeAdminResponse([dict(r) for r in matched])

        if self._order:
            col, desc = self._order
            matched = sorted(matched, key=lambda r: r.get(col, ""), reverse=desc)
        total = len(matched)
        if self._range:
            start, end = self._range
            page_rows = matched[start:end + 1]
        else:
            page_rows = matched

        out = []
        for row in page_rows:
            item = dict(row)
            product = self._store["products"].get(row.get("product_id"), {})
            item["products"] = {
                "id": product.get("id", ""), "source_url": product.get("source_url", ""),
                "platform": product.get("platform", ""), "name": product.get("name", ""),
                "maker": product.get("maker", ""), "priority": product.get("priority", ""),
            }
            out.append(item)
        return _FakeAdminResponse(out, count=total if self._count_mode else None)


class _FakeAdminTable:
    def __init__(self, store, name):
        self._store = store
        self._name = name

    def select(self, columns, count=None):
        return _FakeAdminQuery(self._store, self._name).select(columns, count=count)


class _FakeAdminRpcBuilder:
    def __init__(self, store, fn):
        self._store = store
        self._fn = fn

    def execute(self):
        if self._store.get("fail_rpc"):
            raise RuntimeError("connection refused: db.example.internal:5432")
        count = self._store.get("is_admin_call_count", 0) + 1
        self._store["is_admin_call_count"] = count
        flip_after = self._store.get("is_admin_flip_after")
        if flip_after is not None and count > flip_after:
            return _FakeAdminResponse(False)
        return _FakeAdminResponse(self._store.get("is_admin", False))


class _FakeAdminAppClient:
    """管理者ビューのAppTest用の偽クライアント（saved_items/products/profiles/is_admin）

    fail_tables: saved_items/profiles の select が例外を投げる
    fail_rpc: is_admin RPC が例外を投げる
    is_admin_flip_after: is_admin RPCがN回目までは is_admin の値を返し、
        それ以降は false を返す（描画の下見とrender_admin_screenの再確認が
        食い違う状況を再現するため）
    """

    def __init__(self, saved_items, products, profiles, is_admin=True,
                fail_tables=False, fail_rpc=False):
        self.auth = _FakeCandAuth()
        self._store = {
            "tables": {
                "saved_items": [dict(r) for r in saved_items],
                "profiles": [dict(r) for r in profiles],
            },
            "products": {p["id"]: p for p in products},
            "is_admin": is_admin,
            "fail_tables": fail_tables,
            "fail_rpc": fail_rpc,
        }

    def table(self, name):
        return _FakeAdminTable(self._store, name)

    def rpc(self, fn, params=None):
        return _FakeAdminRpcBuilder(self._store, fn)


def _admin_fixture(extra_saved_items=None):
    products = [
        {"id": "pid-a", "source_url": "https://www.kickstarter.com/projects/x/a",
         "platform": "Kickstarter", "name": "管理者確認商品A", "maker": "メーカーA", "priority": "A"},
        {"id": "pid-b", "source_url": "https://www.kickstarter.com/projects/x/b",
         "platform": "Kickstarter", "name": "管理者確認商品B", "maker": "メーカーB", "priority": "B"},
    ]
    saved_items = [
        {"id": "sid-admin-1", "user_id": "test-user", "product_id": "pid-a",
         "memo": "管理者自身のメモ", "status": "候補", "priority_override": None,
         "archived": False, "saved_at": "2026-01-03T00:00:00+00:00"},
        {"id": "sid-admin-2", "user_id": "other-user", "product_id": "pid-b",
         "memo": "他の受講生のメモ", "status": "交渉中", "priority_override": "B",
         "archived": False, "saved_at": "2026-01-02T00:00:00+00:00"},
    ]
    if extra_saved_items:
        saved_items.extend(extra_saved_items)
    profiles = [
        {"user_id": "test-user", "email": "student@example.com", "display_name": "テスト利用者"},
        {"user_id": "other-user", "email": "other@example.com", "display_name": ""},
    ]
    return saved_items, products, profiles


def test_non_admin_does_not_see_admin_view_option():
    """is_admin()が偽なら、画面切替に管理者ビューが出ない"""
    saved_items, products, profiles = _admin_fixture()
    fake = _FakeAdminAppClient(saved_items, products, profiles, is_admin=False)
    original = supabase_module.create_client
    supabase_module.create_client = lambda url, key: fake
    try:
        at = _app().run()
    finally:
        supabase_module.create_client = original

    assert not at.exception, str(at.exception)
    assert list(at.radio[0].options) == ["🔍 商品をリサーチ", "⭐ マイ候補リスト"]
    assert "管理者ビュー" not in _text(at)


def test_admin_sees_three_view_options():
    """is_admin()が真なら、画面切替に管理者ビューが増える"""
    saved_items, products, profiles = _admin_fixture()
    fake = _FakeAdminAppClient(saved_items, products, profiles, is_admin=True)
    original = supabase_module.create_client
    supabase_module.create_client = lambda url, key: fake
    try:
        at = _app().run()
    finally:
        supabase_module.create_client = original

    assert not at.exception, str(at.exception)
    assert list(at.radio[0].options) == \
        ["🔍 商品をリサーチ", "⭐ マイ候補リスト", "🛡️ 管理者ビュー"]


def test_admin_initial_view_is_still_search():
    """管理者であっても、ログイン直後の初期画面は商品リサーチのまま"""
    saved_items, products, profiles = _admin_fixture()
    fake = _FakeAdminAppClient(saved_items, products, profiles, is_admin=True)
    original = supabase_module.create_client
    supabase_module.create_client = lambda url, key: fake
    try:
        at = _app().run()
    finally:
        supabase_module.create_client = original

    assert not at.exception, str(at.exception)
    assert at.session_state[candidates_ui.VIEW] == candidates_ui.VIEW_SEARCH
    assert "Step 1" in _text(at)


def test_admin_view_shows_all_users_candidates():
    """管理者ビューでは全利用者の候補が、他人の分も含めて表示される"""
    saved_items, products, profiles = _admin_fixture()
    fake = _FakeAdminAppClient(saved_items, products, profiles, is_admin=True)
    original = supabase_module.create_client
    supabase_module.create_client = lambda url, key: fake
    try:
        at = _app().run()
        at = at.radio[0].set_value(candidates_ui.VIEW_ADMIN).run()
    finally:
        supabase_module.create_client = original

    assert not at.exception, str(at.exception)
    labels = [e.label for e in at.expander]
    assert any("管理者確認商品A" in lbl for lbl in labels)
    assert any("管理者確認商品B" in lbl for lbl in labels)
    assert any("テスト利用者" in lbl for lbl in labels)
    assert any("other@example.com" in lbl for lbl in labels), \
        "表示名未設定の利用者がメールアドレスで識別できない"


def test_admin_view_shows_required_fields():
    saved_items, products, profiles = _admin_fixture()
    fake = _FakeAdminAppClient(saved_items, products, profiles, is_admin=True)
    original = supabase_module.create_client
    supabase_module.create_client = lambda url, key: fake
    try:
        at = _app().run()
        at = at.radio[0].set_value(candidates_ui.VIEW_ADMIN).run()
    finally:
        supabase_module.create_client = original

    body = _text(at)
    assert not at.exception, str(at.exception)
    assert "student@example.com" in body
    assert "https://www.kickstarter.com/projects/x/a" in body
    assert "Kickstarter" in body
    assert "メーカーA" in body
    assert "候補" in body
    assert "管理者自身のメモ" in body
    assert "2026年1月3日 9:00" in body, "保存日時が日本時間で表示されていない"


def test_admin_view_is_read_only_no_edit_or_delete_widgets():
    """管理者ビューには更新・削除・アーカイブのボタンや編集可能な入力欄が無い"""
    saved_items, products, profiles = _admin_fixture()
    fake = _FakeAdminAppClient(saved_items, products, profiles, is_admin=True)
    original = supabase_module.create_client
    supabase_module.create_client = lambda url, key: fake
    try:
        at = _app().run()
        at = at.radio[0].set_value(candidates_ui.VIEW_ADMIN).run()
    finally:
        supabase_module.create_client = original

    assert not at.exception, str(at.exception)
    button_labels = [b.label for b in at.button]
    for forbidden in ("更新する", "削除する", "アーカイブする", "アーカイブを解除"):
        assert forbidden not in button_labels, f"{forbidden} ボタンが管理者ビューにある"
    assert list(at.text_area) == [], "編集可能な活動メモ欄が管理者ビューにある"
    # 絞り込み用のselectbox(利用者・ステータス・優先度)以外に選択式の編集欄が無いこと
    assert len(at.selectbox) == 3, "絞り込み以外のselectboxが増えている（編集欄の疑い）"


def test_admin_view_filters_by_user():
    saved_items, products, profiles = _admin_fixture()
    fake = _FakeAdminAppClient(saved_items, products, profiles, is_admin=True)
    original = supabase_module.create_client
    supabase_module.create_client = lambda url, key: fake
    try:
        at = _app().run()
        at = at.radio[0].set_value(candidates_ui.VIEW_ADMIN).run()
        user_select = at.selectbox[0]
        at = user_select.set_value("other-user").run()
    finally:
        supabase_module.create_client = original

    assert not at.exception, str(at.exception)
    labels = [e.label for e in at.expander]
    assert any("管理者確認商品B" in lbl for lbl in labels)
    assert not any("管理者確認商品A" in lbl for lbl in labels), \
        "利用者で絞り込んでも他人の候補が出ている"


def test_admin_view_excludes_archived_by_default_and_toggle_shows_it():
    saved_items, products, profiles = _admin_fixture(extra_saved_items=[
        {"id": "sid-archived", "user_id": "other-user", "product_id": "pid-a",
         "memo": "アーカイブ済み", "status": "見送り", "priority_override": None,
         "archived": True, "saved_at": "2026-01-04T00:00:00+00:00"},
    ])
    fake = _FakeAdminAppClient(saved_items, products, profiles, is_admin=True)
    original = supabase_module.create_client
    supabase_module.create_client = lambda url, key: fake
    try:
        at = _app().run()
        at = at.radio[0].set_value(candidates_ui.VIEW_ADMIN).run()
        assert not at.exception, str(at.exception)
        assert "アーカイブ済み" not in _text(at)

        show_archived = [c for c in at.checkbox if "アーカイブ済みも表示" in c.label][0]
        at = show_archived.check().run()
    finally:
        supabase_module.create_client = original

    assert not at.exception, str(at.exception)
    assert "アーカイブ済み" in _text(at)


def test_admin_view_pagination_controls_work():
    """50件を超えたら次のページへ進める（1ページ50件のページング）"""
    extra = [
        {"id": f"sid-extra-{i}", "user_id": "other-user", "product_id": "pid-a",
         "memo": f"追加{i}", "status": "候補", "priority_override": None,
         "archived": False, "saved_at": f"2026-02-{(i % 27) + 1:02d}T00:00:00+00:00"}
        for i in range(candidates.ADMIN_PAGE_SIZE - 1)  # 既存2件と合わせて丁度51件
    ]
    saved_items, products, profiles = _admin_fixture(extra_saved_items=extra)
    assert len(saved_items) == candidates.ADMIN_PAGE_SIZE + 1
    fake = _FakeAdminAppClient(saved_items, products, profiles, is_admin=True)
    original = supabase_module.create_client
    supabase_module.create_client = lambda url, key: fake
    try:
        at = _app().run()
        at = at.radio[0].set_value(candidates_ui.VIEW_ADMIN).run()
        assert not at.exception, str(at.exception)
        assert f"{candidates.ADMIN_PAGE_SIZE + 1}件中" in _text(at)
        assert "ページ 1 / 2" in _text(at)
        next_btn = [b for b in at.button if "次へ" in b.label][0]
        assert next_btn.disabled is False
        at = next_btn.click().run()
        assert not at.exception, str(at.exception)
        assert "ページ 2 / 2" in _text(at)
        next_btn2 = [b for b in at.button if "次へ" in b.label][0]
        assert next_btn2.disabled is True, "最終ページなのに次へが押せる"
    finally:
        supabase_module.create_client = original


def test_admin_view_refuses_when_second_is_admin_check_fails():
    """サイドバーの下見でtrueでも、画面本体の再確認がfalseなら内容を表示しない

    ブラウザに前回セッションのVIEW_ADMINが残っている状態を想定する
    （サイドバーの1回目の is_admin() は真、render_admin_screen 側の
    2回目の再確認では偽、という食い違いを偽クライアントで再現する）。
    """
    saved_items, products, profiles = _admin_fixture()
    fake = _FakeAdminAppClient(saved_items, products, profiles, is_admin=True)
    fake._store["is_admin_flip_after"] = 1
    original = supabase_module.create_client
    supabase_module.create_client = lambda url, key: fake
    try:
        at = _app()
        at.session_state[candidates_ui.VIEW] = candidates_ui.VIEW_ADMIN
        at = at.run()
    finally:
        supabase_module.create_client = original

    assert not at.exception, str(at.exception)
    body = _text(at)
    assert "管理者確認商品A" not in body
    assert "管理者確認商品B" not in body
    assert "管理者専用です" in body


def test_admin_view_failure_shows_safe_message_only():
    """is_admin()は真でも、一覧取得自体が失敗したら安全な文言だけを出す"""
    saved_items, products, profiles = _admin_fixture()
    fake = _FakeAdminAppClient(saved_items, products, profiles, is_admin=True,
                               fail_tables=True)
    original = supabase_module.create_client
    supabase_module.create_client = lambda url, key: fake
    try:
        at = _app().run()
        at = at.radio[0].set_value(candidates_ui.VIEW_ADMIN).run()
    finally:
        supabase_module.create_client = original

    assert not at.exception, str(at.exception)
    body = _text(at)
    assert candidates.ADMIN_LIST_FAILED in body
    assert "runtimeerror" not in body.lower()
    assert "connection refused" not in body.lower()
    assert "db.example.internal" not in body.lower()


def test_admin_view_state_clears_on_logout():
    saved_items, products, profiles = _admin_fixture()
    fake = _FakeAdminAppClient(saved_items, products, profiles, is_admin=True)
    original = supabase_module.create_client
    supabase_module.create_client = lambda url, key: fake
    try:
        at = _app().run()
        at = at.radio[0].set_value(candidates_ui.VIEW_ADMIN).run()
        assert not at.exception, str(at.exception)
        at.session_state[candidates_ui.ADMIN_FILTER_STATUS] = "交渉中"
        at = at.run()
        logout = [b for b in at.button if "ログアウト" in b.label]
        assert logout, "ログアウトボタンが見つからない"
        at = logout[0].click().run()
    finally:
        supabase_module.create_client = original

    assert not at.exception, str(at.exception)
    for key in at.session_state.filtered_state:
        assert not str(key).startswith("cand_"), f"{key} が残っている"


def test_my_list_screen_edit_features_still_work_after_admin_view_added():
    """管理者ビュー追加後も、マイ候補リストの本人編集機能が壊れていない"""
    saved_items, products = _list_fixture()
    fake = _FakeListClient(saved_items, products)
    original = supabase_module.create_client
    supabase_module.create_client = lambda url, key: fake
    try:
        at = _app().run()
        at = at.radio[0].set_value(candidates_ui.VIEW_LIST).run()
        assert not at.exception, str(at.exception)
        memo_box = [ta for ta in at.text_area if ta.value == "初期メモ"][0]
        at = memo_box.set_value("管理者ビュー追加後の更新").run()
        update_btn = [b for b in at.button if b.label == "更新する"][0]
        at = update_btn.click().run()
    finally:
        supabase_module.create_client = original

    assert not at.exception, str(at.exception)
    assert "候補情報を更新しました" in _text(at)
    assert fake._store["saved_items"][0]["memo"] == "管理者ビュー追加後の更新"


if __name__ == "__main__":
    sys.exit(run(dict(globals()), "アプリ描画テスト（AppTest・AI API不使用）"))
