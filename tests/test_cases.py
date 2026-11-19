# -*- coding: utf-8 -*-
"""案件管理（第1段階「返信獲得」）のオフラインテスト

実行: python tests/test_cases.py

外部通信（Supabase・Anthropic）は一切行わない。偽クライアントで
cases.py の呼び出し方・応答の解釈・エラー文言への変換だけを検証する。

状態機械そのもの（3日後/7日後のフォロー期限、7状態の遷移規則、重複フォロー
防止）の正しさは、SQL側（sql/migrations/0008_cases_functions.sql）の
_case_apply_event() / _case_recompute() に実装されており、それらの構造は
tests/test_cases_sql_migrations.py が静的に検証する。ここで二重に状態機械を
Python側へ再実装すると、SQL側とロジックが分岐したときに気づけなくなるため、
意図的に行わない（product_key.py と 0002_functions.sql のドメイン一覧を
二重管理しないのと同じ考え方）。ここでは、cases.py が
  - RPCへ渡す引数の組み立て
  - RPCの戻り値／例外の解釈（特に invalid_transition の文言分け）
  - JST日付計算・集計・今日やることの優先順位（Python側だけに存在するロジック）
が正しいことを検証する。
"""

import sys
from datetime import date, datetime, timedelta, timezone

from _harness import run  # noqa: E402
import cases  # noqa: E402


# =============================================================================
# 偽クライアント（インメモリ。tests/test_candidates.pyと同じ考え方）
# =============================================================================

class _FakeExecResult:
    def __init__(self, data):
        self.data = data


class _FakePgError(Exception):
    """supabase-py(postgrest-py)の例外を模す。.code にSQLSTATEらしき値を持つ"""

    def __init__(self, message, code=None):
        super().__init__(message)
        self.code = code


class _FakeQuery:
    def __init__(self, store, kind, table, payload=None):
        self._store = store
        self._kind = kind
        self._table = table
        self._payload = payload
        self._filters = {}
        self._order = None
        self._is_null = {}

    def select(self, columns):
        return self

    def eq(self, column, value):
        self._filters[column] = value
        return self

    def is_(self, column, value):
        self._is_null[column] = value
        return self

    def order(self, column, desc=False):
        self._order = (column, desc)
        return self

    def upsert(self, payload, on_conflict=None):
        self._kind = "upsert"
        self._payload = payload
        self._payload_on_conflict = on_conflict
        return self

    def _matches(self, row):
        if not all(row.get(k) == v for k, v in self._filters.items()):
            return False
        for col, expected_null in self._is_null.items():
            is_null = row.get(col) is None
            if expected_null == "null" and not is_null:
                return False
        return True

    def execute(self):
        self._store.calls.append({
            "kind": self._kind, "table": self._table,
            "filters": dict(self._filters), "payload": self._payload,
        })
        rows = self._store.tables.setdefault(self._table, [])
        matched = [r for r in rows if self._matches(r)]

        if self._kind == "select":
            if self._order:
                col, desc = self._order
                matched = sorted(matched, key=lambda r: r.get(col) or "", reverse=desc)
            return _FakeExecResult([dict(r) for r in matched])

        if self._kind == "update":
            for row in matched:
                row.update(self._payload or {})
            return _FakeExecResult([dict(r) for r in matched])

        if self._kind == "upsert":
            key_cols = (self._payload_on_conflict or "").split(",")
            existing = next(
                (r for r in rows if all(r.get(c) == self._payload.get(c) for c in key_cols)),
                None)
            if existing is not None:
                existing.update(self._payload)
            else:
                rows.append(dict(self._payload))
            return _FakeExecResult([dict(self._payload)])

        raise AssertionError(f"未対応の操作: {self._kind}")


class _FakeTable:
    def __init__(self, store, name):
        self._store = store
        self._name = name

    def select(self, columns):
        return _FakeQuery(self._store, "select", self._name).select(columns)

    def update(self, payload):
        return _FakeQuery(self._store, "update", self._name, payload)

    def upsert(self, payload, on_conflict=None):
        return _FakeQuery(self._store, "upsert", self._name).upsert(payload, on_conflict)


