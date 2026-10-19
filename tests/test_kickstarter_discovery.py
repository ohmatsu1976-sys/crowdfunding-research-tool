# -*- coding: utf-8 -*-
"""kickstarter_discovery.py の単体テスト（外部通信なし）

実行: python tests/test_kickstarter_discovery.py

Anthropicクライアントは偽物（スクリプト化したレスポンスを返すだけ）を使い、
実APIへは一切通信しない。requests.get も為替テストでは差し替える。
"""

import sys
from datetime import datetime, timedelta, timezone

from _harness import run  # noqa: E402
import kickstarter_discovery as kd  # noqa: E402

# ── URL抽出（web_search_tool_result ブロックだけから）─────────────────────────

def test_extract_urls_only_from_search_tool_result_block():
    """web_search_tool_result ブロックのURLだけを採用する"""
    message = {
        "content": [
            {"type": "server_tool_use", "name": "web_search"},
            {"type": "web_search_tool_result", "content": [
                {"type": "web_search_result",
                 "url": "https://www.kickstarter.com/projects/foo/bar-gadget",
                 "title": "Foo Bar Gadget"},
            ]},
            {"type": "text", "text": "I found a product at https://www.kickstarter.com/projects/evil/hallucinated"},
        ],
        "usage": {"input_tokens": 10, "output_tokens": 5},
    }
    urls = kd.extract_search_result_urls(message)
    assert urls == [("https://www.kickstarter.com/projects/foo/bar-gadget", "Foo Bar Gadget")], urls


def test_extract_urls_ignores_final_text_only_urls():
    """最終回答文だけにあるURL（モデルの生成）は無視する"""
    message = {
        "content": [
            {"type": "text",
             "text": "https://www.kickstarter.com/projects/fake/not-real"},
        ],
    }
    assert kd.extract_search_result_urls(message) == []


def test_extract_urls_handles_missing_content_gracefully():
    """ブロックが無いレスポンスでも例外にならない（SDK差異への耐性）"""
    assert kd.extract_search_result_urls({}) == []
    assert kd.extract_search_result_urls({"content": []}) == []


# ── URL正規化・除外・重複排除 ─────────────────────────────────────────────────

def test_normalize_candidate_url_accepts_individual_project_form():
    url = "https://www.kickstarter.com/projects/foo/bar-gadget"
    assert kd.normalize_candidate_url(url) == url


def test_normalize_candidate_url_strips_query_and_fragment():
    url = "https://www.kickstarter.com/projects/foo/bar-gadget?ref=discovery#rewards"
    assert kd.normalize_candidate_url(url) == "https://www.kickstarter.com/projects/foo/bar-gadget"


def test_normalize_candidate_url_strips_tab_suffix():
    url = "https://www.kickstarter.com/projects/foo/bar-gadget/description"
    assert kd.normalize_candidate_url(url) == "https://www.kickstarter.com/projects/foo/bar-gadget"


def test_normalize_candidate_url_rejects_discover_page():
    assert kd.normalize_candidate_url(
        "https://www.kickstarter.com/discover/advanced?state=live") is None


def test_normalize_candidate_url_rejects_blog_and_updates():
    assert kd.normalize_candidate_url("https://www.kickstarter.com/blog/introducing-x") is None
    assert kd.normalize_candidate_url(
        "https://updates.kickstarter.com/projects/foo/bar-gadget") is None


def test_normalize_candidate_url_rejects_other_domain():
    assert kd.normalize_candidate_url("https://www.indiegogo.com/projects/foo") is None


def test_normalize_candidate_url_rejects_profile_page():
    assert kd.normalize_candidate_url("https://www.kickstarter.com/profile/foo") is None


def test_filter_and_dedupe_removes_duplicates_and_malformed():
    raw = [
        ("https://www.kickstarter.com/projects/foo/bar", "live"),
        ("https://www.kickstarter.com/projects/foo/bar?ref=x", "live"),  # 同一商品(正規化後重複)
        ("https://www.kickstarter.com/discover/advanced", "live"),       # 除外対象
        ("https://www.kickstarter.com/projects/baz/qux", "ended"),
    ]
    accepted, excluded = kd.filter_and_dedupe_candidates(raw)
    assert accepted == [
        ("https://www.kickstarter.com/projects/foo/bar", "live"),
        ("https://www.kickstarter.com/projects/baz/qux", "ended"),
    ]
    reasons = {e["reason"] for e in excluded}
    assert "重複URL" in reasons
    assert any("対象外" in r for r in reasons)


