# -*- coding: utf-8 -*-
"""サミット特典モード（制限版画面）の決定論テスト

実行: python tests/test_summit.py

商品取得・公式サイト探索・連絡先取得は外部通信を伴うため、summit_ui が
呼び出す関数をすべて偽関数へ差し替えてテストする（通信するのは
test_network.py だけ、という方針に合わせる）。AI分析はclient=Noneで
呼ぶため、Anthropicへも通信しない（analyze_with_claudeの既定の挙動）。
"""

import sys

from _harness import run  # noqa: E402
import summit_ui  # noqa: E402


def _patch(**funcs):
    """summit_ui モジュールの関数をまとめて差し替え、元に戻す関数を返す"""
    originals = {name: getattr(summit_ui, name) for name in funcs}
    for name, fn in funcs.items():
        setattr(summit_ui, name, fn)

    def _restore():
        for name, fn in originals.items():
            setattr(summit_ui, name, fn)
    return _restore


def _never_called(label):
    def _fn(*args, **kwargs):
        raise AssertionError(f"{label} が呼ばれるべきではない場面で呼ばれた")
    return _fn


# ── URL正規化 ────────────────────────────────────────────────────────────────

def test_normalize_url_strips_query_and_trailing_slash():
    assert summit_ui._normalize_url("https://example.com/p/?x=1") == "https://example.com/p"


def test_normalize_url_rejects_non_http():
    assert summit_ui._normalize_url("not a url") == ""


def test_normalize_url_rejects_empty_or_blank():
    assert summit_ui._normalize_url("") == ""
    assert summit_ui._normalize_url("   ") == ""


# ── _run_single: 不正URL・取得失敗 ────────────────────────────────────────────

def test_run_single_rejects_invalid_url_without_fetching():
    restore = _patch(fetch_project_from_url=_never_called("fetch_project_from_url"))
    try:
        row, error = summit_ui._run_single("invalid", None)
    finally:
        restore()
    assert row is None
    assert error == summit_ui.INVALID_URL


def test_run_single_reports_fetch_failure_without_raising():
    restore = _patch(fetch_project_from_url=lambda url: None)
    try:
        row, error = summit_ui._run_single("https://www.kickstarter.com/projects/x/y", None)
    finally:
        restore()
    assert row is None
    assert error == summit_ui.FETCH_FAILED


def test_run_single_handles_fetch_exception_without_leaking_detail():
    def _boom(url):
        raise RuntimeError("network exploded: db.example.internal")
    restore = _patch(fetch_project_from_url=_boom)
    try:
        row, error = summit_ui._run_single("https://www.kickstarter.com/projects/x/y", None)
    finally:
        restore()
    assert row is None
    assert error == summit_ui.FETCH_FAILED
    assert "network exploded" not in error
    assert "db.example.internal" not in error


def test_run_single_builds_row_with_client_none_without_network():
    """AI未接続（client=None）でも例外にならず、既定値で行を組み立てる"""
    p = {"name": "商品X", "maker": "メーカーX", "platform": "Kickstarter",
         "genre": "ガジェット", "description": "説明文",
         "url": "https://www.kickstarter.com/projects/x/y",
         "_creator_websites": ["https://maker.example.com"]}
    restore = _patch(
        fetch_project_from_url=lambda url: dict(p),
        get_contact_info=lambda site_url, brand="": {
            "official_url": site_url, "email": "a@example.com", "contact_form": "未確認",
            "facebook": "未確認", "instagram": "未確認", "linkedin": "未確認"},
    )
    try:
        row, error = summit_ui._run_single("https://www.kickstarter.com/projects/x/y", None)
    finally:
        restore()
    assert error == ""
    assert row["商品名"] == "商品X"
    assert row["メーカー名"] == "メーカーX"
    assert row["公式サイトURL"] == "https://maker.example.com"
    assert row["メールアドレス"] == "a@example.com"


# ── _resolve_contact: 探索の優先順位 ──────────────────────────────────────────

def test_resolve_contact_uses_creator_websites_first():
    restore = _patch(
        find_official_site=_never_called("find_official_site"),
        find_creator_site=_never_called("find_creator_site"),
        get_contact_info=lambda site_url, brand="": {
            "official_url": site_url, "email": "a@example.com", "contact_form": "未確認",
            "facebook": "未確認", "instagram": "未確認", "linkedin": "未確認"},
    )
    try:
        p = {"url": "https://www.kickstarter.com/projects/x/y", "maker": "メーカー",
             "_creator_websites": ["https://maker.example.com"]}
        contact = summit_ui._resolve_contact(p)
    finally:
        restore()
    assert contact["email"] == "a@example.com"