class _FakeRpc:
    def __init__(self, store, name, params):
        self._store = store
        self._name = name
        self._params = params

    def execute(self):
        self._store.calls.append({"kind": "rpc", "name": self._name, "params": self._params})
        handler = self._store.rpc_handlers.get(self._name)
        if handler is None:
            raise AssertionError(f"未設定のRPC呼び出し: {self._name}")
        return handler(self._params)


class _FakeClient:
    def __init__(self, tables=None, rpc_handlers=None):
        self.tables = {k: [dict(r) for r in v] for k, v in (tables or {}).items()}
        self.rpc_handlers = rpc_handlers or {}
        self.calls = []

    def table(self, name):
        return _FakeTable(self, name)

    def rpc(self, name, params=None):
        return _FakeRpc(self, name, params)


def _rpc_calls(client, name):
    return [c for c in client.calls if c["kind"] == "rpc" and c["name"] == name]


# =============================================================================
# JST日付処理
# =============================================================================

def test_to_jst_date_converts_utc_evening_to_next_jst_day():
    """UTC夜のタイムスタンプはJSTでは翌日になる（+9時間）"""
    assert cases.to_jst_date("2026-01-01T16:00:00Z") == date(2026, 1, 2)


def test_to_jst_date_handles_plain_utc_offset_and_z_suffix():
    assert cases.to_jst_date("2026-03-05T00:00:00+00:00") == date(2026, 3, 5)
    assert cases.to_jst_date("2026-03-05T00:00:00Z") == date(2026, 3, 5)


def test_to_jst_date_returns_none_for_invalid_or_empty():
    assert cases.to_jst_date("") is None
    assert cases.to_jst_date(None) is None
    assert cases.to_jst_date("not-a-date") is None


def test_to_jst_month_start_crosses_month_boundary_in_jst():
    """UTC12/31 16:00 は JSTでは1/1 01:00 になり、月は1月として判定する"""
    assert cases.to_jst_month_start("2025-12-31T16:00:00Z") == date(2026, 1, 1)


def test_to_jst_month_start_stays_in_same_month_when_no_boundary_crossed():
    assert cases.to_jst_month_start("2025-12-31T14:00:00Z") == date(2025, 12, 1)


def test_to_jst_month_start_accepts_plain_date():
    assert cases.to_jst_month_start(date(2026, 5, 17)) == date(2026, 5, 1)


# =============================================================================
# 連絡先候補の優先順位（contact_info.resolve_priority()の再利用）
# =============================================================================

def test_resolve_primary_channel_prefers_email_over_everything():
    channel = cases.resolve_primary_channel({
        "email": "maker@example.com", "facebook": "https://facebook.com/x",
    })
    assert channel == "email"


def test_resolve_primary_channel_falls_back_in_documented_order():
    assert cases.resolve_primary_channel({"facebook": "https://facebook.com/x"}) == "facebook"
    assert cases.resolve_primary_channel({"instagram": "https://instagram.com/x"}) == "instagram"
    assert cases.resolve_primary_channel({"contact_form": "https://example.com/contact"}) == "form"
    assert cases.resolve_primary_channel({"linkedin": "https://linkedin.com/company/x"}) == "linkedin"
    assert cases.resolve_primary_channel({"official_url": "https://example.com"}) == "official"


def test_resolve_primary_channel_falls_back_to_listing_url_then_none():
    channel = cases.resolve_primary_channel({}, source_url="https://www.kickstarter.com/projects/a/b",
                                             platform="Kickstarter")
    assert channel == "listing"
    assert cases.resolve_primary_channel({}) == "none"


def test_resolve_primary_channel_rejects_placeholder_email():
    """「未確認」等のプレースホルダはメールとして使えない（contact_infoの検証をそのまま使う）"""
    channel = cases.resolve_primary_channel({"email": "未確認", "facebook": "https://facebook.com/x"})
    assert channel == "facebook"


# =============================================================================
# create_case_from_candidate
# =============================================================================

def test_create_case_rejects_empty_saved_item_id_without_calling_rpc():
    client = _FakeClient()
    result = cases.create_case_from_candidate(client, "")
    assert result.ok is False
    assert not client.calls