def test_cap_candidates_respects_per_status_guideline_and_total():
    live = [(f"https://www.kickstarter.com/projects/x/live-{i}", "live") for i in range(6)]
    ended = [(f"https://www.kickstarter.com/projects/x/ended-{i}", "ended") for i in range(6)]
    kept, dropped = kd.cap_candidates(live + ended)
    assert len(kept) <= kd.MAX_CANDIDATES
    kept_live = [u for u, s in kept if s == "live"]
    assert len(kept_live) <= kd.PER_STATUS_GUIDELINE["live"]
    assert dropped, "上限超過がdropped扱いになっていない"


def test_cap_candidates_does_not_force_quota_when_category_lacks_candidates():
    """少ない区分を無理に埋めない（他区分の枠を奪わない）"""
    live = [("https://www.kickstarter.com/projects/x/live-0", "live")]
    ended = [(f"https://www.kickstarter.com/projects/x/ended-{i}", "ended") for i in range(5)]
    kept, _dropped = kd.cap_candidates(live + ended)
    kept_live = [u for u, s in kept if s == "live"]
    kept_ended = [u for u, s in kept if s == "ended"]
    assert len(kept_live) == 1
    assert len(kept_ended) == kd.PER_STATUS_GUIDELINE["ended"]


# ── 状態判定（Python側。モデルには計算させない）─────────────────────────────────

_NOW = datetime(2026, 10, 19, 6, 0, 0, tzinfo=timezone.utc)


def _product(**kwargs) -> kd.Product:
    base = dict(retrieved_at=_NOW.isoformat())
    base.update(kwargs)
    return kd.Product(**base)


def test_status_live_when_end_date_in_future():
    p = _product(end_date_iso=(_NOW + timedelta(days=5)).isoformat())
    kd.compute_status(p, now=_NOW)
    assert p.status == kd.STATUS_LIVE
    assert p.days_remaining == 5
    assert p.days_elapsed is None


def test_status_ended_when_end_date_in_past_and_no_late_pledge():
    p = _product(end_date_iso=(_NOW - timedelta(days=10)).isoformat())
    kd.compute_status(p, now=_NOW)
    assert p.status == kd.STATUS_ENDED
    assert p.days_elapsed == 10
    assert p.days_remaining is None


def test_status_late_pledge_when_end_date_past_but_confirmed():
    p = _product(end_date_iso=(_NOW - timedelta(days=3)).isoformat(),
                 late_pledge_confirmed=True)
    kd.compute_status(p, now=_NOW)
    assert p.status == kd.STATUS_LATE_PLEDGE


def test_status_unknown_when_no_end_date_or_days_text():
    p = _product()
    kd.compute_status(p, now=_NOW)
    assert p.status == kd.STATUS_UNKNOWN
    assert p.days_remaining is None and p.days_elapsed is None


def test_status_uses_days_left_text_when_no_iso_date():
    p = _product(days_left_text_raw="7")
    kd.compute_status(p, now=_NOW)
    assert p.status == kd.STATUS_LIVE
    assert p.days_remaining == 7


def test_status_mismatch_when_reported_text_contradicts_dates():
    """終了日時からは終了済みのはずなのに、ページが『live』と言っている場合"""
    p = _product(end_date_iso=(_NOW - timedelta(days=5)).isoformat(),
                 reported_status_text="Live, currently funding, 0 days to go")
    kd.compute_status(p, now=_NOW)
    assert p.status == kd.STATUS_MISMATCH
    assert p.status_mismatch_reason


def test_status_never_forces_upcoming_into_three_buckets():
    """未来日程(今後登場予定)はどのステータスにも該当しないため確認不能扱いになる"""
    p = _product()  # end_date無し → 推測しない
    kd.compute_status(p, now=_NOW)
    assert p.status == kd.STATUS_UNKNOWN


# ── 計算処理（達成率・日数）────────────────────────────────────────────────────

def test_percent_funded_computed_from_amounts():
    p = _product(raised_amount=50000, goal_amount=100000,
                 end_date_iso=(_NOW + timedelta(days=1)).isoformat())
    kd.compute_status(p, now=_NOW)
    assert p.percent_funded == 50.0


