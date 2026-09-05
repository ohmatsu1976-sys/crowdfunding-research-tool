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
from datetime import date, datetime, timedelta, timezone
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


# =============================================================================
# マイ候補リスト（フェーズ3C）
#
# 一覧取得・更新・アーカイブ・削除。いずれも products / saved_items へ
# select("*") を使わず、必要な列だけを明示する。like / ilike / text_search
# による products の探索は行わない（そもそも一覧はしない。products は
# saved_items との結合でしか触らない）。
#
# 更新・削除の対象は必ず id で明示したうえで、user_id でも絞り込む
# （RLSで本人以外の行は返らない前提だが、対象を明示することで二重に守る）。
# user_id / product_id / saved_at / updated_at はここから一切更新しない
# （そもそも sql/migrations/0003_rls_grants.sql が権限を与えていない）。
# =============================================================================

# sql/migrations/0001_tables.sql の CHECK 制約と完全一致させる。
# 変更する場合は両方を同時に更新すること
# （tests/test_candidates.py が一致を検証する）。
STATUS_OPTIONS = ("候補", "精査中", "連絡済み", "返信あり",
                  "交渉中", "契約済み", "保留", "見送り")
PRIORITY_OPTIONS = ("A", "B", "C")
DEFAULT_STATUS = "候補"

# saved_items と、表示に使うぶんだけの products 列。select("*") は使わない。
_LIST_SELECT = (
    "id,product_id,memo,status,priority_override,archived,saved_at,updated_at,"
    "products(id,source_url,platform,name,maker,priority)"
)

LIST_FAILED = "候補リストを取得できませんでした。時間をおいて、もう一度お試しください。"

# 保存日時（saved_at）は表示のためだけに日本時間へ変換する。
# DB側にはUTCのISO文字列のまま保存し続ける（ここで変換した結果を書き戻すことはない）。
# 日本にサマータイムは無いため、標準ライブラリの固定オフセット（+9時間）だけで足りる。
_JST = timezone(timedelta(hours=9))
_INVALID_DATETIME_DISPLAY = "－"


def format_saved_at_jst(value: Any) -> str:
    """保存日時のISO文字列を日本時間の表示用文字列に変換する

    例: "2026-09-05T02:27:01.516747+00:00" -> "2026年9月5日 11:27"
    "Z"終端・"+00:00"終端・別のタイムゾーン付きのいずれにも対応する。
    None・空文字・不正な値では例外を出さず "－" を返す（画面を落とさない）。
    """
    if not isinstance(value, str):
        return _INVALID_DATETIME_DISPLAY
    text = value.strip()
    if not text:
        return _INVALID_DATETIME_DISPLAY
    if text.endswith("Z") or text.endswith("z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        local = parsed.astimezone(_JST)
    except (ValueError, TypeError, OverflowError):
        return _INVALID_DATETIME_DISPLAY
    return f"{local.year}年{local.month}月{local.day}日 {local.hour}:{local.minute:02d}"


UPDATE_OK = "候補情報を更新しました。"
UPDATE_FAILED = "更新できませんでした。時間をおいて、もう一度お試しください。"
ARCHIVE_OK = "候補をアーカイブしました。"
UNARCHIVE_OK = "候補を一覧に戻しました。"
DELETE_OK = "候補リストから削除しました。"
DELETE_FAILED = "削除できませんでした。時間をおいて、もう一度お試しください。"

_UNSET = object()  # priority_override を「引数として渡さなかった」ことを表す印


class ListItem:
    """マイ候補リストの1行（saved_items 本人の分 ＋ products の表示用項目）"""

    __slots__ = ("saved_item_id", "product_id", "memo", "status",
                "priority_override", "archived", "saved_at", "updated_at",
                "name", "source_url", "platform", "maker", "priority")

    def __init__(self, **fields: Any):
        for key in self.__slots__:
            setattr(self, key, fields.get(key))


def _first_product(value: Any) -> Dict[str, Any]:
    """postgrestの埋め込み結果（dict または 1件のlist）から商品情報を取り出す"""
    if isinstance(value, list):
        return value[0] if value and isinstance(value[0], dict) else {}
    return value if isinstance(value, dict) else {}


def _to_list_item(row: Dict[str, Any]) -> ListItem:
    product = _first_product(row.get("products"))
    return ListItem(
        saved_item_id=row.get("id", ""),
        product_id=row.get("product_id", ""),
        memo=row.get("memo") or "",
        status=row.get("status") or DEFAULT_STATUS,
        priority_override=row.get("priority_override") or None,
        archived=bool(row.get("archived", False)),
        saved_at=row.get("saved_at") or "",
        updated_at=row.get("updated_at") or "",
        name=product.get("name") or "（商品名不明）",
        source_url=product.get("source_url") or "",
        platform=product.get("platform") or "",
        maker=product.get("maker") or "不明",
        priority=product.get("priority") or "",
    )


def list_saved_items(client, user_id: str, include_archived: bool = False) -> List[ListItem]:
    """本人の候補一覧を取得する

    RLSにより他人の行は返らない前提だが、本人が管理者であっても
    「マイ候補リスト」には自分の分だけを出すため、user_id でも明示的に絞る。
    失敗時は空リストを返す（呼び出し側で LIST_FAILED を表示する）。
    """
    if not user_id:
        return []
    try:
        query = (client.table("saved_items")
                 .select(_LIST_SELECT)
                 .eq("user_id", user_id)
                 .order("saved_at", desc=True))
        if not include_archived:
            query = query.eq("archived", False)
        response = query.execute()
    except Exception:
        return []
    rows = getattr(response, "data", None) or []
    return [_to_list_item(row) for row in rows if isinstance(row, dict)]


def update_saved_item(client, user_id: str, saved_item_id: str, *,
                      memo: Any = None, status: Any = None,
                      priority_override: Any = _UNSET,
                      archived: Any = None) -> bool:
    """本人の候補1件を更新する

    変更できるのは memo / status / priority_override / archived の4列だけ
    （sql/migrations/0003_rls_grants.sql の GRANT UPDATE と同じ4列）。
    user_id / product_id / saved_at / updated_at はここでは絶対に送らない。
    status・priority_override は DB の CHECK 制約と同じ値しか受け付けない。
    """
    if not saved_item_id or not user_id:
        return False

    fields: Dict[str, Any] = {}
    if memo is not None:
        fields["memo"] = str(memo)[:5000]
    if status is not None:
        if status not in STATUS_OPTIONS:
            return False
        fields["status"] = status
    if priority_override is not _UNSET:
        if priority_override is not None and priority_override not in PRIORITY_OPTIONS:
            return False
        fields["priority_override"] = priority_override
    if archived is not None:
        fields["archived"] = bool(archived)
    if not fields:
        return True

    try:
        (client.table("saved_items")
              .update(fields)
              .eq("id", saved_item_id)
              .eq("user_id", user_id)
              .execute())
        return True
    except Exception:
        return False


def delete_saved_item(client, user_id: str, saved_item_id: str) -> bool:
    """本人の候補1件を削除する（saved_itemsの行のみ。productsは削除しない）"""
    if not saved_item_id or not user_id:
        return False
    try:
        (client.table("saved_items")
              .delete()
              .eq("id", saved_item_id)
              .eq("user_id", user_id)
              .execute())
        return True
    except Exception:
        return False