def test_create_case_sets_primary_channel_on_first_creation():
    client = _FakeClient(
        tables={"cases": [{
            "id": "case-1", "contact_snapshot": {"email": "maker@example.com"},
            "source_url": "https://www.kickstarter.com/projects/a/b", "platform": "Kickstarter",
        }]},
        rpc_handlers={
            cases.CREATE_CASE_RPC: lambda p: _FakeExecResult(
                {"case_id": "case-1", "already_exists": False}),
        },
    )
    result = cases.create_case_from_candidate(client, "saved-1")
    assert result.ok is True and result.case_id == "case-1" and result.already_exists is False
    updates = [c for c in client.calls if c["kind"] == "update" and c["table"] == "cases"]
    assert updates and updates[0]["payload"] == {"primary_channel": "email"}


def test_create_case_does_not_touch_primary_channel_when_already_exists():
    client = _FakeClient(
        rpc_handlers={
            cases.CREATE_CASE_RPC: lambda p: _FakeExecResult(
                {"case_id": "case-1", "already_exists": True}),
        },
    )
    result = cases.create_case_from_candidate(client, "saved-1")
    assert result.ok is True and result.already_exists is True
    assert not any(c["kind"] == "update" for c in client.calls)


def test_create_case_returns_safe_message_on_rpc_failure():
    client = _FakeClient(rpc_handlers={
        cases.CREATE_CASE_RPC: lambda p: (_ for _ in ()).throw(
            _FakePgError("duplicate key value violates unique constraint")),
    })
    result = cases.create_case_from_candidate(client, "saved-1")
    assert result.ok is False
    assert "duplicate key" not in result.message
    assert result.message == cases.CREATE_FAILED


# =============================================================================
# list_cases / get_case / list_case_events
# =============================================================================

def _case_row(**overrides):
    row = {
        "id": "case-1", "user_id": "user-1", "product_id": "prod-1", "saved_item_id": "saved-1",
        "platform": "Kickstarter", "product_name": "Example Gadget", "maker_name": "Example Co",
        "source_url": "https://www.kickstarter.com/projects/a/b", "contact_snapshot": {},
        "phase": "reply", "status": "not_contacted", "primary_channel": "none",
        "first_sent_at": None, "last_reply_at": None, "advanced_at": None, "has_reply": False,
        "next_action_at": None, "next_action_label": "", "memo": "", "archived": False,
        "created_at": "2026-11-01T00:00:00Z", "updated_at": "2026-11-01T00:00:00Z",
    }
    row.update(overrides)
    return row


def test_list_cases_filters_by_user_and_excludes_archived_by_default():
    client = _FakeClient(tables={"cases": [
        _case_row(id="a", user_id="user-1", archived=False),
        _case_row(id="b", user_id="user-1", archived=True),
        _case_row(id="c", user_id="user-2", archived=False),
    ]})
    result = cases.list_cases(client, "user-1")
    assert [c.id for c in result] == ["a"]


def test_list_cases_can_include_archived():
    client = _FakeClient(tables={"cases": [
        _case_row(id="a", user_id="user-1", archived=False),
        _case_row(id="b", user_id="user-1", archived=True),
    ]})
    result = cases.list_cases(client, "user-1", include_archived=True)
    assert {c.id for c in result} == {"a", "b"}


def test_get_case_returns_none_for_other_users_case():
    client = _FakeClient(tables={"cases": [_case_row(id="a", user_id="user-1")]})
    assert cases.get_case(client, "user-2", "a") is None
    assert cases.get_case(client, "user-1", "a") is not None


def test_list_case_events_can_exclude_voided():
    client = _FakeClient(tables={"case_events": [
        {"id": "e1", "case_id": "c1", "user_id": "u1", "kind": "initial",
         "channel": "", "recipient": "", "occurred_at": "2026-11-01T00:00:00Z",
         "message_text": "", "translated_text": "", "ai_assist": None,
         "target_status": None, "voided_at": None, "voided_reason": "",
         "superseded_by": None, "redacted_at": None, "created_at": "2026-11-01T00:00:00Z"},
        {"id": "e2", "case_id": "c1", "user_id": "u1", "kind": "followup1",
         "channel": "", "recipient": "", "occurred_at": "2026-11-04T00:00:00Z",
         "message_text": "", "translated_text": "", "ai_assist": None,
         "target_status": None, "voided_at": "2026-11-05T00:00:00Z", "voided_reason": "mistake",
         "superseded_by": None, "redacted_at": None, "created_at": "2026-11-04T00:00:00Z"},
    ]})
    all_events = cases.list_case_events(client, "u1", "c1")
    assert {e.id for e in all_events} == {"e1", "e2"}
    active_only = cases.list_case_events(client, "u1", "c1", include_voided=False)
    assert {e.id for e in active_only} == {"e1"}