def test_percent_funded_none_when_goal_is_zero_or_missing():
    p = _product(raised_amount=100, goal_amount=0)
    kd.compute_status(p, now=_NOW)
    assert p.percent_funded is None
    p2 = _product(raised_amount=100, goal_amount=None)
    kd.compute_status(p2, now=_NOW)
    assert p2.percent_funded is None


def test_percent_funded_none_when_amount_missing():
    p = _product(raised_amount=None, goal_amount=100)
    kd.compute_status(p, now=_NOW)
    assert p.percent_funded is None


# ── 日本円換算（ECB、固定レート不使用）────────────────────────────────────────────

def test_convert_to_jpy_uses_cross_rate():
    rates = {"EUR": 1.0, "USD": 1.08, "JPY": 160.0}
    jpy, reason = kd.convert_to_jpy(100.0, "USD", rates)
    assert reason == ""
    assert round(jpy, 2) == round(100.0 / 1.08 * 160.0, 2)


def test_convert_to_jpy_passthrough_for_jpy():
    jpy, reason = kd.convert_to_jpy(5000.0, "JPY", {"EUR": 1.0, "JPY": 160.0})
    assert jpy == 5000.0 and reason == ""


def test_convert_to_jpy_unavailable_for_unsupported_currency():
    jpy, reason = kd.convert_to_jpy(100.0, "TWD", {"EUR": 1.0, "USD": 1.08, "JPY": 160.0})
    assert jpy is None and reason


def test_convert_to_jpy_unavailable_when_amount_missing():
    jpy, reason = kd.convert_to_jpy(None, "USD", {"EUR": 1.0, "USD": 1.08, "JPY": 160.0})
    assert jpy is None and reason == "金額未取得"


def test_convert_to_jpy_does_not_use_fixed_fallback_rate():
    """レート取得失敗時に固定レートへフォールバックしない"""
    jpy, reason = kd.convert_to_jpy(100.0, "USD", {})
    assert jpy is None
    assert "レート" in reason


def test_fetch_ecb_rates_handles_network_failure_without_fallback():
    import requests
    original_get = requests.get
    requests.get = lambda *a, **k: (_ for _ in ()).throw(
        requests.exceptions.ConnectionError("boom"))
    try:
        rates, date, error = kd.fetch_ecb_rates()
    finally:
        requests.get = original_get
    assert rates == {}
    assert date == ""
    assert error


# ── 検索・取得オーケストレーション（偽クライアント）───────────────────────────────

class _FakeMessages:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if not self._responses:
            raise AssertionError("予定より多くmessages.createが呼ばれた")
        resp = self._responses.pop(0)
        if isinstance(resp, Exception):
            raise resp
        return resp


class _FakeClient:
    def __init__(self, responses):
        self.messages = _FakeMessages(responses)


def _search_message(urls_with_titles):
    return {
        "content": [
            {"type": "web_search_tool_result", "content": [
                {"type": "web_search_result", "url": u, "title": t}
                for u, t in urls_with_titles
            ]},
            {"type": "text", "text": "ok"},
        ],
        "usage": {"input_tokens": 100, "output_tokens": 10,
                  "server_tool_use": {"web_search_requests": 1}},
    }


def _fetch_message(url, retrieved_at, fields):
    import json
    return {
        "content": [
            {"type": "web_fetch_tool_result", "content": {
                "url": url, "retrieved_at": retrieved_at,
            }},
            {"type": "text", "text": json.dumps(fields)},
        ],
        "usage": {"input_tokens": 200, "output_tokens": 50},
    }


def test_run_status_search_tags_candidates_with_status_and_calls_tool():
    client = _FakeClient([_search_message([
        ("https://www.kickstarter.com/projects/foo/bar", "Foo Bar"),
    ])])
    tagged, usage, error = kd.run_status_search(client, kd.STATUS_LIVE, "アウトドア調理器具")
    assert error == ""
    assert tagged == [("https://www.kickstarter.com/projects/foo/bar", kd.STATUS_LIVE)]
    tool = client.messages.calls[0]["tools"][0]
    assert tool["type"] == "web_search_20260209"
    assert tool["allowed_domains"] == ["kickstarter.com"]
    assert tool["allowed_callers"] == ["direct"]
    assert tool["max_uses"] == 1
    assert usage["input_tokens"] == 100