def test_resolve_contact_falls_back_to_find_official_site():
    restore = _patch(
        find_official_site=lambda url, maker: ["https://found.example.com"],
        find_creator_site=_never_called("find_creator_site"),
        get_contact_info=lambda site_url, brand="": {
            "official_url": site_url, "email": "未確認", "contact_form": "未確認",
            "facebook": "未確認", "instagram": "未確認", "linkedin": "未確認"},
    )
    try:
        p = {"url": "https://www.kickstarter.com/projects/x/y", "maker": "メーカー"}
        contact = summit_ui._resolve_contact(p)
    finally:
        restore()
    assert contact["official_url"] == "https://found.example.com"


def test_resolve_contact_falls_back_to_find_creator_site():
    restore = _patch(
        find_official_site=lambda url, maker: [],
        find_creator_site=lambda slug, name, maker: "https://creator.example.com",
        get_contact_info=lambda site_url, brand="": {
            "official_url": site_url, "email": "未確認", "contact_form": "未確認",
            "facebook": "未確認", "instagram": "未確認", "linkedin": "未確認"},
    )
    try:
        p = {"url": "https://www.kickstarter.com/projects/x/y", "maker": "メーカー",
             "_creator_slug": "slug", "name": "name"}
        contact = summit_ui._resolve_contact(p)
    finally:
        restore()
    assert contact["official_url"] == "https://creator.example.com"


def test_resolve_contact_kickstarter_domain_skips_scraping():
    restore = _patch(
        find_official_site=lambda url, maker: [],
        find_creator_site=lambda slug, name, maker: "",
        get_contact_info=_never_called("get_contact_info"),
    )
    try:
        p = {"url": "https://www.kickstarter.com/projects/x/y",
             "_creator_websites": ["https://www.kickstarter.com/projects/x/y"]}
        contact = summit_ui._resolve_contact(p)
    finally:
        restore()
    assert contact["official_url"] == "https://www.kickstarter.com/projects/x/y"


def test_resolve_contact_returns_unconfirmed_when_nothing_found():
    restore = _patch(
        find_official_site=lambda url, maker: [],
        find_creator_site=lambda slug, name, maker: "",
        get_contact_info=lambda site_url, brand="": {},
    )
    try:
        contact = summit_ui._resolve_contact({"url": "https://www.kickstarter.com/projects/x/y"})
    finally:
        restore()
    assert contact["email"] == summit_ui._UNCONFIRMED
    assert contact["official_url"] == ""


# ── 表示用の正規化（list・文字列・空値の揺れを吸収する）─────────────────────────
#
# analyze_with_claude() の "日本販売時の訴求ポイント"（appeal_points）・
# "注意点・懸念点"（concerns）はJSON配列で返ることも、箇条書き風の1つの
# 文字列で返ることもある。生のlistをst.write()へ渡すとStreamlitが角括弧・
# 0:/1:のインデックス・引用符付きのオブジェクト表示にしてしまうため
# （本番で報告された不具合）、_normalize_text_list() が両方を同じ
# 「ふつうの文字列のリスト」へそろえることを確認する。

def test_normalize_text_list_from_plain_string():
    """商品概要が存在する場合（通常の1文の文字列）"""
    assert summit_ui._normalize_text_list("ふつうの説明文です。") == ["ふつうの説明文です。"]


def test_normalize_text_list_single_sentence_with_number_inside_is_not_split():
    """文中の数字（箇条書き記号ではない）を箇条書きとして分割しない"""
    assert summit_ui._normalize_text_list("普通の1文の説明です。") == ["普通の1文の説明です。"]


def test_normalize_text_list_from_list_of_strings():
    """箇条書きがlistの場合（Claudeがそのまま配列で返したケース）"""
    assert summit_ui._normalize_text_list(["Fast setup", "Compact design"]) == \
        ["Fast setup", "Compact design"]


def test_normalize_text_list_splits_bullet_style_string():
    """箇条書きが文字列の場合（「・」区切りの1つの文字列で返ったケース）"""
    assert summit_ui._normalize_text_list("・Fast setup\n・Compact design") == \
        ["Fast setup", "Compact design"]


def test_normalize_text_list_splits_numbered_string():
    """箇条書きが文字列の場合（番号付きの1つの文字列で返ったケース）"""
    assert summit_ui._normalize_text_list("1. Fast setup\n2) Compact design") == \
        ["Fast setup", "Compact design"]


def test_normalize_text_list_strips_outer_quotes_only():
    """各項目の外側の引用符だけを除去する"""
    assert summit_ui._normalize_text_list(['"Fast setup"', "'Compact design'"]) == \
        ["Fast setup", "Compact design"]


def test_normalize_text_list_keeps_internal_quotes():
    """項目内部の引用符は取り除かない（外側だけを対象にする）"""
    assert summit_ui._normalize_text_list(['He said "hello" to me']) == \
        ['He said "hello" to me']


def test_normalize_text_list_none_and_empty_return_empty():
    """空・Noneの場合は空リストになる（呼び出し側が案内文を出す）"""
    assert summit_ui._normalize_text_list(None) == []
    assert summit_ui._normalize_text_list("") == []
    assert summit_ui._normalize_text_list("   ") == []
    assert summit_ui._normalize_text_list([]) == []