# =============================================================================
# log_event
# =============================================================================

def test_log_event_rejects_unknown_kind_without_calling_rpc():
    client = _FakeClient()
    result = cases.log_event(client, "case-1", "bogus_kind")
    assert result.ok is False
    assert not _rpc_calls(client, cases.LOG_EVENT_RPC)


def test_log_event_rejects_status_change_with_invalid_target_without_calling_rpc():
    client = _FakeClient()
    result = cases.log_event(client, "case-1", cases.EVENT_STATUS_CHANGE, new_status="bogus")
    assert result.ok is False
    assert not _rpc_calls(client, cases.LOG_EVENT_RPC)


def test_log_event_success_parses_status_and_next_action():
    client = _FakeClient(rpc_handlers={
        cases.LOG_EVENT_RPC: lambda p: _FakeExecResult({
            "event_id": "ev-1", "case_id": "case-1",
            "status": "awaiting_first_reply",
            "next_action_at": "2026-11-04", "next_action_label": "1回目フォロー",
        }),
    })
    result = cases.log_event(client, "case-1", cases.EVENT_INITIAL)
    assert result.ok is True
    assert result.status == "awaiting_first_reply"
    assert result.next_action_label == "1回目フォロー"
    call = _rpc_calls(client, cases.LOG_EVENT_RPC)[0]
    assert call["params"]["p_case_id"] == "case-1"
    assert call["params"]["p_kind"] == cases.EVENT_INITIAL


def test_log_event_translates_invalid_transition_to_friendly_message():
    client = _FakeClient(rpc_handlers={
        cases.LOG_EVENT_RPC: lambda p: (_ for _ in ()).throw(
            _FakePgError("invalid_transition", code="P0001")),
    })
    result = cases.log_event(client, "case-1", cases.EVENT_FOLLOWUP1)
    assert result.ok is False
    assert result.message == cases.LOG_EVENT_INVALID_TRANSITION


def test_log_event_generic_failure_does_not_leak_db_error():
    client = _FakeClient(rpc_handlers={
        cases.LOG_EVENT_RPC: lambda p: (_ for _ in ()).throw(
            RuntimeError("connection refused: db.example.internal:5432")),
    })
    result = cases.log_event(client, "case-1", cases.EVENT_INITIAL)
    assert result.ok is False
    assert "db.example.internal" not in result.message
    assert result.message == cases.LOG_EVENT_FAILED


# =============================================================================
# void_event / correct_event / redact_event
# =============================================================================

def test_void_event_success():
    client = _FakeClient(rpc_handlers={cases.VOID_EVENT_RPC: lambda p: _FakeExecResult({})})
    result = cases.void_event(client, "ev-1", reason="misclick")
    assert result.ok is True
    assert _rpc_calls(client, cases.VOID_EVENT_RPC)[0]["params"]["p_reason"] == "misclick"


def test_void_event_distinguishes_history_conflict_from_generic_failure():
    conflict_client = _FakeClient(rpc_handlers={
        cases.VOID_EVENT_RPC: lambda p: (_ for _ in ()).throw(
            _FakePgError("invalid_transition", code="P0001")),
    })
    result = cases.void_event(conflict_client, "ev-1")
    assert result.ok is False
    assert result.message == cases.VOID_BREAKS_HISTORY

    generic_client = _FakeClient(rpc_handlers={
        cases.VOID_EVENT_RPC: lambda p: (_ for _ in ()).throw(RuntimeError("boom")),
    })
    result2 = cases.void_event(generic_client, "ev-1")
    assert result2.ok is False
    assert result2.message == cases.VOID_FAILED
    assert result2.message != cases.VOID_BREAKS_HISTORY