def test_run_status_search_returns_error_message_on_exception():
    client = _FakeClient([RuntimeError("rate limited")])
    tagged, usage, error = kd.run_status_search(client, kd.STATUS_LIVE, "x")
    assert tagged == []
    assert "rate limited" in error


def test_run_product_fetch_uses_cache_disabled_and_extracts_fields():
    url = "https://www.kickstarter.com/projects/foo/bar"
    fields = {
        "name": "Bar Gadget", "image_url": "https://img.example/x.jpg",
        "summary_ja": "便利な調理器具です。", "category_raw": "Product Design",
        "maker": "Foo Inc", "country": "US",
        "raised_amount": 50000, "currency": "USD", "goal_amount": 10000,
        "backers": 500, "launch_date": "2026-09-01",
        "end_date_iso": "2026-11-01", "days_left_text_raw": None,
        "late_pledge_confirmed": False, "reported_status_text": "live",
        "canonical_url": url,
    }
    client = _FakeClient([_fetch_message(url, "2026-10-19T06:00:00+00:00", fields)])
    product, usage, error = kd.run_product_fetch(client, url, kd.STATUS_LIVE)
    assert error == ""
    assert product.name == "Bar Gadget"
    assert product.raised_amount == 50000
    assert product.retrieved_at == "2026-10-19T06:00:00+00:00"
    tool = client.messages.calls[0]["tools"][0]
    assert tool["type"] == "web_fetch_20260309"
    assert tool["use_cache"] is False
    assert tool["allowed_callers"] == ["direct"]


def test_run_product_fetch_returns_none_without_fabricating_on_fetch_error():
    url = "https://www.kickstarter.com/projects/foo/bar"
    message = {
        "content": [
            {"type": "web_fetch_tool_result", "content": {"error_code": "too_many_requests"}},
        ],
        "usage": {"input_tokens": 10, "output_tokens": 0},
    }
    client = _FakeClient([message])
    product, usage, error = kd.run_product_fetch(client, url, kd.STATUS_LIVE)
    assert product is None
    assert "too_many_requests" in error


def test_run_product_fetch_returns_none_when_json_unparseable():
    url = "https://www.kickstarter.com/projects/foo/bar"
    message = {
        "content": [
            {"type": "web_fetch_tool_result", "content": {
                "url": url, "retrieved_at": "2026-10-19T06:00:00+00:00"}},
            {"type": "text", "text": "I could not extract structured data."},
        ],
        "usage": {"input_tokens": 10, "output_tokens": 5},
    }
    client = _FakeClient([message])
    product, usage, error = kd.run_product_fetch(client, url, kd.STATUS_LIVE)
    assert product is None
    assert error


