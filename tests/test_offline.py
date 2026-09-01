# -*- coding: utf-8 -*-
"""ネットワーク・外部AI APIを使わない決定論テスト

実行: python tests/test_offline.py
外部サイトの状態に依存しないため、いつ実行しても同じ結果になる。
"""

import csv
import io
import sys

from _harness import run  # noqa: E402
import research_crowdfunding as r  # noqa: E402
from summary_table import build_summary_html  # noqa: E402

KS = "https://www.kickstarter.com/projects/{}/{}"
SITPACK = ("rest", "sitpack-zen-the-worlds-most-compact-chair-perfect")
NEX = ("niid", "nex-slim-wallet-1cm-magnetic-rfid-and-tap")


# ── CSVの構造 ──────────────────────────────────────────────────────────────────

def test_csv_fields_count():
    """CSV_FIELDS が24列である"""
    assert len(r.CSV_FIELDS) == 24, f"実際は {len(r.CSV_FIELDS)} 列"


def test_csv_fields_order():
    """「判定の確度」が「優先度」の直後にある"""
    assert r.CSV_FIELDS[18:20] == ["優先度", "判定の確度"], str(r.CSV_FIELDS[18:20])


def test_csv_fields_no_missing():
    """既存の主要列が欠落していない"""
    for col in ("商品名", "メーカー名", "掲載URL", "調達額(円)", "支援者数",
                "公式サイトURL", "メールアドレス", "優先度", "営業メール本文(英語)"):
        assert col in r.CSV_FIELDS, f"{col} が無い"


def test_build_row_matches_csv_fields():
    """build_row の出力キーが CSV_FIELDS と一致する"""
    row = r.build_row({"name": "X", "raised_usd": 100, "url": "u"}, {}, {})
    assert set(row) == set(r.CSV_FIELDS), str(set(r.CSV_FIELDS) ^ set(row))


def test_csv_roundtrip_is_excel_compatible():
    """CSVがBOM付きで書き出され、24列のまま読み戻せる"""
    import pandas as pd
    rows = [r.build_row({"name": "A", "raised_usd": 1, "url": "u"}, {}, {}),
            r.build_row({"name": "B", "raised_usd": 0, "url": "u"}, {}, {})]
    data = pd.DataFrame(rows, columns=r.CSV_FIELDS).to_csv(
        index=False, encoding="utf-8-sig").encode("utf-8-sig")
    assert data.startswith(b"\xef\xbb\xbf"), "BOMが無い"
    text = data.decode("utf-8-sig")
    header = next(csv.reader(io.StringIO(text)))
    assert len(header) == 24, f"ヘッダが {len(header)} 列"
    assert "判定の確度" in header
    assert len(list(csv.DictReader(io.StringIO(text)))) == 2


# ── 判定の確度 ─────────────────────────────────────────────────────────────────

def test_low_confidence_truth_table():
    """判定の確度: 調達額なし・推定・部分取得は「参考値」になる"""
    assert r.is_low_confidence({"raised_usd": 1000}) is False
    assert r.is_low_confidence({"raised_usd": 0}) is True
    assert r.is_low_confidence({"raised_usd": 999, "_from_slug": True}) is True
    assert r.is_low_confidence({"raised_usd": 999, "_partial": True}) is True


def test_confidence_column_values():
    """build_row が「データ取得済み」「参考値（データ不足）」を出し分ける"""
    ok = r.build_row({"name": "X", "raised_usd": 100, "url": "u"}, {}, {})
    lc = r.build_row({"name": "Y", "raised_usd": 0, "url": "u", "_from_slug": True}, {}, {})
    assert ok["判定の確度"] == "データ取得済み", ok["判定の確度"]
    assert lc["判定の確度"] == "参考値（データ不足）", lc["判定の確度"]


# ── メーカー名の採用可否 ───────────────────────────────────────────────────────

def test_usable_maker_rejects_generic_words():
    """一般語・数値ID・空文字はメーカー名として採用しない"""
    for bad in ("rest", "REST", "team", "home", "design", "12345", "", "   "):
        assert r.usable_maker(bad) == "", f"{bad!r} が採用されてしまう"


def test_usable_maker_keeps_real_names():
    """通常のアカウント名はそのまま採用する"""
    for good in ("niid", "peak-design", "Sitpack", "Mono+Mono"):
        assert r.usable_maker(good) == good, f"{good!r} が落ちてしまう"