def test_correct_event_returns_new_event_id():
    client = _FakeClient(rpc_handlers={
        cases.CORRECT_EVENT_RPC: lambda p: _FakeExecResult(
            {"old_event_id": "ev-1", "new_event_id": "ev-2", "case_id": "c1"}),
    })
    result = cases.correct_event(client, "ev-1", message_text="fixed text")
    assert result.ok is True and result.new_event_id == "ev-2"


def test_redact_event_calls_rpc_with_event_id_only():
    client = _FakeClient(rpc_handlers={cases.REDACT_EVENT_RPC: lambda p: _FakeExecResult({})})
    result = cases.redact_event(client, "ev-1")
    assert result.ok is True
    assert _rpc_calls(client, cases.REDACT_EVENT_RPC)[0]["params"] == {"p_event_id": "ev-1"}


# =============================================================================
# AI支援結果の保存（固定スキーマの検証）
# =============================================================================

def _valid_ai_assist():
    return {
        "translated_text": "日本語訳です。",
        "intent_summary": "価格と最小発注数を聞きたい様子。",
        "questions": ["MOQはいくつですか？", "独占販売は可能ですか？"],
        "sentiment": "positive",
        "suggested_reply_en": "Thank you for your reply...",
    }


def test_save_ai_assist_accepts_valid_payload():
    client = _FakeClient(rpc_handlers={
        cases.SAVE_AI_ASSIST_RPC: lambda p: _FakeExecResult({"event_id": "ev-1", "saved": True}),
    })
    result = cases.save_ai_assist(client, "ev-1", _valid_ai_assist())
    assert result.ok is True


def test_save_ai_assist_rejects_missing_field_without_calling_rpc():
    client = _FakeClient()
    payload = _valid_ai_assist()
    del payload["sentiment"]
    result = cases.save_ai_assist(client, "ev-1", payload)
    assert result.ok is False
    assert not _rpc_calls(client, cases.SAVE_AI_ASSIST_RPC)


def test_save_ai_assist_rejects_invalid_sentiment_value():
    client = _FakeClient()
    payload = _valid_ai_assist()
    payload["sentiment"] = "very_positive"
    result = cases.save_ai_assist(client, "ev-1", payload)
    assert result.ok is False
    assert not _rpc_calls(client, cases.SAVE_AI_ASSIST_RPC)


def test_save_ai_assist_rejects_non_list_questions():
    client = _FakeClient()
    payload = _valid_ai_assist()
    payload["questions"] = "MOQはいくつですか？"
    result = cases.save_ai_assist(client, "ev-1", payload)
    assert result.ok is False


def test_save_ai_assist_rejects_oversized_payload():
    client = _FakeClient()
    payload = _valid_ai_assist()
    payload["translated_text"] = "あ" * 30000
    result = cases.save_ai_assist(client, "ev-1", payload)
    assert result.ok is False


def test_validate_ai_assist_payload_rejects_non_dict():
    assert cases.validate_ai_assist_payload("not a dict") != ""
    assert cases.validate_ai_assist_payload(None) != ""


# =============================================================================
# 月間目標（月別保存）
# =============================================================================

def test_set_month_target_upserts_with_user_and_month_as_conflict_key():
    client = _FakeClient()
    ok = cases.set_month_target(client, "user-1", date(2026, 11, 15), 20, 5)
    assert ok is True
    upserts = [c for c in client.calls if c["kind"] == "upsert"]
    assert len(upserts) == 1
    payload = upserts[0]["payload"]
    assert payload["target_month"] == "2026-11-01"
    assert payload["approach_target"] == 20 and payload["reply_target"] == 5


def test_set_month_target_clamps_negative_and_excessive_values():
    client = _FakeClient()
    cases.set_month_target(client, "user-1", date(2026, 11, 1), -5, 999999)
    payload = [c for c in client.calls if c["kind"] == "upsert"][0]["payload"]
    assert payload["approach_target"] == 0
    assert payload["reply_target"] == 100000


def test_get_month_target_returns_zero_defaults_when_no_row():
    client = _FakeClient(tables={"case_targets": []})
    target = cases.get_month_target(client, "user-1", date(2026, 11, 1))
    assert target.approach_target == 0 and target.reply_target == 0


