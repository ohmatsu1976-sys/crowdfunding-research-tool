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
import search_state as sstate  # noqa: E402

from streamlit.testing.v1 import AppTest  # noqa: E402

APP = str(Path(__file__).resolve().parent.parent / "streamlit_app.py")
TIMEOUT = 60

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
    at = AppTest.from_file(APP, default_timeout=TIMEOUT)
    at.session_state[sstate.RESULTS] = rows
    at.session_state[sstate.QUERY] = query
    at.session_state[sstate.EXECUTED_AT] = datetime(2026, 1, 2, 3, 4)
    at.session_state[sstate.STATUS] = status
    at.session_state[sstate.ERROR] = error
    at.session_state[sstate.FAILED_URLS] = []
    at.session_state[sstate.LOG] = []
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
    at = AppTest.from_file(APP, default_timeout=TIMEOUT).run()
    assert not at.exception, str(at.exception)


def test_initial_view_has_no_results():
    """初回表示では検索結果セクションが出ない"""
    at = AppTest.from_file(APP, default_timeout=TIMEOUT).run()
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
    at = AppTest.from_file(APP, default_timeout=TIMEOUT)
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


if __name__ == "__main__":
    sys.exit(run(dict(globals()), "アプリ描画テスト（AppTest・AI API不使用）"))