def test_generic_account_falls_back_to_slug_brand():
    """アカウント名が一般語(rest)なら商品スラグからSitpackを採用する"""
    info = r._extract_from_slug(KS.format(*SITPACK))
    assert info["maker"] == "Sitpack", f"実際は {info['maker']!r}"


def test_normal_account_is_unchanged():
    """一般語でないアカウント名(niid)は従来どおり採用される"""
    info = r._extract_from_slug(KS.format(*NEX))
    assert info["maker"] == "niid", f"実際は {info['maker']!r}"


def test_unresolvable_maker_is_empty_internally():
    """どれも信頼できない場合、内部のメーカー名は空になる"""
    info = r._extract_from_slug(KS.format("team", "the-smart-home-gadget-for-you"))
    assert info["maker"] == "", f"実際は {info['maker']!r}"


def test_unresolvable_maker_displays_unknown():
    """表示・CSV上では「不明」と明示される"""
    info = r._extract_from_slug(KS.format("team", "the-smart-home-gadget-for-you"))
    assert r.build_row(info, {}, {})["メーカー名"] == "不明"


def test_unknown_maker_never_reaches_sales_email():
    """「不明」が英文営業メールに混入しない（プレースホルダになる）"""
    body = r.build_approach_email({"name": "X", "maker": ""})["approach_body"]
    assert "不明" not in body, "英文メールに「不明」が入っている"
    assert "[Brand / Team Name]" in body


def test_stats_json_branch_reuses_slug_logic(monkeypatched=True):
    """stats.json経路のメーカー名が一般語(rest)にならない"""
    original = r._kicktraq_project
    r._kicktraq_project = lambda *a, **k: {}          # Kicktraq未収録を再現
    original_stats = r._ks_stats_json
    r._ks_stats_json = lambda *a, **k: {"raised_usd": 1.0, "raised_jpy": 150, "backers": 2}
    try:
        p = r._fetch_ks_via_fallback(KS.format(*SITPACK), *SITPACK)
    finally:
        r._kicktraq_project = original
        r._ks_stats_json = original_stats
    assert p is not None, "stats.json経路が None を返した"
    assert p["maker"] == "Sitpack", f"実際は {p['maker']!r}"
    assert p["_source"] == "stats.json"
    assert p["_partial"] is True, "参考値として扱われていない"


def test_stats_json_branch_keeps_amounts():
    """メーカー名の修正が調達額・支援者数に影響しない"""
    original = r._kicktraq_project
    r._kicktraq_project = lambda *a, **k: {}
    original_stats = r._ks_stats_json
    r._ks_stats_json = lambda *a, **k: {"raised_usd": 12345.0, "raised_jpy": 1851750,
                                        "backers": 678}
    try:
        p = r._fetch_ks_via_fallback(KS.format(*SITPACK), *SITPACK)
    finally:
        r._kicktraq_project = original
        r._ks_stats_json = original_stats
    assert p["raised_usd"] == 12345.0, p["raised_usd"]
    assert p["raised_jpy"] == 1851750, p["raised_jpy"]
    assert p["backers"] == 678, p["backers"]
    assert r.is_low_confidence(p) is True


def test_kicktraq_maker_is_not_overwritten():
    """Kicktraqが返したメーカー名はそのまま使う（修正の影響を受けない）"""
    original = r._kicktraq_project
    r._kicktraq_project = lambda *a, **k: {
        "raised_usd": 518764.0, "raised_jpy": 77814600, "backers": 5338,
        "name": "Sitpack ZEN", "maker": "Mono+Mono", "description": "d"}
    try:
        p = r._fetch_ks_via_fallback(KS.format(*SITPACK), *SITPACK)
    finally:
        r._kicktraq_project = original
    assert p["maker"] == "Mono+Mono", f"実際は {p['maker']!r}"
    assert p["_source"] == "kicktraq"
    assert r.is_low_confidence(p) is False, "Kicktraq経路が参考値扱いになっている"


# ── AI分析プロンプト（偽クライアントで捕捉。APIは呼ばない）────────────────────

class _FakeMessages:
    def __init__(self, box):
        self.box = box

    def create(self, **kwargs):
        self.box.update(kwargs)
        raise RuntimeError("captured")           # API呼び出し前に中断する


class _FakeClient:
    def __init__(self, box):
        self.messages = _FakeMessages(box)


