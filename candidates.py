# -*- coding: utf-8 -*-
"""候補保存（フェーズ3B）

検索・AI分析済みの商品を、ログイン中の本人の「マイ候補リスト」へ保存する。

保存は public.save_candidate() のRPCだけを使う。products / saved_items への
直接 insert / upsert / update は一切行わない（そもそもその権限を与えていない。
sql/migrations/0003_rls_grants.sql 参照）。保存者は RPC 内部の auth.uid() で
決まるため、この層からも Supabase 側からも user_id を指定する経路は無い。

Streamlit にも認証にも依存しない。クライアントを引数で受け取るだけなので、
偽クライアントで外部通信なしにテストできる（auth.py と同じ方針）。
本人のJWTをクライアントへ設定する処理（auth.apply_session）は呼び出し側
（candidates_ui.py）の責務とする。
"""

import math
from datetime import date, datetime
from typing import Any, Dict, List, Tuple

import product_key as pk
import result_schema as rschema

RPC_NAME = "save_candidate"

# ── 利用者へ見せる文言（例外の中身・Supabaseの内部情報は一切含めない）────────
SAVED_ONE = "マイ候補リストに保存しました。"
ALREADY_ONE = "この商品はすでにマイ候補リストに保存されています。"
SAVE_FAILED = "保存できませんでした。時間をおいて、もう一度お試しください。"
INVALID_URL = "このURLは候補として保存できません。対応プラットフォームのURLかご確認ください。"
NOTHING_SELECTED = "保存する商品が選択されていません。チェックを付けてから押してください。"


class SaveResult:
    """1件の保存結果"""

    __slots__ = ("name", "url", "ok", "already_saved", "message")

    def __init__(self, name: str, url: str, ok: bool,
                already_saved: bool, message: str):
        self.name = name
        self.url = url
        self.ok = ok
        self.already_saved = already_saved
        self.message = message


# ── JSON化できない値の安全な正規化 ────────────────────────────────────────────

def _json_safe(value: Any) -> Any:
    """jsonbへ送っても壊れない値へそろえる

    NaN・Infinity・日付型・その他JSON化できない型が混ざっていても、
    例外を出さずに安全な値（null・ISO日付文字列・文字列）へ変換する。
    """
    if value is None or isinstance(value, (bool, str)):
        return value
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
        return round(value, 2)
    if isinstance(value, int):
        return value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(v) for v in value]
    try:
        return str(value)
    except Exception:
        return None


def _int_or_zero(value: Any) -> int:
    try:
        if value is None:
            return 0
        f = float(value)
        if math.isnan(f) or math.isinf(f):
            return 0
        return int(f)
    except (TypeError, ValueError):
        return 0


def _number_or_zero(value: Any) -> float:
    try:
        if value is None:
            return 0.0
        f = float(value)
        if math.isnan(f) or math.isinf(f):
            return 0.0
        return round(f, 2)
    except (TypeError, ValueError):
        return 0.0


