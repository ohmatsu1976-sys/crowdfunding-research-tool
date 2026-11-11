"""サマリー表のHTML生成

st.dataframe は長文セルを折り返せず「日本で売れそうな理由」が途中で切れるため、
折り返し表示できるHTMLテーブルを組み立てる。
"""

import html
from typing import Iterable, List

import contact_info

PRIORITY_ORDER = {"A": 0, "B": 1, "C": 2}
PRIORITY_BADGE = {"A": "🟢", "B": "🟡", "C": "🔴"}

TABLE_STYLE = """
<style>
.cf-wrap { overflow-x: auto; margin-bottom: 1rem; }
.cf-table { width: 100%; min-width: 880px; border-collapse: collapse; font-size: 0.86rem; }
.cf-table th, .cf-table td {
    border: 1px solid rgba(128,128,128,0.35);
    padding: 8px 10px;
    vertical-align: top;
    text-align: left;
    white-space: normal;      /* 長文を折り返して全文表示する */
    word-break: break-word;
    line-height: 1.6;
}
.cf-table th { background: rgba(128,128,128,0.14); font-weight: 600; white-space: nowrap; }
.cf-table tr:nth-child(even) td { background: rgba(128,128,128,0.06); }
.cf-num { text-align: right; white-space: nowrap; }
.cf-pri { font-weight: 700; white-space: nowrap; }
.cf-lowconf {
    display: block; margin-top: 4px; font-size: 0.74rem; font-weight: 600;
    color: #bf8700; white-space: nowrap;
}
.cf-pri-A { color: #1a7f37; }
.cf-pri-B { color: #bf8700; }
.cf-pri-C { color: #cf222e; }
.cf-reason { min-width: 260px; }
</style>
"""

_HEADER = """
<div class="cf-wrap">
<table class="cf-table">
<colgroup>
  <col style="width:6%"><col style="width:20%"><col style="width:11%"><col style="width:10%">
  <col style="width:11%"><col style="width:33%"><col style="width:9%">
</colgroup>
<thead><tr>
  <th>優先度</th><th>商品名</th><th>メーカー名</th><th>プラットフォーム</th>
  <th>調達額(円)</th><th>日本で売れそうな理由</th><th>アプローチ先</th>
</tr></thead>
<tbody>
"""


def _esc(value) -> str:
    return html.escape("" if value is None else str(value))


def _link(url: str, label: str) -> str:
    """リンクHTML。URLが不正なら素のテキストにする"""
    url = str(url or "").strip()
    if not (url.startswith("http") or url.startswith("mailto:")):
        return _esc(label)
    return f'<a href="{_esc(url)}" target="_blank" rel="noopener">{_esc(label)}</a>'


def _sort_key(row: dict):
    priority = str(row.get("優先度", "")).strip().upper()
    return (PRIORITY_ORDER.get(priority, 9), str(row.get("商品名", "")))


def _contact_cell_html(row: dict) -> str:
    """「アプローチ先」セルのHTMLを組み立てる

    優先順位（contact_info.resolve_priority）どおりに1つだけ選んで表示する:
    1.メール 2.Facebook 3.問い合わせフォーム 4.LinkedIn 5.Instagram
    6.公式サイト 7.商品掲載ページ 8.連絡先未取得。
    メールは mailto: を唯一の手段にせず、アドレス自体も表示してコピーできるようにする
    （端末にメールソフトが無いと mailto: リンクだけでは空白画面になるため）。
    """
    choice = contact_info.resolve_priority(row)
    if choice["kind"] == "email":
        mail_link = _link("mailto:" + choice["email"], "📧 メールを作成")
        return f'<div>📧 <code>{_esc(choice["email"])}</code></div><div>{mail_link}</div>'
    if choice["kind"] != "none":
        return _link(choice["url"], choice["label"])
    return "連絡先未取得"


def build_summary_html(rows: Iterable[dict]) -> str:
    """サマリー表のHTMLを返す（rows は 1商品 = 1辞書）"""
    body: List[str] = []
    for row in sorted(rows, key=_sort_key):
        priority = str(row.get("優先度", "")).strip().upper()
        badge = PRIORITY_BADGE.get(priority, "")

        try:
            amount = int(row.get("調達額(円)") or 0)
        except (TypeError, ValueError):
            amount = 0
        amount_txt = f"¥{amount:,}" if amount else "—"

        name_html = _link(row.get("掲載URL", ""), row.get("商品名", ""))
        contact_html = _contact_cell_html(row)

        # 判定材料が不足している行は、判定が揺れうることを明示する
        low_conf = str(row.get("判定の確度", "")).startswith("参考値")
        conf_html = '<span class="cf-lowconf">⚠ 参考値</span>' if low_conf else ""

        body.append(
            "<tr>"
            f'<td class="cf-pri cf-pri-{_esc(priority)}">{badge} {_esc(priority)}{conf_html}</td>'
            f"<td>{name_html}</td>"
            f'<td>{_esc(row.get("メーカー名", ""))}</td>'
            f'<td>{_esc(row.get("プラットフォーム", ""))}</td>'
            f'<td class="cf-num">{amount_txt}</td>'
            f'<td class="cf-reason">{_esc(row.get("日本で売れそうな理由", ""))}</td>'
            f"<td>{contact_html}</td>"
            "</tr>"
        )

    return TABLE_STYLE + _HEADER + "".join(body) + "</tbody></table></div>"