def test_get_month_target_does_not_overwrite_past_months():
    """過去月の目標を更新しても、別の月の行は変わらない（別の一意キー）"""
    client = _FakeClient()
    cases.set_month_target(client, "user-1", date(2026, 9, 1), 10, 3)
    cases.set_month_target(client, "user-1", date(2026, 11, 1), 20, 5)
    sep = cases.get_month_target(client, "user-1", date(2026, 9, 1))
    nov = cases.get_month_target(client, "user-1", date(2026, 11, 1))
    assert sep.approach_target == 10 and nov.approach_target == 20


# =============================================================================
# ダッシュボード集計（コホート方式の返信率）
# =============================================================================

def _case(**overrides):
    row = dict(
        id="x", status="not_contacted", has_reply=False,
        first_sent_at=None, advanced_at=None, archived=False,
        created_at="2026-11-01T00:00:00Z", next_action_at=None,
    )
    row.update(overrides)
    return cases.Case(**row)


def test_monthly_stats_counts_cases_not_send_events():
    """送信数は案件数で数える（同一案件に複数回送信しても1件のまま）"""
    case_list = [_case(id="a", first_sent_at="2026-11-02T00:00:00Z", has_reply=False)]
    stats = cases.compute_monthly_stats(case_list, date(2026, 11, 1))
    assert stats.approach_count == 1


def test_monthly_stats_reply_counts_toward_the_month_the_case_was_first_sent():
    """返信は翌月に来ても、初回送信した月のコホートの実績として数える"""
    case_list = [_case(id="a", first_sent_at="2026-11-28T00:00:00Z",
                       has_reply=True)]
    nov_stats = cases.compute_monthly_stats(case_list, date(2026, 11, 1))
    dec_stats = cases.compute_monthly_stats(case_list, date(2026, 12, 1))
    assert nov_stats.approach_count == 1 and nov_stats.reply_count == 1
    assert dec_stats.approach_count == 0 and dec_stats.reply_count == 0


def test_monthly_stats_reply_rate_is_none_when_no_approach():
    stats = cases.compute_monthly_stats([], date(2026, 11, 1))
    assert stats.approach_count == 0
    assert stats.reply_rate is None


def test_monthly_stats_reply_rate_is_computed_from_cohort():
    case_list = [
        _case(id="a", first_sent_at="2026-11-01T00:00:00Z", has_reply=True),
        _case(id="b", first_sent_at="2026-11-02T00:00:00Z", has_reply=False),
    ]
    stats = cases.compute_monthly_stats(case_list, date(2026, 11, 1))
    assert stats.approach_count == 2 and stats.reply_count == 1
    assert abs(stats.reply_rate - 0.5) < 1e-9


def test_monthly_stats_advanced_count_uses_advanced_at_independently():
    case_list = [
        _case(id="a", first_sent_at="2026-10-01T00:00:00Z", has_reply=True,
              advanced_at="2026-11-05T00:00:00Z"),
    ]
    stats = cases.compute_monthly_stats(case_list, date(2026, 11, 1))
    assert stats.approach_count == 0  # 送信は10月なので11月のアプローチ数には入らない
    assert stats.advanced_count == 1  # 商談移行は11月なので数える


# =============================================================================
# 今日やること（最大5件、決定論的な優先順位）
# =============================================================================

def test_todays_todo_prioritizes_awaiting_our_reply_first():
    today = date(2026, 11, 10)
    case_list = [
        _case(id="a", status=cases.STATUS_NOT_CONTACTED, created_at="2026-11-01T00:00:00Z"),
        _case(id="b", status=cases.STATUS_AWAITING_OUR_REPLY, created_at="2026-11-05T00:00:00Z"),
    ]
    todo = cases.todays_todo(case_list, today=today)
    assert todo[0].case.id == "b"
    assert todo[0].reason == cases.TODO_REASON_AWAITING_OUR_REPLY


def test_todays_todo_overdue_followup_before_due_today_before_not_contacted():
    today = date(2026, 11, 10)
    case_list = [
        _case(id="not_contacted", status=cases.STATUS_NOT_CONTACTED,
              created_at="2026-11-01T00:00:00Z"),
        _case(id="due_today", status=cases.STATUS_AWAITING_FIRST_REPLY,
              next_action_at="2026-11-10", created_at="2026-11-02T00:00:00Z"),
        _case(id="overdue", status=cases.STATUS_AWAITING_FIRST_REPLY,
              next_action_at="2026-11-05", created_at="2026-11-03T00:00:00Z"),
    ]
    todo = cases.todays_todo(case_list, today=today)
    assert [t.case.id for t in todo] == ["overdue", "due_today", "not_contacted"]