def build_product_payload(row: Dict[str, Any]) -> Dict[str, Any]:
    """検索結果1行（CSV_FIELDS形式）から save_candidate() の p_product 引数を作る

    save_candidate() が実際に読む項目（0002_functions.sql）に合わせて、
    フラットな数値・文字列項目と、analysis / contact のネストしたJSONへ分ける。
    schema_version は現在の正規化バージョンを使う（更新機能を作るときの
    「どの版で作られたか」の手がかりにする）。
    """
    row = row or {}
    analysis = {
        "description": _json_safe(row.get("商品の特徴", "")),
        "japanese_market_reason": _json_safe(row.get("日本で売れそうな理由", "")),
        "appeal_points": _json_safe(row.get("日本販売時の訴求ポイント", "")),
        "japanese_competitors": _json_safe(row.get("競合する日本商品", "")),
        "priority_reason": _json_safe(row.get("優先度の理由", "")),
        "concerns": _json_safe(row.get("注意点・懸念点", "")),
        "approach_subject": _json_safe(row.get("営業メール件名(英語)", "")),
        "approach_body": _json_safe(row.get("営業メール本文(英語)", "")),
    }
    contact = {
        "official_url": _json_safe(row.get("公式サイトURL", "")),
        "email": _json_safe(row.get("メールアドレス", "")),
        "contact_form": _json_safe(row.get("問い合わせフォームURL", "")),
        "facebook": _json_safe(row.get("Facebook", "")),
        "instagram": _json_safe(row.get("Instagram", "")),
        "linkedin": _json_safe(row.get("LinkedIn", "")),
    }
    return {
        "platform": str(row.get("プラットフォーム", "") or ""),
        "name": str(row.get("商品名", "") or ""),
        "maker": str(row.get("メーカー名", "") or ""),
        "genre": str(row.get("商品ジャンル", "") or ""),
        "raised_jpy": _int_or_zero(row.get("調達額(円)")),
        "raised_usd": _number_or_zero(row.get("調達額(USD)")),
        "backers": _int_or_zero(row.get("支援者数")),
        "priority": str(row.get("優先度", "") or ""),
        "confidence": str(row.get("判定の確度", "") or ""),
        "analysis": analysis,
        "contact": contact,
        "schema_version": rschema.SCHEMA_VERSION,
    }


# ── 保存 ──────────────────────────────────────────────────────────────────────

def save_one(client, row: Dict[str, Any]) -> SaveResult:
    """1件を候補リストへ保存する

    書き込みは save_candidate() のRPC呼び出しだけで行う。
    保存済みかどうかは事前SELECTでは調べず、RPCの戻り値 already_saved だけで
    判断する（他人の保存状況を推測できる問い合わせを作らないため）。
    """
    name = str(row.get("商品名", "") or "（商品名不明）")
    source_url = str(row.get("掲載URL", "") or "")
    url_key = pk.normalize_url(source_url)
    if not url_key:
        return SaveResult(name, source_url, False, False, INVALID_URL)

    payload = build_product_payload(row)
    try:
        response = client.rpc(RPC_NAME, {
            "p_url_key": url_key,
            "p_source_url": source_url,
            "p_product": payload,
        }).execute()
    except Exception:
        # 例外の中身にはSupabaseの内部情報が含まれうるため、利用者へは出さない
        return SaveResult(name, source_url, False, False, SAVE_FAILED)

    data = getattr(response, "data", None) or {}
    if not isinstance(data, dict) or not data.get("saved_item_id"):
        return SaveResult(name, source_url, False, False, SAVE_FAILED)

    return SaveResult(name, source_url, True, bool(data.get("already_saved")), "")


def save_many(client, rows: List[Dict[str, Any]]) -> List[SaveResult]:
    """複数件を候補リストへ保存する（1件ずつ save_one を呼ぶだけ）"""
    return [save_one(client, row) for row in (rows or [])]


def summarize(results: List[SaveResult]) -> Tuple[int, int, int]:
    """保存成功・保存済み・失敗の件数を集計する"""
    saved = sum(1 for r in results if r.ok and not r.already_saved)
    already = sum(1 for r in results if r.ok and r.already_saved)
    failed = sum(1 for r in results if not r.ok)
    return saved, already, failed


def summary_message(results: List[SaveResult]) -> str:
    """保存結果をまとめた1〜2行の日本語文言を作る

    1件だけなら「保存しました」/「すでに保存されています」の具体的な文言、
    複数件なら件数の集計を返す。失敗の中身（例外文）は一切含めない。
    """
    if not results:
        return ""
    if len(results) == 1:
        r = results[0]
        if r.ok and not r.already_saved:
            return SAVED_ONE
        if r.ok and r.already_saved:
            return ALREADY_ONE
        return r.message or SAVE_FAILED

    saved, already, failed = summarize(results)
    parts = []
    if saved:
        parts.append(f"新規保存 {saved}件")
    if already:
        parts.append(f"保存済み {already}件")
    if failed:
        parts.append(f"失敗 {failed}件")
    return "、".join(parts) + "。" if parts else ""
