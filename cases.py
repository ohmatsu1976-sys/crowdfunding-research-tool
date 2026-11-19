# -*- coding: utf-8 -*-
"""商品案件管理（第1段階「返信獲得」）のデータ層

マイ候補リストの1件から商品案件を作り、アプローチ（送信・フォロー・返信・
条件確認/回答・状態変更）の履歴を記録・訂正・伏せ字化・取消しし、月間目標と
ダッシュボード集計・今日やることを計算する。

書き込みは sql/migrations/0008_cases_functions.sql のRPCだけを使う。
cases / case_events への直接 insert は一切行わない（そもそも権限が無い。
sql/migrations/0009_cases_rls_grants.sql 参照）。保存者・操作対象は常に
RPC内部の auth.uid() で決まるため、この層からも user_id を指定する経路は無い。

Streamlit には一切依存しない。クライアントを引数で受け取るだけなので、
偽クライアントで外部通信なしでテストできる（auth.py / candidates.py と同じ方針）。
"""

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence

import contact_info

# ── RPC名 ──────────────────────────────────────────────────────────────────────
CREATE_CASE_RPC = "create_case_from_candidate"
LOG_EVENT_RPC = "log_case_event"
VOID_EVENT_RPC = "void_case_event"
CORRECT_EVENT_RPC = "correct_case_event"
REDACT_EVENT_RPC = "redact_case_event"
SAVE_AI_ASSIST_RPC = "save_ai_assist_result"

# ── 状態（7つ）。日本語表示と内部値 ────────────────────────────────────────────
STATUS_NOT_CONTACTED = "not_contacted"
STATUS_AWAITING_FIRST_REPLY = "awaiting_first_reply"
STATUS_AWAITING_OUR_REPLY = "awaiting_our_reply"
STATUS_AWAITING_NEXT_REPLY = "awaiting_next_reply"
STATUS_ADVANCED = "advanced"
STATUS_ON_HOLD = "on_hold"
STATUS_LOST = "lost"

STATUS_OPTIONS = (
    STATUS_NOT_CONTACTED, STATUS_AWAITING_FIRST_REPLY, STATUS_AWAITING_OUR_REPLY,
    STATUS_AWAITING_NEXT_REPLY, STATUS_ADVANCED, STATUS_ON_HOLD, STATUS_LOST,
)

STATUS_LABELS_JA = {
    STATUS_NOT_CONTACTED: "未送信",
    STATUS_AWAITING_FIRST_REPLY: "メーカーからの初回返信待ち",
    STATUS_AWAITING_OUR_REPLY: "こちらの回答待ち",
    STATUS_AWAITING_NEXT_REPLY: "メーカーからの再返信待ち",
    STATUS_ADVANCED: "商談へ進んだ",
    STATUS_ON_HOLD: "保留",
    STATUS_LOST: "見送り・終了",
}

# ── フェーズ（将来拡張用。第1回実装では'reply'だけを使用） ─────────────────────
PHASE_REPLY = "reply"
PHASE_DEAL = "deal"
PHASE_PLAN = "plan"
PHASE_LAUNCH = "launch"
PHASE_OPTIONS = (PHASE_REPLY, PHASE_DEAL, PHASE_PLAN, PHASE_LAUNCH)

# ── 連絡チャネル（contact_info.resolve_priority()のkindと同じ値） ────────────
CHANNEL_OPTIONS = (
    "email", "facebook", "instagram", "form", "linkedin", "official", "listing", "none",
)

# ── イベント種別（9つ） ────────────────────────────────────────────────────────
EVENT_INITIAL = "initial"
EVENT_FOLLOWUP1 = "followup1"
EVENT_FOLLOWUP2 = "followup2"
EVENT_REPLY = "reply"
EVENT_OUR_REPLY = "our_reply"
EVENT_CONDITION_CHECK = "condition_check"
EVENT_CONDITION_ANSWER = "condition_answer"
EVENT_STATUS_CHANGE = "status_change"
EVENT_NOTE = "note"

EVENT_KIND_OPTIONS = (
    EVENT_INITIAL, EVENT_FOLLOWUP1, EVENT_FOLLOWUP2, EVENT_REPLY, EVENT_OUR_REPLY,
    EVENT_CONDITION_CHECK, EVENT_CONDITION_ANSWER, EVENT_STATUS_CHANGE, EVENT_NOTE,
)
# 受信（メーカーからの着信）とみなすイベント種別。AI支援結果の保存対象を
# この2種類だけに絞るのに使う（save_ai_assist_result() RPCと同じ制約）。
INBOUND_EVENT_KINDS = (EVENT_REPLY, EVENT_CONDITION_ANSWER)
# 手動状態変更で指定できる遷移先
STATUS_CHANGE_TARGETS = (STATUS_ADVANCED, STATUS_ON_HOLD, STATUS_LOST)

