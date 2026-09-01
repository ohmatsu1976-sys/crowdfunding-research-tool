# -*- coding: utf-8 -*-
"""検索結果1行のスキーマ（表示・CSVで必要な列）の定義と正規化

表示側で列を選ぶ直前に `df[[...]]` すると、行に列が欠けていたときに
KeyError でページ全体が落ちる。列の有無を表示箇所で場当たり的に握り潰すのではなく、
「検索結果をDataFrameへ変換する境界」で行のスキーマをここに統一する。

列を追加・変更したときは SCHEMA_VERSION を上げる。
開きっぱなしのブラウザセッションに残った古い形式の結果は、
search_state.migrate_state() がこのモジュールの normalize_rows() で移行する。

Streamlit にも pandas にも依存しないため、単体でテストできる。
"""

from typing import Dict, List, Sequence, Tuple

from research_crowdfunding import CSV_FIELDS

# 行の形式を変えたら必ず上げる（session_state の移行判定に使う）
SCHEMA_VERSION = 2

# サマリー表が必要とする列。表示側で列名を直書きせず、ここだけを見る
DISPLAY_COLUMNS: List[str] = [
    "優先度", "判定の確度", "商品名", "メーカー名", "プラットフォーム",
    "調達額(円)", "日本で売れそうな理由", "掲載URL",
]

# 既定値は build_row / analyze_with_claude の既存仕様に合わせる
_UNVERIFIED = "未確認"                     # 連絡先などの未確認表記
_NOT_ANALYZED = "（Claude未接続のため省略）"  # AI分析が無いときの文章列
_HIGH_CONFIDENCE = "データ取得済み"
_LOW_CONFIDENCE = "参考値（データ不足）"

# 数値列。型を壊さないよう欠損時は 0（int）で埋める
_NUMERIC_COLUMNS = frozenset({"調達額(円)", "調達額(USD)", "支援者数"})
# 連絡先系。既存仕様どおり「未確認」
_UNVERIFIED_COLUMNS = frozenset({
    "メールアドレス", "問い合わせフォームURL",
    "Facebook", "Instagram", "LinkedIn",
    "競合する日本商品", "注意点・懸念点",
})
# AI分析が無いと埋まらない文章列。AI分析失敗時と同じ表記にそろえる
_NOT_ANALYZED_COLUMNS = frozenset({
    "日本で売れそうな理由", "日本販売時の訴求ポイント", "優先度の理由",
})
# URL・文字列列。型を壊さないよう空文字で埋める
_URL_COLUMNS = frozenset({"掲載URL", "公式サイトURL"})


def required_columns() -> List[str]:
    """行に必ず持たせる列

    CSV_FIELDS を正とするが、表示に必要な列は必ず含める。
    Streamlit は再実行時に import 済みモジュールを読み直さないことがあり、
    デプロイ直後に古い CSV_FIELDS を掴んだままになる場合があるため、
    サマリー表の列だけは CSV_FIELDS の状態に関わらず補えるようにする。
    """
    columns = list(CSV_FIELDS)
    for column in DISPLAY_COLUMNS:
        if column not in columns:
            columns.append(column)
    return columns


def _is_missing(value) -> bool:
    """欠損とみなすか（None と NaN のみ。空文字は「値がある」として尊重する）"""
    if value is None:
        return True
    return value != value          # NaN は自分自身と等しくならない


def _looks_low_confidence(row: Dict) -> bool:
    """行から判定の確度を推定する

    is_low_confidence() は取得直後の project 辞書（_from_slug / _partial）を見るが、
    CSV1行にはそのフラグが残らない。行から復元できるのは調達額の条件だけなので、
    調達額が取れていない行を安全側（参考値）に倒す。
    """
    try:
        return not float(row.get("調達額(USD)") or 0)
    except (TypeError, ValueError):
        return True


def default_for(column: str, row: Dict) -> object:
    """欠けている列に入れる既定値（既存仕様と整合させる）"""
    if column == "メーカー名":
        return "不明"                      # build_row と同じ。表示上は「不明」
    if column == "優先度":
        return "B"                        # analyze_with_claude の既定と同じ
    if column == "判定の確度":
        return _LOW_CONFIDENCE if _looks_low_confidence(row) else _HIGH_CONFIDENCE
    if column in _NUMERIC_COLUMNS:
        return 0
    if column in _UNVERIFIED_COLUMNS:
        return _UNVERIFIED
    if column in _NOT_ANALYZED_COLUMNS:
        return _NOT_ANALYZED
    if column in _URL_COLUMNS:
        return ""
    return ""


def normalize_row(row: Dict) -> Tuple[Dict, List[str]]:
    """1行を現在のスキーマへそろえる

    戻り値は (そろえた行, 欠けていた列名). 既に値がある列は上書きしない。
    CSV_FIELDS に無い列（将来削除された列など）は捨てずにそのまま残す。
    """
    if not isinstance(row, dict):
        raise TypeError(f"検索結果の行が辞書ではありません: {type(row).__name__}")
    fixed = dict(row)
    missing: List[str] = []
    for column in required_columns():
        if column not in fixed or _is_missing(fixed[column]):
            fixed[column] = default_for(column, fixed)
            missing.append(column)
    return fixed, missing


def normalize_rows(rows: Sequence[Dict]) -> Tuple[List[Dict], List[str]]:
    """検索結果の全行を現在のスキーマへそろえる

    戻り値は (そろえた行, 1行でも欠けていた列名の一覧). 原因を隠さないよう、
    どの列が欠けていたかを呼び出し側へ返す。
    """
    fixed_rows: List[Dict] = []
    missing_all: List[str] = []
    for row in rows or []:
        fixed, missing = normalize_row(row)
        fixed_rows.append(fixed)
        for column in missing:
            if column not in missing_all:
                missing_all.append(column)
    order = required_columns()
    missing_all.sort(key=order.index)
    return fixed_rows, missing_all