def test_todays_todo_excludes_future_followup_and_future_manual_reminder():
    today = date(2026, 11, 10)
    case_list = [
        _case(id="future_followup", status=cases.STATUS_AWAITING_FIRST_REPLY,
              next_action_at="2026-11-15"),
        _case(id="future_reminder", status=cases.STATUS_AWAITING_NEXT_REPLY,
              next_action_at="2026-11-15"),
        _case(id="no_reminder_set", status=cases.STATUS_AWAITING_NEXT_REPLY,
              next_action_at=None),
    ]
    todo = cases.todays_todo(case_list, today=today)
    assert todo == []


def test_todays_todo_includes_due_manual_reminder_on_awaiting_next_reply():
    today = date(2026, 11, 10)
    case_list = [_case(id="due_reminder", status=cases.STATUS_AWAITING_NEXT_REPLY,
                       next_action_at="2026-11-09")]
    todo = cases.todays_todo(case_list, today=today)
    assert len(todo) == 1 and todo[0].reason == cases.TODO_REASON_MANUAL_REMINDER


def test_todays_todo_excludes_archived_cases():
    today = date(2026, 11, 10)
    case_list = [_case(id="a", status=cases.STATUS_AWAITING_OUR_REPLY, archived=True)]
    assert cases.todays_todo(case_list, today=today) == []


def test_todays_todo_excludes_terminal_statuses():
    today = date(2026, 11, 10)
    case_list = [
        _case(id="a", status=cases.STATUS_ADVANCED),
        _case(id="b", status=cases.STATUS_ON_HOLD),
        _case(id="c", status=cases.STATUS_LOST),
    ]
    assert cases.todays_todo(case_list, today=today) == []


def test_todays_todo_caps_at_five_total_across_all_priority_groups():
    today = date(2026, 11, 10)
    case_list = [
        _case(id=f"our_reply_{i}", status=cases.STATUS_AWAITING_OUR_REPLY,
              created_at=f"2026-11-{i:02d}T00:00:00Z")
        for i in range(1, 4)
    ] + [
        _case(id=f"not_contacted_{i}", status=cases.STATUS_NOT_CONTACTED,
              created_at=f"2026-11-{i:02d}T00:00:00Z")
        for i in range(1, 4)
    ]
    todo = cases.todays_todo(case_list, today=today, limit=5)
    assert len(todo) == 5
    assert all(t.reason == cases.TODO_REASON_AWAITING_OUR_REPLY for t in todo[:3])


def test_todays_todo_breaks_ties_by_oldest_next_action_at_then_oldest_created_at():
    today = date(2026, 11, 10)
    case_list = [
        _case(id="newer_due", status=cases.STATUS_AWAITING_FIRST_REPLY,
              next_action_at="2026-11-08", created_at="2026-11-05T00:00:00Z"),
        _case(id="older_due", status=cases.STATUS_AWAITING_FIRST_REPLY,
              next_action_at="2026-11-05", created_at="2026-11-06T00:00:00Z"),
    ]
    todo = cases.todays_todo(case_list, today=today)
    assert [t.case.id for t in todo] == ["older_due", "newer_due"]


# =============================================================================
# 例外からのSQLSTATE判定（_is_invalid_transition）
# =============================================================================

def test_is_invalid_transition_detects_code_attribute():
    assert cases._is_invalid_transition(_FakePgError("x", code="P0001")) is True


def test_is_invalid_transition_detects_message_substring_fallback():
    assert cases._is_invalid_transition(RuntimeError("invalid_transition happened")) is True


def test_is_invalid_transition_false_for_unrelated_errors():
    assert cases._is_invalid_transition(RuntimeError("connection refused")) is False
    assert cases._is_invalid_transition(_FakePgError("not authenticated", code="28000")) is False


if __name__ == "__main__":
    sys.exit(run(dict(globals()), "案件管理オフラインテスト"))