# ── 利用者へ見せる文言（例外の中身は一切出さない） ────────────────────────────
CREATE_FAILED = "商品案件を作成できませんでした。時間をおいて、もう一度お試しください。"
CREATE_INVALID_CANDIDATE = "この商品候補からは案件を作成できません。"
LOG_EVENT_FAILED = "記録できませんでした。時間をおいて、もう一度お試しください。"
LOG_EVENT_INVALID_TRANSITION = "現在の状態では、この操作は行えません。画面を更新してご確認ください。"
VOID_FAILED = "取消しできませんでした。時間をおいて、もう一度お試しください。"
VOID_BREAKS_HISTORY = (
    "この取消しは、後に記録した履歴と矛盾するため行えませんでした。"
    "後ろ（新しい方）の履歴から順番に取り消してください。"
)
CORRECT_FAILED = "訂正できませんでした。時間をおいて、もう一度お試しください。"
REDACT_FAILED = "伏せ字化できませんでした。時間をおいて、もう一度お試しください。"
AI_ASSIST_SAVE_FAILED = "AI支援結果を保存できませんでした。"
AI_ASSIST_INVALID_PAYLOAD = "AI支援結果の形式が不正です。"
TARGET_SAVE_FAILED = "月間目標を保存できませんでした。時間をおいて、もう一度お試しください。"

REDACTED_PLACEHOLDER = "［伏せ字化済み：内容は削除されました］"


# =============================================================================
# JST日付処理
#
# すべての日付計算（フォロー期限・月の判定・今日やること）はJST基準で行う。
# DBにはUTCのtimestamptzのまま保存し続け、ここで変換した結果を書き戻すことはない
# （candidates.format_saved_at_jst()と同じ方針）。
# =============================================================================

JST = timezone(timedelta(hours=9))