def test_normalize_text_list_skips_none_and_blank_items_in_list():
    assert summit_ui._normalize_text_list(["a", None, "", "   ", "b"]) == ["a", "b"]


def test_normalize_text_list_does_not_raise_on_non_string_items():
    """数値など文字列以外の要素が混ざっても例外にしない"""
    assert summit_ui._normalize_text_list([1, "a", 2.5]) == ["1", "a", "2.5"]


# ── 再発防止：バグの温床だった生のst.write(list/value)が無いこと ─────────────

def test_old_broken_show_field_helper_is_removed():
    """バグの温床だった_show_field（list値をそのままst.write）が存在しない"""
    assert not hasattr(summit_ui, "_show_field")


def test_render_never_passes_raw_value_to_st_write():
    """st.write(value)のように生の値をそのまま渡していない（配列表示事故の再発防止）"""
    import pathlib
    text = pathlib.Path(summit_ui.__file__).read_text(encoding="utf-8")
    assert "st.write(value" not in text
    assert "st.write(row.get(" not in text


def test_summit_uses_correct_build_row_keys():
    """summit_ui.pyが参照しているキーが、build_row()の実際のキー名と一致する"""
    import pathlib
    import research_crowdfunding as rc
    for key in ("商品の特徴", "日本で売れそうな理由", "日本販売時の訴求ポイント", "注意点・懸念点"):
        assert key in rc.CSV_FIELDS, f"{key} はbuild_row()の列ではない"
    text = pathlib.Path(summit_ui.__file__).read_text(encoding="utf-8")
    for key in ("商品の特徴", "日本で売れそうな理由", "日本販売時の訴求ポイント", "注意点・懸念点"):
        assert f'"{key}"' in text, f"{key} を参照していない"


# ── 非表示にすべき機能を参照していないこと（静的検査）────────────────────────

def test_summit_module_never_builds_outreach_email():
    """営業メール生成を参照していない"""
    import pathlib
    text = pathlib.Path(summit_ui.__file__).read_text(encoding="utf-8")
    for forbidden in ("approach_subject", "approach_body", "build_approach_email",
                      "営業メール件名", "営業メール本文"):
        assert forbidden not in text, f"{forbidden} が summit_ui.py にある"


def test_summit_module_never_saves_candidates():
    """保存機能（マイ候補リスト）を一切参照していない"""
    import pathlib
    text = pathlib.Path(summit_ui.__file__).read_text(encoding="utf-8")
    for forbidden in ("import candidates", "save_candidate", "save_one", "save_many"):
        assert forbidden not in text, f"{forbidden} が summit_ui.py にある"


def test_summit_module_never_shows_advanced_analysis_fields():
    """優先度判定・判定の確度・調達額等の「高度分析」を表示しない"""
    import pathlib
    text = pathlib.Path(summit_ui.__file__).read_text(encoding="utf-8")
    for forbidden in ('"優先度"', '"判定の確度"', '"調達額(円)"', '"調達額(USD)"',
                      '"支援者数"', '"競合する日本商品"', '"優先度の理由"'):
        assert forbidden not in text, f"{forbidden} が summit_ui.py にある"


def test_summit_result_state_is_overwritten_not_accumulated():
    """今回の1件だけを保持する（履歴用に追記せず、毎回上書きする）"""
    import pathlib
    text = pathlib.Path(summit_ui.__file__).read_text(encoding="utf-8")
    assert "st.session_state[RESULT_KEY] = row" in text
    assert "RESULT_KEY].append" not in text


def test_clear_state_removes_result_and_error():
    state = {summit_ui.RESULT_KEY: {"商品名": "x"}, summit_ui.ERROR_KEY: "エラー",
             "other_key": "残ってよい"}
    summit_ui.clear_state(state)
    assert summit_ui.RESULT_KEY not in state
    assert summit_ui.ERROR_KEY not in state
    assert state["other_key"] == "残ってよい"


def test_auth_ui_clears_summit_state_on_logout():
    """auth_ui.py のログアウト経路すべてでsummit_ui.clear_stateも呼ばれる"""
    import pathlib
    path = pathlib.Path(__file__).resolve().parent.parent / "auth_ui.py"
    text = path.read_text(encoding="utf-8")
    assert text.count("summit_ui.clear_state(st.session_state)") == 3, (
        "ログアウト・初回ログアウト・パスワード変更成功の3箇所で呼ばれるはず"
    )


def test_summit_title_and_caption_match_spec():
    assert summit_ui.TITLE == "海外ブランド AIリサーチアシスタント｜サミット特典版"
    assert summit_ui.CAPTION == (
        "気になる海外商品のURLを入力してください。日本市場との相性や、"
        "売れそうな理由・懸念点をAIが簡易分析します。"
    )


if __name__ == "__main__":
    sys.exit(run(dict(globals()), "サミット特典モードテスト（偽関数・通信なし）"))
