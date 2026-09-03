# -*- coding: utf-8 -*-
"""クラウドファンディングURLの正規化と対象ドメインの検証

「マイ候補リスト」で同じ商品を同じ1行に束ねるための正規化ロジック。
Streamlit にも Supabase にも依存しない純関数のみで、単体テストできる。

対象ドメインは ALLOWED_HOSTS の3つだけ（検索ツールが対応している範囲と
一致させている）。sql/migrations/0002_functions.sql の save_candidate() が
DB側で同じ判定を行っており、tests/test_sql_migrations.py が両者の
ドメイン一覧が一致していることを検証する。ドメインを追加するときは
両方を同時に更新すること。

判定は文字列の部分一致では行わない。urllib.parse.urlsplit() で
URLを解析してホスト名だけを取り出し、許可リストと完全一致で比較する。
これにより「zeczec.com.example.com」（後ろに何か続く偽装）や
「example-zeczec.com」（似た名前の偽装）を正しく拒否できる。
"""

import re
from urllib.parse import urlsplit

# 検索ツールが対応している3プラットフォームのみ許可する。
ALLOWED_HOSTS = frozenset({"kickstarter.com", "indiegogo.com", "zeczec.com"})

# Kickstarter のタブ付きURL（プロジェクト本体と同じ商品を指す）。
# research_crowdfunding._KS_TAB_SUFFIXES と同じ一覧。
_KS_TAB_SUFFIXES = (
    "/creator", "/description", "/updates", "/comments",
    "/community", "/faqs", "/risks", "/rewards",
)

# 末尾に手動入力された調達額（"https://...  $57,485" のような形式）を除去する
_MANUAL_AMOUNT_SUFFIX = re.compile(r"\s+\$[\d,]+(?:\.\d+)?\s*$")

# sql/migrations/0001_tables.sql の products_url_key_len 制約と合わせる
MIN_LENGTH = 20
MAX_LENGTH = 1024


def _hostname(url: str) -> str:
    """URLを解析してホスト名だけを取り出す（文字列の部分一致では判定しない）

    ポート番号・ユーザー情報（user:pass@host）が付いていても、
    urlsplit().hostname は host 部分だけを正しく返す。
    """
    if not url:
        return ""
    try:
        parsed = urlsplit(url)
    except ValueError:
        return ""
    host = (parsed.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    return host


def is_allowed_host(url: str) -> bool:
    """対象ドメイン（Kickstarter / Indiegogo / ZECZEC）かどうか

    完全一致でのみ判定する。サブドメインや類似ドメインは許可しない。
    """
    return _hostname(url) in ALLOWED_HOSTS


def normalize_url(raw_url: str) -> str:
    """検索・保存で同じ商品を指すURLを1つのキーへ正規化する

    対象ドメイン以外・解析できないURL・パスが空になるURLは空文字を返す。
    戻り値は save_candidate() に渡す url_key と同じ形式にする:
        https:// + ホスト名（www.なし） + パス（クエリ・フラグメントなし）
        末尾スラッシュなし
    """
    if not raw_url:
        return ""
    text = _MANUAL_AMOUNT_SUFFIX.sub("", raw_url).strip()
    if not text:
        return ""
    try:
        parsed = urlsplit(text)
    except ValueError:
        return ""
    if parsed.scheme != "https":
        return ""

    host = (parsed.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if host not in ALLOWED_HOSTS:
        return ""

    path = (parsed.path or "").rstrip("/")
    for suffix in _KS_TAB_SUFFIXES:
        if path.endswith(suffix):
            path = path[: -len(suffix)]
            break
    path = path.rstrip("/")
    if not path or not path.startswith("/"):
        return ""

    key = f"https://{host}{path}"
    if not (MIN_LENGTH <= len(key) <= MAX_LENGTH):
        return ""
    return key