def _parse_datetime(value: Any) -> Optional[datetime]:
    """ISO文字列・datetime・Noneのいずれでも、tz付きdatetimeに正規化する

    不正な値・空文字では例外を出さずNoneを返す（画面を落とさない）。
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    if text.endswith("Z") or text.endswith("z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except (ValueError, TypeError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def to_jst_date(value: Any) -> Optional[date]:
    """ISO文字列・datetime・Noneのいずれでも、JSTの日付(date)に変換する"""
    parsed = _parse_datetime(value)
    if parsed is None:
        return None
    return parsed.astimezone(JST).date()


def to_jst_month_start(value: Any) -> Optional[date]:
    """JST基準でその月の1日を返す（case_targets.target_monthと同じ正規化）

    value は ISO文字列・datetime・date のいずれでもよい（date はそのまま、
    他はJSTへ変換したうえで月初日にする）。
    """
    if isinstance(value, date) and not isinstance(value, datetime):
        return value.replace(day=1)
    d = to_jst_date(value)
    if d is None:
        return None
    return d.replace(day=1)


def jst_today() -> date:
    return datetime.now(JST).date()


def current_month_start() -> date:
    return jst_today().replace(day=1)


def _iso(value: Any) -> str:
    """datetimeをISO8601文字列に変換する（RPCへ渡す用）"""
    if isinstance(value, datetime):
        dt = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return dt.isoformat()
    return str(value)


# =============================================================================
# 連絡先候補（contact_snapshot）
# =============================================================================

# products.contact / candidates.build_product_payload() と同じキー名に揃える。
# create_case_from_candidate() RPCがコピーする許可リストと一致させること
# （tests/test_cases_sql_migrations.py が一致を検証する）。
CONTACT_SNAPSHOT_KEYS = (
    "email", "facebook", "instagram", "contact_form", "linkedin", "official_url",
)


def _contact_snapshot_to_priority_row(
    contact_snapshot: Dict[str, Any], source_url: str, platform: str
) -> Dict[str, Any]:
    """contact_snapshot(英語キー)を、contact_info.resolve_priority()が読む
    日本語キーの行形式へ変換する（優先順位ロジックを重複させない）"""
    snap = contact_snapshot or {}
    return {
        "メールアドレス": snap.get("email", ""),
        "Facebook": snap.get("facebook", ""),
        "Instagram": snap.get("instagram", ""),
        "問い合わせフォームURL": snap.get("contact_form", ""),
        "LinkedIn": snap.get("linkedin", ""),
        "公式サイトURL": snap.get("official_url", ""),
        "掲載URL": source_url or "",
        "プラットフォーム": platform or "",
    }


def resolve_primary_channel(
    contact_snapshot: Dict[str, Any], source_url: str = "", platform: str = ""
) -> str:
    """連絡先候補から、既存のcontact_info.resolve_priority()と同じ優先順位
    （メール→Facebook→Instagram→問い合わせフォーム→LinkedIn→公式サイト→
    掲載URL→連絡先未取得）で1つのチャネルを選ぶ。戻り値はCHANNEL_OPTIONSの1つ"""
    row = _contact_snapshot_to_priority_row(contact_snapshot, source_url, platform)
    return contact_info.resolve_priority(row)["kind"]


def resolve_contact_display(
    contact_snapshot: Dict[str, Any], source_url: str = "", platform: str = ""
) -> Dict[str, Any]:
    """画面表示用に、連絡先候補から選んだ1件の全体（kind/email/url/label）を返す

    resolve_primary_channel()と同じ優先順位ロジック（contact_info.resolve_priority）
    を再利用し、UI側（cases_ui.py）に優先順位判定を重複させない。
    """
    row = _contact_snapshot_to_priority_row(contact_snapshot, source_url, platform)
    return contact_info.resolve_priority(row)


# =============================================================================
# データ構造
# =============================================================================

@dataclass
class Case:
    id: str = ""
    user_id: str = ""
    product_id: str = ""
    saved_item_id: Optional[str] = None
    platform: str = ""
    product_name: str = ""
    maker_name: str = ""
    source_url: str = ""
    contact_snapshot: Dict[str, Any] = field(default_factory=dict)
    phase: str = PHASE_REPLY
    status: str = STATUS_NOT_CONTACTED
    primary_channel: str = "none"
    first_sent_at: Optional[str] = None
    last_reply_at: Optional[str] = None
    advanced_at: Optional[str] = None
    has_reply: bool = False
    next_action_at: Optional[str] = None
    next_action_label: str = ""
    memo: str = ""
    archived: bool = False
    created_at: str = ""
    updated_at: str = ""


@dataclass
class CaseEvent:
    id: str = ""
    case_id: str = ""
    kind: str = ""
    channel: str = ""
    recipient: str = ""
    occurred_at: str = ""
    message_text: str = ""
    translated_text: str = ""
    ai_assist: Optional[Dict[str, Any]] = None
    target_status: Optional[str] = None
    voided_at: Optional[str] = None
    voided_reason: str = ""
    superseded_by: Optional[str] = None
    redacted_at: Optional[str] = None
    created_at: str = ""


@dataclass
class MonthTarget:
    user_id: str = ""
    target_month: str = ""
    approach_target: int = 0
    reply_target: int = 0


_CASE_SELECT = (
    "id,user_id,product_id,saved_item_id,platform,product_name,maker_name,source_url,"
    "contact_snapshot,phase,status,primary_channel,first_sent_at,last_reply_at,"
    "advanced_at,has_reply,next_action_at,next_action_label,memo,archived,"
    "created_at,updated_at"
)
_EVENT_SELECT = (
    "id,case_id,kind,channel,recipient,occurred_at,message_text,translated_text,"
    "ai_assist,target_status,voided_at,voided_reason,superseded_by,redacted_at,created_at"
)


def _to_case(row: Dict[str, Any]) -> Case:
    return Case(
        id=row.get("id", ""),
        user_id=row.get("user_id", ""),
        product_id=row.get("product_id", ""),
        saved_item_id=row.get("saved_item_id"),
        platform=row.get("platform") or "",
        product_name=row.get("product_name") or "（商品名不明）",
        maker_name=row.get("maker_name") or "不明",
        source_url=row.get("source_url") or "",
        contact_snapshot=row.get("contact_snapshot") or {},
        phase=row.get("phase") or PHASE_REPLY,
        status=row.get("status") or STATUS_NOT_CONTACTED,
        primary_channel=row.get("primary_channel") or "none",
        first_sent_at=row.get("first_sent_at"),
        last_reply_at=row.get("last_reply_at"),
        advanced_at=row.get("advanced_at"),
        has_reply=bool(row.get("has_reply", False)),
        next_action_at=row.get("next_action_at"),
        next_action_label=row.get("next_action_label") or "",
        memo=row.get("memo") or "",
        archived=bool(row.get("archived", False)),
        created_at=row.get("created_at") or "",
        updated_at=row.get("updated_at") or "",
    )


def _to_event(row: Dict[str, Any]) -> CaseEvent:
    return CaseEvent(
        id=row.get("id", ""),
        case_id=row.get("case_id", ""),
        kind=row.get("kind") or "",
        channel=row.get("channel") or "",
        recipient=row.get("recipient") or "",
        occurred_at=row.get("occurred_at") or "",
        message_text=row.get("message_text") or "",
        translated_text=row.get("translated_text") or "",
        ai_assist=row.get("ai_assist"),
        target_status=row.get("target_status"),
        voided_at=row.get("voided_at"),
        voided_reason=row.get("voided_reason") or "",
        superseded_by=row.get("superseded_by"),
        redacted_at=row.get("redacted_at"),
        created_at=row.get("created_at") or "",
    )


# =============================================================================
# 案件の作成
# =============================================================================

@dataclass
class CreateCaseResult:
    ok: bool
    case_id: str
    already_exists: bool
    message: str


def create_case_from_candidate(client, saved_item_id: str) -> CreateCaseResult:
    """マイ候補リストの1件(saved_item_id)から商品案件を作る

    書き込みは create_case_from_candidate() RPCのみ。本人の候補でなければ
    RPC内部で拒否される。同一利用者・同一商品の重複案件は作らない
    （二重クリックでも安全。RPCの unique制約 + on conflict do nothing）。
    """
    if not saved_item_id:
        return CreateCaseResult(False, "", False, CREATE_INVALID_CANDIDATE)
    try:
        response = client.rpc(CREATE_CASE_RPC, {"p_saved_item_id": saved_item_id}).execute()
    except Exception:
        return CreateCaseResult(False, "", False, CREATE_FAILED)

    data = getattr(response, "data", None) or {}
    if not isinstance(data, dict) or not data.get("case_id"):
        return CreateCaseResult(False, "", False, CREATE_FAILED)

    case_id = data["case_id"]
    already = bool(data.get("already_exists"))
    if not already:
        _apply_initial_primary_channel(client, case_id)
    return CreateCaseResult(True, case_id, already, "")


def _apply_initial_primary_channel(client, case_id: str) -> None:
    """作成直後の案件に、既存の優先順位ロジックで選んだ連絡チャネルを設定する

    失敗しても案件作成そのものは成功として扱う（primary_channelは表示用の
    付加情報であり、既定値'none'のままでも案件は使える）。
    """
    try:
        response = (client.table("cases")
                   .select("contact_snapshot,source_url,platform")
                   .eq("id", case_id)
                   .execute())
        rows = getattr(response, "data", None) or []
        if not rows:
            return
        row = rows[0]
        channel = resolve_primary_channel(
            row.get("contact_snapshot") or {}, row.get("source_url") or "",
            row.get("platform") or "")
        (client.table("cases")
              .update({"primary_channel": channel})
              .eq("id", case_id)
              .execute())
    except Exception:
        pass


# =============================================================================
# 案件・イベントの取得
# =============================================================================

def list_cases(client, user_id: str, include_archived: bool = False) -> List[Case]:
    """本人の案件一覧を取得する（新しい順）"""
    if not user_id:
        return []
    try:
        query = (client.table("cases")
                 .select(_CASE_SELECT)
                 .eq("user_id", user_id)
                 .order("created_at", desc=True))
        if not include_archived:
            query = query.eq("archived", False)
        response = query.execute()
    except Exception:
        return []
    rows = getattr(response, "data", None) or []
    return [_to_case(row) for row in rows if isinstance(row, dict)]


def get_case(client, user_id: str, case_id: str) -> Optional[Case]:
    """本人の案件1件を取得する"""
    if not user_id or not case_id:
        return None
    try:
        response = (client.table("cases")
                   .select(_CASE_SELECT)
                   .eq("id", case_id)
                   .eq("user_id", user_id)
                   .execute())
    except Exception:
        return None
    rows = getattr(response, "data", None) or []
    if not rows or not isinstance(rows[0], dict):
        return None
    return _to_case(rows[0])


_CASE_UPDATE_UNSET = object()  # next_action_at/next_action_label を「渡さなかった」ことを表す印


def update_case(client, user_id: str, case_id: str, *,
                memo: Any = None,
                next_action_at: Any = _CASE_UPDATE_UNSET,
                next_action_label: Any = _CASE_UPDATE_UNSET,
                archived: Any = None,
                primary_channel: Any = None) -> bool:
    """本人の案件1件を直接更新する

    変更できるのは memo / next_action_at / next_action_label / archived /
    primary_channel の5列だけ（sql/migrations/0009_cases_rls_grants.sqlの
    GRANT UPDATEと同じ5列）。status / has_reply / first_sent_at 等の状態機械が
    管理する列はここでは絶対に送らない（状態変更は必ずlog_event()のRPCを通す）。
    next_action_at に None を渡すと手動リマインダーを解除できる
    （candidates.update_saved_itemのpriority_override と同じ「渡さなかった」判定）。
    """
    if not case_id or not user_id:
        return False

    fields: Dict[str, Any] = {}
    if memo is not None:
        fields["memo"] = str(memo)[:5000]
    if next_action_at is not _CASE_UPDATE_UNSET:
        if next_action_at is None:
            fields["next_action_at"] = None
        elif isinstance(next_action_at, datetime):
            fields["next_action_at"] = next_action_at.astimezone(JST).date().isoformat()
        elif isinstance(next_action_at, date):
            fields["next_action_at"] = next_action_at.isoformat()
        else:
            return False
    if next_action_label is not _CASE_UPDATE_UNSET:
        fields["next_action_label"] = str(next_action_label or "")[:200]
    if archived is not None:
        fields["archived"] = bool(archived)
    if primary_channel is not None:
        if primary_channel not in CHANNEL_OPTIONS:
            return False
        fields["primary_channel"] = primary_channel
    if not fields:
        return True

    try:
        (client.table("cases")
              .update(fields)
              .eq("id", case_id)
              .eq("user_id", user_id)
              .execute())
        return True
    except Exception:
        return False


def list_case_events(client, user_id: str, case_id: str,
                     include_voided: bool = True) -> List[CaseEvent]:
    """本人の案件1件のイベント履歴を発生順に取得する

    既定では訂正・取消し済みの行も含める（タイムライン表示側で折りたたむ）。
    include_voided=False にすると有効なイベントだけに絞る。
    """
    if not user_id or not case_id:
        return []
    try:
        query = (client.table("case_events")
                 .select(_EVENT_SELECT)
                 .eq("case_id", case_id)
                 .eq("user_id", user_id)
                 .order("occurred_at"))
        if not include_voided:
            query = query.is_("voided_at", "null")
        response = query.execute()
    except Exception:
        return []
    rows = getattr(response, "data", None) or []
    return [_to_event(row) for row in rows if isinstance(row, dict)]


# =============================================================================
# イベントの記録・訂正・伏せ字化・取消し
# =============================================================================

def _is_sqlstate(exc: Exception, code: str) -> bool:
    """例外からPostgreSQLのSQLSTATEらしき値を取り出し、一致するか見る

    supabase-py（postgrest-py）の例外表現はバージョンにより異なるため、
    よくある属性名を順に試し、最後に文字列表現もあたる防御的な実装。
    """
    for attr in ("code", "pgcode", "sqlstate"):
        value = getattr(exc, attr, None)
        if value == code:
            return True
    details = getattr(exc, "args", None)
    if details:
        for item in details:
            if isinstance(item, dict) and item.get("code") == code:
                return True
    return code in str(exc)


def _is_invalid_transition(exc: Exception) -> bool:
    return _is_sqlstate(exc, "P0001") or "invalid_transition" in str(exc)


@dataclass
class LogEventResult:
    ok: bool
    event_id: str
    status: str
    next_action_at: Optional[str]
    next_action_label: str
    message: str


def log_event(client, case_id: str, kind: str, *,
              channel: str = "", recipient: str = "",
              occurred_at: Optional[datetime] = None,
              message_text: str = "", translated_text: str = "",
              new_status: Optional[str] = None) -> LogEventResult:
    """案件イベント（送信・フォロー・返信・条件確認/回答・メモ・手動の状態変更）
    を記録する。状態機械はDB側(log_case_event RPC)に委ねる。現在の状態と
    矛盾するイベント（例: 初回送信前のフォロー、重複フォロー）は拒否される"""
    if not case_id or kind not in EVENT_KIND_OPTIONS:
        return LogEventResult(False, "", "", None, "", LOG_EVENT_FAILED)
    if kind == EVENT_STATUS_CHANGE and new_status not in STATUS_CHANGE_TARGETS:
        return LogEventResult(False, "", "", None, "", LOG_EVENT_FAILED)

    payload = {
        "p_case_id": case_id,
        "p_kind": kind,
        "p_channel": channel or "",
        "p_recipient": recipient or "",
        "p_occurred_at": _iso(occurred_at or datetime.now(timezone.utc)),
        "p_message_text": message_text or "",
        "p_translated_text": translated_text or "",
        "p_new_status": new_status,
    }
    try:
        response = client.rpc(LOG_EVENT_RPC, payload).execute()
    except Exception as e:
        if _is_invalid_transition(e):
            return LogEventResult(False, "", "", None, "", LOG_EVENT_INVALID_TRANSITION)
        return LogEventResult(False, "", "", None, "", LOG_EVENT_FAILED)

    data = getattr(response, "data", None) or {}
    if not isinstance(data, dict) or not data.get("event_id"):
        return LogEventResult(False, "", "", None, "", LOG_EVENT_FAILED)

    return LogEventResult(
        True, data["event_id"], data.get("status", ""),
        data.get("next_action_at"), data.get("next_action_label", ""), ""
    )


@dataclass
class EventActionResult:
    ok: bool
    message: str
    new_event_id: Optional[str] = None


def void_event(client, event_id: str, reason: str = "") -> EventActionResult:
    """イベントを取消す（物理削除はしない）。後続の履歴と矛盾する取消しは
    拒否され、案件状態は一切変わらない（部分更新は残らない）"""
    if not event_id:
        return EventActionResult(False, VOID_FAILED)
    try:
        client.rpc(VOID_EVENT_RPC, {
            "p_event_id": event_id, "p_reason": reason or ""
        }).execute()
    except Exception as e:
        if _is_invalid_transition(e):
            return EventActionResult(False, VOID_BREAKS_HISTORY)
        return EventActionResult(False, VOID_FAILED)
    return EventActionResult(True, "")


def correct_event(client, event_id: str, message_text: str = "",
                  translated_text: str = "") -> EventActionResult:
    """本文を訂正する。元の行は取消し済みとして残り、訂正済みの新しい行が
    追加される（superseded_byで紐づく）"""
    if not event_id:
        return EventActionResult(False, CORRECT_FAILED)
    try:
        response = client.rpc(CORRECT_EVENT_RPC, {
            "p_event_id": event_id,
            "p_message_text": message_text or "",
            "p_translated_text": translated_text or "",
        }).execute()
    except Exception:
        return EventActionResult(False, CORRECT_FAILED)
    data = getattr(response, "data", None) or {}
    new_id = data.get("new_event_id") if isinstance(data, dict) else None
    if not new_id:
        return EventActionResult(False, CORRECT_FAILED)
    return EventActionResult(True, "", new_event_id=new_id)


def redact_event(client, event_id: str) -> EventActionResult:
    """機密情報を伏せ字化する（本文・翻訳・AI支援結果を復元不能な固定文言にする）"""
    if not event_id:
        return EventActionResult(False, REDACT_FAILED)
    try:
        client.rpc(REDACT_EVENT_RPC, {"p_event_id": event_id}).execute()
    except Exception:
        return EventActionResult(False, REDACT_FAILED)
    return EventActionResult(True, "")


# =============================================================================
# AI支援結果の保存（第1回実装ではAI APIは呼び出さない。保存先だけを用意する）
#
# 固定スキーマ:
#   {
#     "translated_text": str,       # 日本語訳
#     "intent_summary": str,        # 相手の意図の要約
#     "questions": [str, ...],      # 質問・確認事項の抽出
#     "sentiment": "positive"|"neutral"|"negative"|"unclear",  # 前向き度の目安
#     "suggested_reply_en": str,    # 次に送る英語返信案
#     "model": str,                 # 使用モデル（任意）
#     "generated_at": str,          # ISO8601（任意）
#   }
# =============================================================================

AI_ASSIST_REQUIRED_FIELDS = (
    "translated_text", "intent_summary", "questions", "sentiment", "suggested_reply_en",
)
AI_ASSIST_SENTIMENT_OPTIONS = ("positive", "neutral", "negative", "unclear")
AI_ASSIST_MAX_BYTES = 20000


def validate_ai_assist_payload(payload: Any) -> str:
    """AI支援結果が固定スキーマに沿っているか検証する

    問題があればその文言、問題が無ければ空文字を返す。断定表現
    （「必ず」「確実に」等）の検査は呼び出し側（将来のreply_assistant.py）の
    生成後処理で行う想定で、ここでは構造だけを見る。
    """
    if not isinstance(payload, dict):
        return AI_ASSIST_INVALID_PAYLOAD
    for field_name in AI_ASSIST_REQUIRED_FIELDS:
        if field_name not in payload:
            return f"AI支援結果に必須項目が不足しています（{field_name}）。"
    if not isinstance(payload.get("translated_text"), str):
        return AI_ASSIST_INVALID_PAYLOAD
    if not isinstance(payload.get("intent_summary"), str):
        return AI_ASSIST_INVALID_PAYLOAD
    if not isinstance(payload.get("questions"), list):
        return AI_ASSIST_INVALID_PAYLOAD
    if not all(isinstance(q, str) for q in payload.get("questions", [])):
        return AI_ASSIST_INVALID_PAYLOAD
    if payload.get("sentiment") not in AI_ASSIST_SENTIMENT_OPTIONS:
        return AI_ASSIST_INVALID_PAYLOAD
    if not isinstance(payload.get("suggested_reply_en"), str):
        return AI_ASSIST_INVALID_PAYLOAD
    try:
        import json
        if len(json.dumps(payload, ensure_ascii=False).encode("utf-8")) > AI_ASSIST_MAX_BYTES:
            return "AI支援結果が大きすぎます。"
    except (TypeError, ValueError):
        return AI_ASSIST_INVALID_PAYLOAD
    return ""


def save_ai_assist(client, event_id: str, payload: Dict[str, Any]) -> EventActionResult:
    """AI返信アシスタントの結果を保存する（対象は本人の受信イベントだけ。
    DB側のRPCも同じ制約を持つため、ここでの検証は二重の安全策）"""
    if not event_id:
        return EventActionResult(False, AI_ASSIST_SAVE_FAILED)
    problem = validate_ai_assist_payload(payload)
    if problem:
        return EventActionResult(False, problem)
    try:
        client.rpc(SAVE_AI_ASSIST_RPC, {
            "p_event_id": event_id, "p_ai_assist": payload
        }).execute()
    except Exception:
        return EventActionResult(False, AI_ASSIST_SAVE_FAILED)
    return EventActionResult(True, "")


# =============================================================================
# 月間目標（利用者ごと・月ごと）
# =============================================================================

_TARGET_SELECT = "user_id,target_month,approach_target,reply_target,updated_at"


def get_month_target(client, user_id: str, target_month: date) -> MonthTarget:
    """指定した月の目標を取得する。無ければ0件の既定値を返す（レコードを作らない）"""
    month_start = target_month.replace(day=1)
    if not user_id:
        return MonthTarget(user_id="", target_month=month_start.isoformat())
    try:
        response = (client.table("case_targets")
                   .select(_TARGET_SELECT)
                   .eq("user_id", user_id)
                   .eq("target_month", month_start.isoformat())
                   .execute())
        rows = getattr(response, "data", None) or []
    except Exception:
        rows = []
    if not rows or not isinstance(rows[0], dict):
        return MonthTarget(user_id=user_id, target_month=month_start.isoformat())
    row = rows[0]
    return MonthTarget(
        user_id=user_id,
        target_month=row.get("target_month") or month_start.isoformat(),
        approach_target=int(row.get("approach_target") or 0),
        reply_target=int(row.get("reply_target") or 0),
    )


def set_month_target(client, user_id: str, target_month: date,
                     approach_target: int, reply_target: int) -> bool:
    """指定した月の目標を保存する（upsert）。過去月の目標は別の行として残り、
    上書きされない（primary key = user_id, target_month）"""
    if not user_id:
        return False
    try:
        approach_target = max(0, min(int(approach_target), 100000))
        reply_target = max(0, min(int(reply_target), 100000))
    except (TypeError, ValueError):
        return False
    month_start = target_month.replace(day=1)
    try:
        (client.table("case_targets")
              .upsert({
                  "user_id": user_id,
                  "target_month": month_start.isoformat(),
                  "approach_target": approach_target,
                  "reply_target": reply_target,
              }, on_conflict="user_id,target_month")
              .execute())
        return True
    except Exception:
        return False


# =============================================================================
# ダッシュボード集計
#
# 集計定義（確定版。おすすめの定義として前段の調査報告で合意済み）:
#   - アプローチ数: その月に「初回送信」した案件数（フォローは含めない）
#   - 返信数: そのアプローチ数のコホートのうち、一度でも受信返信があった案件数
#            （返信自体は翌月以降に来てもよい。初回送信した月の実績に反映する）
#   - 返信率: 返信数 / アプローチ数
#   - 商談移行数: advanced_at が対象月(JST)に含まれる案件数
#   - 月の判定は常にJST。送信数・返信数は送信回数ではなく案件数で数える
# =============================================================================

@dataclass
class MonthlyStats:
    target_month: str
    approach_count: int
    reply_count: int
    reply_rate: Optional[float]
    advanced_count: int


def compute_monthly_stats(cases: Sequence[Case], target_month: date) -> MonthlyStats:
    """案件一覧(Caseのリスト)から、指定した月(JST)の実績を集計する

    DBへは問い合わせない純粋関数。呼び出し側がlist_cases()等で取得した
    一覧をそのまま渡す（archived分を含めるかどうかは呼び出し側の判断）。
    """
    month_start = target_month.replace(day=1)
    approach = 0
    replied = 0
    advanced = 0
    for c in cases:
        sent_month = to_jst_month_start(c.first_sent_at) if c.first_sent_at else None
        if sent_month == month_start:
            approach += 1
            if c.has_reply:
                replied += 1
        adv_month = to_jst_month_start(c.advanced_at) if c.advanced_at else None
        if adv_month == month_start:
            advanced += 1
    rate = (replied / approach) if approach else None
    return MonthlyStats(
        target_month=month_start.isoformat(),
        approach_count=approach, reply_count=replied,
        reply_rate=rate, advanced_count=advanced,
    )


# =============================================================================
# 今日やること（最大5件、決定論的）
#
# 優先順位（高い順）:
#   1. awaiting_our_reply（日付に関係なく常に対象）
#   2. awaiting_first_reply で next_action_at が今日より前（期限超過）
#   3. awaiting_first_reply で next_action_at が今日（本日期限）
#   4. not_contacted
#   5. awaiting_next_reply で next_action_at が設定済みかつ今日以前
# 同順位内は next_action_at が古いもの、次に created_at が古いものを優先する
# （next_action_atが無いグループ（1・4）はcreated_atだけで決まる）。
# =============================================================================

TODO_REASON_AWAITING_OUR_REPLY = "awaiting_our_reply"
TODO_REASON_FOLLOWUP_OVERDUE = "followup_overdue"
TODO_REASON_FOLLOWUP_DUE_TODAY = "followup_due_today"
TODO_REASON_NOT_CONTACTED = "not_contacted"
TODO_REASON_MANUAL_REMINDER = "manual_reminder"

TODO_REASON_LABELS_JA = {
    TODO_REASON_AWAITING_OUR_REPLY: "返信内容の確認・回答",
    TODO_REASON_FOLLOWUP_OVERDUE: "フォローアップ（期限超過）",
    TODO_REASON_FOLLOWUP_DUE_TODAY: "フォローアップ（本日期限）",
    TODO_REASON_NOT_CONTACTED: "未送信の商品へのアプローチ",
    TODO_REASON_MANUAL_REMINDER: "次回対応日（手動設定）",
}

_TODO_PRIORITY_ORDER = (
    TODO_REASON_AWAITING_OUR_REPLY, TODO_REASON_FOLLOWUP_OVERDUE,
    TODO_REASON_FOLLOWUP_DUE_TODAY, TODO_REASON_NOT_CONTACTED,
    TODO_REASON_MANUAL_REMINDER,
)


@dataclass
class TodoItem:
    case: Case
    reason: str
    label: str


def _created_at_sort_key(c: Case) -> str:
    return c.created_at or ""


def todays_todo(cases: Sequence[Case], today: Optional[date] = None,
                limit: int = 5) -> List[TodoItem]:
    """今日やることを最大limit件、決定論的な優先順位で返す（DBへは問い合わせない）

    archivedの案件は対象外（呼び出し側がlist_cases(include_archived=False)で
    渡す前提だが、ここでも二重に除外する）。
    """
    today = today or jst_today()
    buckets: Dict[str, List[Case]] = {k: [] for k in _TODO_PRIORITY_ORDER}

    for c in cases:
        if c.archived:
            continue
        next_at = to_jst_date(c.next_action_at) if c.next_action_at else None

        if c.status == STATUS_AWAITING_OUR_REPLY:
            buckets[TODO_REASON_AWAITING_OUR_REPLY].append(c)
        elif c.status == STATUS_AWAITING_FIRST_REPLY and next_at is not None and next_at < today:
            buckets[TODO_REASON_FOLLOWUP_OVERDUE].append(c)
        elif c.status == STATUS_AWAITING_FIRST_REPLY and next_at == today:
            buckets[TODO_REASON_FOLLOWUP_DUE_TODAY].append(c)
        elif c.status == STATUS_NOT_CONTACTED:
            buckets[TODO_REASON_NOT_CONTACTED].append(c)
        elif c.status == STATUS_AWAITING_NEXT_REPLY and next_at is not None and next_at <= today:
            buckets[TODO_REASON_MANUAL_REMINDER].append(c)

    items: List[TodoItem] = []
    for reason in _TODO_PRIORITY_ORDER:
        group = buckets[reason]
        group.sort(key=lambda c: (
            to_jst_date(c.next_action_at) or date.max, _created_at_sort_key(c)
        ))
        for c in group:
            items.append(TodoItem(case=c, reason=reason, label=TODO_REASON_LABELS_JA[reason]))
            if len(items) >= limit:
                return items
    return items