def _capture_prompt(project: dict) -> dict:
    box: dict = {}
    r.analyze_with_claude(project, _FakeClient(box))
    box["_prompt"] = box.get("messages", [{}])[0].get("content", "")
    return box


def _line(prompt: str, prefix: str) -> str:
    return next((l for l in prompt.splitlines() if l.startswith(prefix)), "")


def test_prompt_marks_missing_data_as_unknown():
    """欠損データを $0 ではなく「取得できず」として渡す"""
    box = _capture_prompt({"name": "X", "maker": "niid", "platform": "Kickstarter",
                           "raised_usd": 0, "raised_jpy": 0, "backers": 0,
                           "genre": "T", "url": "u", "description": "",
                           "_from_slug": True})
    funding = _line(box["_prompt"], "調達額:")
    assert "取得できず" in funding, funding
    assert "$0" not in funding, funding


def test_prompt_passes_real_amounts():
    """データが揃っていれば実額をそのまま渡す"""
    box = _capture_prompt({"name": "X", "maker": "NIID", "platform": "Kickstarter",
                           "raised_usd": 135176, "raised_jpy": 20276400, "backers": 1200,
                           "genre": "D", "url": "u", "description": "A slim wallet."})
    funding = _line(box["_prompt"], "調達額:")
    backers = _line(box["_prompt"], "支援者数:")
    assert "$135,176" in funding and "取得できず" not in funding, funding
    assert "1,200" in backers and "取得できず" not in backers, backers


def test_prompt_does_not_pass_temperature():
    """temperature を渡さない

    新しい Anthropic SDK では messages.create から temperature が削除されており、
    渡すと API へ送信される前に TypeError になって分析が全件失敗する。
    実SDKとの引数整合は tests/test_sdk_compat.py で検証する。
    """
    box = _capture_prompt({"name": "X", "maker": "M", "platform": "Kickstarter",
                           "raised_usd": 1, "raised_jpy": 1, "backers": 1,
                           "genre": "T", "url": "u", "description": "d"})
    assert "temperature" not in box, str(box.get("temperature"))


# ── サマリー表HTML ─────────────────────────────────────────────────────────────

def _sample_rows():
    return [
        {"優先度": "B", "判定の確度": "データ取得済み", "商品名": "B商品", "メーカー名": "M",
         "プラットフォーム": "KS", "調達額(円)": 77814600, "日本で売れそうな理由": "理由B",
         "掲載URL": "https://example.com", "種別": "🌐 公式サイト",
         "アプローチ先リンク": "https://example.com"},
        {"優先度": "C", "判定の確度": "データ取得済み", "商品名": "C商品", "メーカー名": "不明",
         "プラットフォーム": "IGG", "調達額(円)": 0, "日本で売れそうな理由": "理由C",
         "掲載URL": "", "種別": "—", "アプローチ先リンク": ""},
        {"優先度": "A", "判定の確度": "参考値（データ不足）",
         "商品名": '<script>x</script>&"q"', "メーカー名": "M", "プラットフォーム": "KS",
         "調達額(円)": 0, "日本で売れそうな理由": "あ" * 300,
         "掲載URL": "https://example.com", "種別": "📧 メール",
         "アプローチ先リンク": "mailto:a@b.com"},
    ]


def _body(rows):
    return build_summary_html(rows).split("<tbody>")[1]


def test_summary_sorted_a_b_c():
    """サマリー表が優先度 A→B→C の順に並ぶ"""
    import re
    order = re.findall(r"cf-pri cf-pri-([ABC])", _body(_sample_rows()))
    assert order == ["A", "B", "C"], str(order)


def test_summary_low_confidence_badge():
    """参考値バッジが該当行だけに表示される"""
    body = _body(_sample_rows())
    assert body.count("⚠ 参考値") == 1, f"{body.count('⚠ 参考値')}件"


def test_summary_escapes_html():
    """AI生成テキストのHTMLがエスケープされる"""
    body = _body(_sample_rows())
    assert "<script>" not in body and "&lt;script&gt;" in body


def test_summary_keeps_full_text():
    """理由の全文が切り詰められない"""
    assert "あ" * 300 in _body(_sample_rows())


def test_summary_formats_amounts():
    """金額はカンマ区切り、未取得は「—」で表示する"""
    body = _body(_sample_rows())
    assert "¥77,814,600" in body
    assert ">—<" in body


if __name__ == "__main__":
    sys.exit(run(dict(globals()), "オフライン回帰テスト（ネットワーク・AI API 不使用）"))