def test_run_discovery_end_to_end_with_fake_client():
    """検索→抽出→取得→状態判定→JPY換算までの一連の流れ（実通信なし）"""
    live_url = "https://www.kickstarter.com/projects/foo/live-gadget"
    ended_url = "https://www.kickstarter.com/projects/foo/ended-gadget"

    search_live = _search_message([(live_url, "Live Gadget")])
    search_ended = _search_message([(ended_url, "Ended Gadget")])
    fetch_live = _fetch_message(live_url, "2026-10-19T06:00:00+00:00", {
        "name": "Live Gadget", "image_url": None, "summary_ja": "ライブ商品です。",
        "category_raw": "Gadgets", "maker": "Foo", "country": "US",
        "raised_amount": 20000, "currency": "USD", "goal_amount": 10000,
        "backers": 300, "launch_date": "2026-10-01",
        "end_date_iso": "2026-11-01", "days_left_text_raw": None,
        "late_pledge_confirmed": False, "reported_status_text": "live",
        "canonical_url": live_url,
    })
    fetch_ended = _fetch_message(ended_url, "2026-10-19T06:00:05+00:00", {
        "name": "Ended Gadget", "image_url": None, "summary_ja": "終了済み商品です。",
        "category_raw": "Product Design", "maker": "Bar", "country": "JP",
        "raised_amount": 80000, "currency": "USD", "goal_amount": 50000,
        "backers": 900, "launch_date": "2026-01-01",
        "end_date_iso": "2026-03-01", "days_left_text_raw": None,
        "late_pledge_confirmed": False, "reported_status_text": "funded",
        "canonical_url": ended_url,
    })

    client = _FakeClient([search_live, search_ended, fetch_live, fetch_ended])

    import requests
    original_get = requests.get

    class _FakeResp:
        status_code = 200
        content = (
            b'<?xml version="1.0" encoding="UTF-8"?>'
            b'<gesmes:Envelope xmlns:gesmes="http://www.gesmes.org/xml/2002-08-01" '
            b'xmlns="http://www.ecb.int/vocabulary/2002-08-01/eurofxref">'
            b'<Cube><Cube time="2026-10-19">'
            b'<Cube currency="USD" rate="1.08"/><Cube currency="JPY" rate="160.0"/>'
            b'</Cube></Cube></gesmes:Envelope>'
        )

    requests.get = lambda *a, **k: _FakeResp()
    try:
        result = kd.run_discovery(client, "アウトドア調理器具",
                                  [kd.STATUS_LIVE, kd.STATUS_ENDED],
                                  now=datetime(2026, 10, 19, 6, 0, 0, tzinfo=timezone.utc))
    finally:
        requests.get = original_get

    assert len(result.by_status[kd.STATUS_LIVE]) == 1
    assert len(result.by_status[kd.STATUS_ENDED]) == 1
    assert result.by_status[kd.STATUS_LIVE][0].name == "Live Gadget"
    assert result.by_status[kd.STATUS_ENDED][0].name == "Ended Gadget"
    assert result.by_status[kd.STATUS_ENDED][0].raised_amount_jpy is not None
    assert result.jpy_rate_date == "2026-10-19"
    assert result.usage["search_calls"] == 2
    assert result.usage["fetch_calls"] == 2


def test_run_discovery_respects_fetch_cap_of_ten():
    """Web Fetchは最大10回（3ステータス合計で候補が多くても超えない）"""
    def _urls(label, n):
        return [(f"https://www.kickstarter.com/projects/foo/{label}-{i}", f"{label} {i}")
                for i in range(n)]

    search_live = _search_message(_urls("live", 6))
    search_late = _search_message(_urls("late", 6))
    search_ended = _search_message(_urls("ended", 6))
    fetch_fields = {
        "name": "X", "image_url": None, "summary_ja": "x", "category_raw": "Gadgets",
        "maker": "M", "country": "US", "raised_amount": 1, "currency": "USD",
        "goal_amount": 1, "backers": 1, "launch_date": "2026-01-01",
        "end_date_iso": "2026-11-01", "days_left_text_raw": None,
        "late_pledge_confirmed": False, "reported_status_text": "live",
        "canonical_url": "https://www.kickstarter.com/projects/foo/live-0",
    }
    fetch_resp_factory = lambda: _fetch_message(
        "https://www.kickstarter.com/projects/foo/live-0",
        "2026-10-19T06:00:00+00:00", fetch_fields)

    responses = ([search_live, search_late, search_ended]
                + [fetch_resp_factory() for _ in range(kd.MAX_FETCH_CALLS)])
    client = _FakeClient(responses)

    import requests
    original_get = requests.get
    requests.get = lambda *a, **k: (_ for _ in ()).throw(
        requests.exceptions.ConnectionError("no network in test"))
    try:
        result = kd.run_discovery(
            client, "x", [kd.STATUS_LIVE, kd.STATUS_LATE_PLEDGE, kd.STATUS_ENDED],
            now=datetime(2026, 10, 19, 6, 0, 0, tzinfo=timezone.utc))
    finally:
        requests.get = original_get

    assert result.usage["fetch_calls"] == kd.MAX_FETCH_CALLS
    assert len(client.messages.calls) == 3 + kd.MAX_FETCH_CALLS  # 検索3回＋取得上限分のみ
    assert result.jpy_rate_error  # ネットワーク遮断時は固定レートへ逃げず未取得扱い


def test_run_discovery_search_limited_to_three_calls_total():
    """ステータスを多く選んでもWeb Search合計は最大3回"""
    assert kd.MAX_SEARCH_CALLS == 3


if __name__ == "__main__":
    sys.exit(run(dict(globals()), "kickstarter_discovery 単体テスト（外部通信なし）"))
