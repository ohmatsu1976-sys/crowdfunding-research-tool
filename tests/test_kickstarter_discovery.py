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
    tagged, titles, usage, error = kd.run_status_search(client, kd.STATUS_LIVE, "アウトドア調理器具")
    assert error == ""
    assert tagged == [("https://www.kickstarter.com/projects/foo/bar", kd.STATUS_LIVE)]
    assert titles == {"https://www.kickstarter.com/projects/foo/bar": "Foo Bar"}
    tool = client.messages.calls[0]["tools"][0]
    assert tool["type"] == "web_search_20260209"
    assert tool["allowed_domains"] == ["kickstarter.com"]
    assert tool["allowed_callers"] == ["direct"]
    assert tool["max_uses"] == 1
    assert usage["input_tokens"] == 100


def test_run_status_search_returns_error_message_on_exception():
    client = _FakeClient([RuntimeError("rate limited")])
    tagged, titles, usage, error = kd.run_status_search(client, kd.STATUS_LIVE, "x")
    assert tagged == []
    assert titles == {}
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

    # 候補カードは個別ページ取得の成否に関わらず常に作られ、条件一致が
    # 確認できたものだけにcondition_matchedが立つ
    assert len(result.candidate_cards) == 2
    urls = {c.url for c in result.candidate_cards}
    assert urls == {live_url, ended_url}
    for card in result.candidate_cards:
        assert card.detail_confirmed is True
        assert card.condition_matched is True
        assert card.product is not None


# ── 候補カード：個別ページ取得の成否に関わらず一覧表示できること ──────────────

def test_candidate_cards_are_built_even_when_all_fetches_fail():
    """8件見つかっても個別取得が全滅した場合でも、8件の候補カードが作られる
    （本番で報告された『候補8件・個別取得0件』の再現と修正確認）

    件数配分はステータスごとの上限目安（ライブ4／遅延プレッジ3／終了済み3）に
    収まるようにする（上限自体は別テストで確認済み）。
    """
    def _urls(label, n):
        return [(f"https://www.kickstarter.com/projects/foo/{label}-item-{i}", f"{label} {i}")
                for i in range(n)]

    search_live = _search_message(_urls("live", 3))
    search_late = _search_message(_urls("late", 3))
    search_ended = _search_message(_urls("ended", 2))
    fail_resp = {
        "content": [
            {"type": "web_fetch_tool_result", "content": {"error_code": "url_not_accessible"}},
        ],
        "usage": {"input_tokens": 10, "output_tokens": 0},
    }
    client = _FakeClient(
        [search_live, search_late, search_ended] + [fail_resp for _ in range(8)])
    result = kd.run_discovery(
        client, "x", [kd.STATUS_LIVE, kd.STATUS_LATE_PLEDGE, kd.STATUS_ENDED],
        now=datetime(2026, 10, 19, 6, 0, 0, tzinfo=timezone.utc))

    assert result.candidate_count == 8
    assert len(result.candidate_cards) == 8
    assert result.fetch_success_count == 0
    for card in result.candidate_cards:
        assert card.title, f"タイトルが空になっている: {card.url}"
        assert card.detail_confirmed is False
        assert card.condition_matched is False
        assert card.product is None


def test_candidate_card_title_is_empty_not_fabricated_when_search_has_no_title():
    """検索結果にタイトルが無い場合、架空の商品名を作らず空文字のままにする"""
    search_resp = _search_message([
        ("https://www.kickstarter.com/projects/foo/no-title-item", ""),
    ])
    fail_resp = {
        "content": [
            {"type": "web_fetch_tool_result", "content": {"error_code": "url_not_accessible"}},
        ],
        "usage": {"input_tokens": 10, "output_tokens": 0},
    }
    client = _FakeClient([search_resp, fail_resp])
    result = kd.run_discovery(client, "x", [kd.STATUS_LIVE],
                              now=datetime(2026, 10, 19, 6, 0, 0, tzinfo=timezone.utc))
    assert len(result.candidate_cards) == 1
    assert result.candidate_cards[0].title == ""


def test_candidate_card_not_condition_matched_when_excluded_by_category():
    """個別取得に成功しても、カテゴリー等の条件に合わなければcondition_matchedにしない"""
    url = "https://www.kickstarter.com/projects/foo/not-tech-gadget"
    search_resp = _search_message([(url, "Some Gadget")])
    fetch_resp = _fetch_message(url, "2026-10-19T06:00:00+00:00", {
        "name": "Hand Knit Scarf", "image_url": None, "summary_ja": "手編みマフラーです。",
        "category_raw": "Knitting", "maker": "M", "country": "US",
        "raised_amount": 20000, "currency": "USD", "goal_amount": 10000, "backers": 100,
        "launch_date": "2026-01-01", "end_date_iso": "2026-03-01",
        "days_left_text_raw": None, "late_pledge_confirmed": False,
        "reported_status_text": "funded", "canonical_url": url,
    })
    client = _FakeClient([search_resp, fetch_resp])
    result = kd.run_discovery(client, "x", [kd.STATUS_ENDED], category="Technology",
                              now=datetime(2026, 10, 19, 6, 0, 0, tzinfo=timezone.utc))
    assert len(result.candidate_cards) == 1
    card = result.candidate_cards[0]
    assert card.detail_confirmed is True
    assert card.condition_matched is False
    assert card.product is not None
    assert card.product.exclusion_reason == "選択カテゴリーと不一致"


def test_candidate_cards_exclude_invalid_and_duplicate_urls():
    """不正なURL・重複URLは候補カードに含まれない（正規化・重複排除を経由する）"""
    valid_url = "https://www.kickstarter.com/projects/foo/valid-item"
    search_resp = _search_message([
        (valid_url, "Valid Item"),
        (valid_url + "?ref=share", "Valid Item (dup via query string)"),
        ("https://www.kickstarter.com/discover/categories/technology", "Not a project page"),
        ("https://example.com/not-kickstarter", "Unrelated site"),
    ])
    fail_resp = {
        "content": [
            {"type": "web_fetch_tool_result", "content": {"error_code": "url_not_accessible"}},
        ],
        "usage": {"input_tokens": 10, "output_tokens": 0},
    }
    client = _FakeClient([search_resp, fail_resp])
    result = kd.run_discovery(client, "x", [kd.STATUS_LIVE],
                              now=datetime(2026, 10, 19, 6, 0, 0, tzinfo=timezone.utc))
    assert result.candidate_count == 1
    assert len(result.candidate_cards) == 1
    assert result.candidate_cards[0].url == valid_url
    assert len(result.excluded_duplicate) == 1
    assert len(result.excluded_invalid_url) == 2


def test_candidate_cards_empty_when_no_candidates_found():
    search_resp = _search_message([])
    client = _FakeClient([search_resp])
    result = kd.run_discovery(client, "x", [kd.STATUS_LIVE],
                              now=datetime(2026, 10, 19, 6, 0, 0, tzinfo=timezone.utc))
    assert result.candidate_count == 0
    assert result.candidate_cards == []


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


# ── カテゴリー：日本語↔英語の対応 ─────────────────────────────────────────────

def test_category_labels_cover_all_fifteen_parents_plus_all():
    """日本語カテゴリー表記が15カテゴリー＋「すべて」の16件ぶん揃っている"""
    assert len(kd.CATEGORY_OPTIONS) == 16
    assert kd.CATEGORY_OPTIONS[0] == kd.CATEGORY_ALL
    for code in kd.CATEGORY_OPTIONS:
        assert code in kd.CATEGORY_LABELS_JA, f"{code} の日本語表記が無い"
    assert kd.CATEGORY_LABELS_JA[kd.CATEGORY_ALL] == "すべて"
    assert kd.CATEGORY_LABELS_JA["Technology"] == "テクノロジー"
    assert kd.CATEGORY_LABELS_JA["Film & Video"] == "映画・映像"


def test_category_passed_into_search_prompt():
    """選択したカテゴリーが検索プロンプトへ渡る（英語名で）"""
    prompt = kd._search_prompt(kd.STATUS_LIVE, "x", category="Technology")
    assert "Technology" in prompt
    prompt_all = kd._search_prompt(kd.STATUS_LIVE, "x", category=kd.CATEGORY_ALL)
    assert "Prefer Kickstarter's official category" not in prompt_all


def test_amount_range_passed_into_search_prompt_as_guidance_only():
    """支援総額の条件が検索プロンプトへ案内として渡る（確定情報とは明記しない）"""
    prompt = kd._search_prompt(kd.STATUS_ENDED, "x", min_yen=5_000_000, max_yen=50_000_000)
    assert "500万円以上5,000万円以下" in prompt
    assert "guidance only" in prompt


def test_search_prompt_warns_not_to_trust_snippet_category_or_amount():
    """検索結果スニペットのカテゴリー・金額を確定情報としないようモデルへ指示する"""
    prompt = kd._search_prompt(kd.STATUS_LIVE, "x", category="Technology", min_yen=1_000_000)
    assert "snippets as confirmed fact" in prompt


# ── モデルの最終回答文を候補抽出に使わない（カテゴリー検索でも同じ規律）────────────

def test_category_search_still_ignores_final_text_for_urls():
    message = {
        "content": [
            {"type": "web_search_tool_result", "content": [
                {"type": "web_search_result",
                 "url": "https://www.kickstarter.com/projects/real/technology-gadget",
                 "title": "Real Gadget"},
            ]},
            {"type": "text",
             "text": "I also recall a Technology product at "
                     "https://www.kickstarter.com/projects/fake/hallucinated-gadget"},
        ],
    }
    urls = kd.extract_search_result_urls(message)
    assert urls == [("https://www.kickstarter.com/projects/real/technology-gadget", "Real Gadget")]


# ── カテゴリーの正規化（親カテゴリー・サブカテゴリー）───────────────────────────

def test_normalize_category_exact_parent_match():
    assert kd.normalize_category("Technology") == "Technology"
    assert kd.normalize_category("technology") == "Technology"


def test_normalize_category_subcategory_maps_to_parent():
    assert kd.normalize_category("Gadgets") == "Technology"
    assert kd.normalize_category("Product Design") == "Design"
    assert kd.normalize_category("Wearables") == "Technology"


def test_normalize_category_handles_breadcrumb_format():
    """「Technology / Gadgets」のような区切り表記にも対応する"""
    assert kd.normalize_category("Technology / Gadgets") == "Technology"
    assert kd.normalize_category("Design > Product Design") == "Design"


def test_normalize_category_unknown_text_returns_none_not_a_guess():
    """未知のカテゴリー文字列は判定不能（Noneを推測で埋めない）"""
    assert kd.normalize_category("Some Made Up Category") is None
    assert kd.normalize_category("") is None
    assert kd.normalize_category(None) is None


# ── 個別ページのカテゴリーで最終判定する（検索スニペットは信用しない）────────────

def test_run_discovery_final_category_judgement_uses_fetched_page_only():
    """検索結果のタイトルに"Technology"と書かれていても、実際のcategory_rawで判定する"""
    url = "https://www.kickstarter.com/projects/foo/not-tech-gadget"
    search_resp = _search_message([(url, "Amazing NEW Technology Gadget!!!")])
    fetch_resp = _fetch_message(url, "2026-10-19T06:00:00+00:00", {
        "name": "Hand Knit Scarf", "image_url": None, "summary_ja": "手編みマフラーです。",
        "category_raw": "Knitting",  # 実際は Crafts のサブカテゴリー
        "maker": "M", "country": "US", "raised_amount": 20000, "currency": "USD",
        "goal_amount": 10000, "backers": 100, "launch_date": "2026-01-01",
        "end_date_iso": "2026-03-01", "days_left_text_raw": None,
        "late_pledge_confirmed": False, "reported_status_text": "funded",
        "canonical_url": url,
    })
    client = _FakeClient([search_resp, fetch_resp])
    result = kd.run_discovery(client, "x", [kd.STATUS_ENDED], category="Technology",
                              now=datetime(2026, 10, 19, 6, 0, 0, tzinfo=timezone.utc))
    assert result.by_status[kd.STATUS_ENDED] == []
    assert len(result.excluded_condition) == 1
    assert result.excluded_condition[0].category_parent == "Crafts"
    assert result.excluded_condition[0].exclusion_reason == "選択カテゴリーと不一致"


def test_category_unknown_is_not_force_matched():
    """個別ページのカテゴリーを確認できない商品は、推測で一致扱いにしない"""
    url = "https://www.kickstarter.com/projects/foo/mystery-gadget"
    search_resp = _search_message([(url, "Mystery Gadget")])
    fetch_resp = _fetch_message(url, "2026-10-19T06:00:00+00:00", {
        "name": "Mystery Gadget", "image_url": None, "summary_ja": "謎の商品です。",
        "category_raw": None,  # カテゴリーがページ上で確認できない
        "maker": "M", "country": "US", "raised_amount": 20000, "currency": "USD",
        "goal_amount": 10000, "backers": 100, "launch_date": "2026-01-01",
        "end_date_iso": "2026-03-01", "days_left_text_raw": None,
        "late_pledge_confirmed": False, "reported_status_text": "funded",
        "canonical_url": url,
    })
    client = _FakeClient([search_resp, fetch_resp])
    result = kd.run_discovery(client, "x", [kd.STATUS_ENDED], category="Technology",
                              now=datetime(2026, 10, 19, 6, 0, 0, tzinfo=timezone.utc))
    assert result.by_status[kd.STATUS_ENDED] == []
    assert len(result.category_unknown) == 1
    assert result.category_unknown[0].exclusion_reason == "カテゴリーを確認できない"


def test_category_all_does_not_filter_by_category():
    """カテゴリー「すべて」なら、カテゴリー不明でも最終結果から除外されない"""
    url = "https://www.kickstarter.com/projects/foo/any-gadget"
    search_resp = _search_message([(url, "Any Gadget")])
    fetch_resp = _fetch_message(url, "2026-10-19T06:00:00+00:00", {
        "name": "Any Gadget", "image_url": None, "summary_ja": "任意の商品です。",
        "category_raw": None, "maker": "M", "country": "US",
        "raised_amount": 20000, "currency": "USD", "goal_amount": 10000, "backers": 100,
        "launch_date": "2026-01-01", "end_date_iso": "2026-03-01",
        "days_left_text_raw": None, "late_pledge_confirmed": False,
        "reported_status_text": "funded", "canonical_url": url,
    })
    client = _FakeClient([search_resp, fetch_resp])
    result = kd.run_discovery(client, "x", [kd.STATUS_ENDED], category=kd.CATEGORY_ALL,
                              now=datetime(2026, 10, 19, 6, 0, 0, tzinfo=timezone.utc))
    assert len(result.by_status[kd.STATUS_ENDED]) == 1
    assert result.category_unknown == []


# ── キーワード必須ルール（カテゴリー次第で変わる）───────────────────────────────

def test_keyword_optional_when_category_specified():
    keyword, error = kd.validate_keyword("", category="Technology")
    assert error == ""
    assert keyword == ""


def test_keyword_required_when_category_is_all():
    keyword, error = kd.validate_keyword("", category=kd.CATEGORY_ALL)
    assert error != ""
    assert keyword == ""


def test_keyword_too_long_rejected_regardless_of_category():
    _, error = kd.validate_keyword("x" * 201, category="Technology")
    assert error != ""


# ── 支援総額の下限・上限判定（境界値を含む）──────────────────────────────────

def test_matches_amount_range_lower_bound_only():
    assert kd.matches_amount_range(5_000_000, 5_000_000, None) is True   # 境界値
    assert kd.matches_amount_range(4_999_999, 5_000_000, None) is False
    assert kd.matches_amount_range(9_999_999_999, 5_000_000, None) is True


def test_matches_amount_range_upper_bound_only():
    assert kd.matches_amount_range(50_000_000, None, 50_000_000) is True  # 境界値
    assert kd.matches_amount_range(50_000_001, None, 50_000_000) is False
    assert kd.matches_amount_range(0, None, 50_000_000) is True


def test_matches_amount_range_both_bounds_inclusive():
    assert kd.matches_amount_range(5_000_000, 5_000_000, 50_000_000) is True
    assert kd.matches_amount_range(50_000_000, 5_000_000, 50_000_000) is True
    assert kd.matches_amount_range(4_999_999, 5_000_000, 50_000_000) is False
    assert kd.matches_amount_range(50_000_001, 5_000_000, 50_000_000) is False


def test_matches_amount_range_none_jpy_never_matches():
    assert kd.matches_amount_range(None, 5_000_000, None) is False
    assert kd.matches_amount_range(None, None, None) is False


# ── 下限が上限を超える場合のエラー ────────────────────────────────────────────

def test_validate_amount_range_rejects_min_above_max():
    error = kd.validate_amount_range(50_000_000, 5_000_000)
    assert error != ""


def test_validate_amount_range_allows_min_equal_max():
    assert kd.validate_amount_range(5_000_000, 5_000_000) == ""


def test_validate_amount_range_allows_either_side_unset():
    assert kd.validate_amount_range(None, 5_000_000) == ""
    assert kd.validate_amount_range(5_000_000, None) == ""
    assert kd.validate_amount_range(None, None) == ""


# ── 複数通貨のECB日本円換算（固定レート不使用）──────────────────────────────────

def test_convert_to_jpy_handles_gbp_in_addition_to_usd():
    rates = {"EUR": 1.0, "USD": 1.08, "GBP": 0.84, "JPY": 160.0}
    jpy, reason = kd.convert_to_jpy(100.0, "GBP", rates)
    assert reason == ""
    assert round(jpy, 2) == round(100.0 / 0.84 * 160.0, 2)


def test_amount_unknown_when_ecb_rate_missing_for_currency_and_condition_set():
    """金額条件があるとき、換算できない商品は一致結果に混ぜず判定不能へ回す"""
    url = "https://www.kickstarter.com/projects/foo/twd-gadget"
    search_resp = _search_message([(url, "TWD Gadget")])
    fetch_resp = _fetch_message(url, "2026-10-19T06:00:00+00:00", {
        "name": "TWD Gadget", "image_url": None, "summary_ja": "台湾ドルの商品です。",
        "category_raw": "Gadgets", "maker": "M", "country": "TW",
        "raised_amount": 1_000_000, "currency": "TWD",  # ECB未対応通貨
        "goal_amount": 500_000, "backers": 100, "launch_date": "2026-01-01",
        "end_date_iso": "2026-03-01", "days_left_text_raw": None,
        "late_pledge_confirmed": False, "reported_status_text": "funded",
        "canonical_url": url,
    })
    client = _FakeClient([search_resp, fetch_resp])

    import requests
    original_get = requests.get
    requests.get = lambda *a, **k: _FakeEcbResp()
    try:
        result = kd.run_discovery(client, "x", [kd.STATUS_ENDED], min_yen=1_000_000,
                                  now=datetime(2026, 10, 19, 6, 0, 0, tzinfo=timezone.utc))
    finally:
        requests.get = original_get

    assert result.by_status[kd.STATUS_ENDED] == []
    assert len(result.amount_unknown) == 1
    assert "対応していない通貨" in result.amount_unknown[0].exclusion_reason


class _FakeEcbResp:
    status_code = 200
    content = (
        b'<?xml version="1.0" encoding="UTF-8"?>'
        b'<gesmes:Envelope xmlns:gesmes="http://www.gesmes.org/xml/2002-08-01" '
        b'xmlns="http://www.ecb.int/vocabulary/2002-08-01/eurofxref">'
        b'<Cube><Cube time="2026-10-19">'
        b'<Cube currency="USD" rate="1.08"/><Cube currency="JPY" rate="160.0"/>'
        b'</Cube></Cube></gesmes:Envelope>'
    )


# ── 条件一致商品は日本円換算額の高い順 ─────────────────────────────────────────

def test_matched_products_sorted_by_jpy_descending():
    url_a = "https://www.kickstarter.com/projects/foo/cheap-gadget"
    url_b = "https://www.kickstarter.com/projects/foo/expensive-gadget"
    search_resp = _search_message([(url_a, "Cheap"), (url_b, "Expensive")])
    fetch_a = _fetch_message(url_a, "2026-10-19T06:00:00+00:00", {
        "name": "Cheap Gadget", "image_url": None, "summary_ja": "x", "category_raw": "Gadgets",
        "maker": "M", "country": "US", "raised_amount": 1000, "currency": "USD",
        "goal_amount": 500, "backers": 10, "launch_date": "2026-01-01",
        "end_date_iso": "2026-03-01", "days_left_text_raw": None,
        "late_pledge_confirmed": False, "reported_status_text": "funded",
        "canonical_url": url_a,
    })
    fetch_b = _fetch_message(url_b, "2026-10-19T06:00:05+00:00", {
        "name": "Expensive Gadget", "image_url": None, "summary_ja": "x",
        "category_raw": "Gadgets", "maker": "M", "country": "US",
        "raised_amount": 900000, "currency": "USD", "goal_amount": 500,
        "backers": 9000, "launch_date": "2026-01-01", "end_date_iso": "2026-03-01",
        "days_left_text_raw": None, "late_pledge_confirmed": False,
        "reported_status_text": "funded", "canonical_url": url_b,
    })
    client = _FakeClient([search_resp, fetch_a, fetch_b])

    import requests
    original_get = requests.get
    requests.get = lambda *a, **k: _FakeEcbResp()
    try:
        result = kd.run_discovery(client, "x", [kd.STATUS_ENDED],
                                  now=datetime(2026, 10, 19, 6, 0, 0, tzinfo=timezone.utc))
    finally:
        requests.get = original_get

    names = [p.name for p in result.by_status[kd.STATUS_ENDED]]
    assert names == ["Expensive Gadget", "Cheap Gadget"], names


# ── 検索上限（カテゴリー・金額条件を使っても変わらない）────────────────────────

def test_search_and_fetch_caps_unchanged_with_category_and_amount_filters():
    many_urls = [(f"https://www.kickstarter.com/projects/foo/g{i}", f"G{i}") for i in range(8)]
    search_resp = _search_message(many_urls)
    fields = {
        "name": "G", "image_url": None, "summary_ja": "x", "category_raw": "Gadgets",
        "maker": "M", "country": "US", "raised_amount": 1_000_000, "currency": "USD",
        "goal_amount": 1, "backers": 1, "launch_date": "2026-01-01",
        "end_date_iso": "2026-11-01", "days_left_text_raw": None,
        "late_pledge_confirmed": False, "reported_status_text": "live",
        "canonical_url": "https://www.kickstarter.com/projects/foo/g0",
    }
    responses = [search_resp] + [
        _fetch_message("https://www.kickstarter.com/projects/foo/g0",
                       "2026-10-19T06:00:00+00:00", fields)
        for _ in range(4)  # STATUS_LIVE のガイドライン上限
    ]
    client = _FakeClient(responses)
    result = kd.run_discovery(client, "x", [kd.STATUS_LIVE], category="Technology",
                              min_yen=1_000_000, max_yen=100_000_000,
                              now=datetime(2026, 10, 19, 6, 0, 0, tzinfo=timezone.utc))
    assert result.usage["search_calls"] == 1
    assert result.usage["fetch_calls"] == 4  # PER_STATUS_GUIDELINE["live"]


# ── Indiegogo（公式Public API） ────────────────────────────────────────────
#
# Anthropicクライアントは一切使わない（requests.get だけを差し替える）。
# 実際のAPIレスポンス構造（2026-11-23に実機で確認済み）に合わせたフィールド
# 名をそのまま使う。

def _igg_item(**overrides):
    base = {
        "backerCount": 2,
        "campaignEndDate": "2026-12-19T23:00:00Z",
        "campaignGoal": 1000.0,
        "campaignStartDate": "2026-11-21T23:00:00Z",
        "commentCount": 0,
        "creatorName": "Blake Farha",
        "creatorUrlName": "blakefarha",
        "currencyShortName": "USD",
        "followerCount": 8,
        "fundsGathered": 190.6,
        "projectHomeUrl": "https://www.indiegogo.com/projects/blakefarha/wearexylophone",
        "projectImageUrl": "https://cdn.images.indiegogo.com/x.png",
        "projectName": "Xylophone: Berlin's Hottest Musical Comedy",
        "projectType": 1,
        "projectUrlName": "wearexylophone",
        "rewardCount": 12,
        "shortDescription": "A camera backpack for outdoor adventures.",
        "updateCount": 0,
    }
    base.update(overrides)
    return base


class _IggResp:
    def __init__(self, status_code=200, payload=None, raise_json_error=False):
        self.status_code = status_code
        self._payload = payload
        self._raise_json_error = raise_json_error

    def json(self):
        if self._raise_json_error:
            raise ValueError("not json")
        return self._payload


_ECB_OK_RESP_BODY = (
    b'<?xml version="1.0" encoding="UTF-8"?>'
    b'<gesmes:Envelope xmlns:gesmes="http://www.gesmes.org/xml/2002-08-01" '
    b'xmlns="http://www.ecb.int/vocabulary/2002-08-01/eurofxref">'
    b'<Cube><Cube time="2026-11-23">'
    b'<Cube currency="USD" rate="1.08"/><Cube currency="JPY" rate="160.0"/>'
    b'</Cube></Cube></gesmes:Envelope>'
)


class _EcbOkResp:
    status_code = 200
    content = _ECB_OK_RESP_BODY


def _patch_requests_get(fake_get):
    import requests
    original = requests.get
    requests.get = fake_get
    return original


def _unpatch_requests_get(original):
    import requests
    requests.get = original


def test_parse_igg_active_item_maps_official_fields():
    """公式APIの実フィールド名（2026-11-23確認）を正しくマッピングする"""
    p = kd._parse_igg_active_item(_igg_item())
    assert p is not None
    assert p.name == "Xylophone: Berlin's Hottest Musical Comedy"
    assert p.url == "https://www.indiegogo.com/projects/blakefarha/wearexylophone"
    assert p.maker == "Blake Farha"
    assert p.raised_amount == 190.6
    assert p.currency == "USD"
    assert p.goal_amount == 1000.0
    assert p.backers == 2
    assert p.start_date_iso == "2026-11-21T23:00:00Z"
    assert p.end_date_iso == "2026-12-19T23:00:00Z"


def test_parse_igg_active_item_without_url_is_rejected():
    """projectHomeUrlが無い項目は採用しない（架空URLを作らない）"""
    assert kd._parse_igg_active_item(_igg_item(projectHomeUrl="")) is None
    assert kd._parse_igg_active_item(_igg_item(projectHomeUrl=None)) is None


def test_parse_igg_active_item_handles_missing_fields_safely():
    """欠損フィールドは安全な既定値（空文字・None）になる（推測で埋めない）"""
    item = {"projectHomeUrl": "https://www.indiegogo.com/projects/x/y"}
    p = kd._parse_igg_active_item(item)
    assert p is not None
    assert p.name == ""
    assert p.maker == ""
    assert p.short_description == ""
    assert p.raised_amount is None
    assert p.currency == ""
    assert p.goal_amount is None
    assert p.backers is None
    assert p.start_date_iso == ""
    assert p.end_date_iso == ""


def test_parse_igg_active_item_rejects_non_dict():
    assert kd._parse_igg_active_item("not a dict") is None
    assert kd._parse_igg_active_item(None) is None


def test_indiegogo_matches_keyword_case_insensitive_partial_match():
    p = kd._parse_igg_active_item(_igg_item(
        projectName="Smart Camera Backpack", shortDescription="For travel."))
    assert kd.indiegogo_matches_keyword(p, "camera") is True
    assert kd.indiegogo_matches_keyword(p, "CAMERA") is True
    assert kd.indiegogo_matches_keyword(p, "Backpack") is True
    assert kd.indiegogo_matches_keyword(p, "travel") is True  # 説明文側も対象
    assert kd.indiegogo_matches_keyword(p, "watch") is False


def test_indiegogo_matches_keyword_empty_keyword_matches_everything():
    p = kd._parse_igg_active_item(_igg_item())
    assert kd.indiegogo_matches_keyword(p, "") is True
    assert kd.indiegogo_matches_keyword(p, "   ") is True
    assert kd.indiegogo_matches_keyword(p, None) is True


def test_fetch_indiegogo_active_projects_success():
    original = _patch_requests_get(
        lambda *a, **k: _IggResp(200, [_igg_item()]))
    try:
        data, error = kd.fetch_indiegogo_active_projects()
    finally:
        _unpatch_requests_get(original)
    assert error == ""
    assert len(data) == 1


def test_fetch_indiegogo_active_projects_handles_connection_error():
    """通信エラーでは架空データで補完せず、エラー文言だけを返す"""
    import requests as _requests
    original = _patch_requests_get(
        lambda *a, **k: (_ for _ in ()).throw(
            _requests.exceptions.ConnectionError("no network in test")))
    try:
        data, error = kd.fetch_indiegogo_active_projects()
    finally:
        _unpatch_requests_get(original)
    assert data == []
    assert error


def test_fetch_indiegogo_active_projects_handles_timeout():
    import requests as _requests
    original = _patch_requests_get(
        lambda *a, **k: (_ for _ in ()).throw(_requests.exceptions.Timeout("timeout")))
    try:
        data, error = kd.fetch_indiegogo_active_projects()
    finally:
        _unpatch_requests_get(original)
    assert data == []
    assert error


def test_fetch_indiegogo_active_projects_handles_non_200_status():
    original = _patch_requests_get(lambda *a, **k: _IggResp(503, None))
    try:
        data, error = kd.fetch_indiegogo_active_projects()
    finally:
        _unpatch_requests_get(original)
    assert data == []
    assert "503" in error


def test_fetch_indiegogo_active_projects_handles_malformed_json():
    original = _patch_requests_get(
        lambda *a, **k: _IggResp(200, None, raise_json_error=True))
    try:
        data, error = kd.fetch_indiegogo_active_projects()
    finally:
        _unpatch_requests_get(original)
    assert data == []
    assert error


def test_fetch_indiegogo_active_projects_handles_unexpected_response_shape():
    """配列以外（例: {"error":...}）が返った場合も安全に扱う"""
    original = _patch_requests_get(lambda *a, **k: _IggResp(200, {"unexpected": "shape"}))
    try:
        data, error = kd.fetch_indiegogo_active_projects()
    finally:
        _unpatch_requests_get(original)
    assert data == []
    assert error


def test_search_indiegogo_active_projects_filters_and_converts_jpy():
    items = [
        _igg_item(projectName="Camera Backpack", shortDescription="For travel.",
                  projectHomeUrl="https://www.indiegogo.com/a"),
        _igg_item(projectName="Smart Watch", shortDescription="Tracks your sleep.",
                  projectHomeUrl="https://www.indiegogo.com/b"),
    ]
    call_log = []

    def _fake_get(url, timeout=None):
        call_log.append(url)
        if "ecb.europa.eu" in url:
            return _EcbOkResp()
        return _IggResp(200, items)

    original = _patch_requests_get(_fake_get)
    try:
        result = kd.search_indiegogo_active_projects("camera")
    finally:
        _unpatch_requests_get(original)

    assert result.error == ""
    assert result.total_active_count == 2
    assert result.matched_count == 1
    assert len(result.products) == 1
    assert result.products[0].name == "Camera Backpack"
    assert result.products[0].raised_amount_jpy is not None
    assert result.jpy_rate_date == "2026-11-23"


def test_search_indiegogo_active_projects_empty_keyword_returns_all():
    items = [
        _igg_item(projectHomeUrl="https://www.indiegogo.com/a"),
        _igg_item(projectHomeUrl="https://www.indiegogo.com/b"),
    ]

    def _fake_get(url, timeout=None):
        if "ecb.europa.eu" in url:
            return _EcbOkResp()
        return _IggResp(200, items)

    original = _patch_requests_get(_fake_get)
    try:
        result = kd.search_indiegogo_active_projects("")
    finally:
        _unpatch_requests_get(original)

    assert result.matched_count == 2


def test_search_indiegogo_active_projects_zero_matches_is_not_an_error():
    items = [_igg_item(projectName="Camera Backpack")]

    def _fake_get(url, timeout=None):
        if "ecb.europa.eu" in url:
            return _EcbOkResp()
        return _IggResp(200, items)

    original = _patch_requests_get(_fake_get)
    try:
        result = kd.search_indiegogo_active_projects("submarine")
    finally:
        _unpatch_requests_get(original)

    assert result.error == ""
    assert result.matched_count == 0
    assert result.products == []
    assert result.total_active_count == 1


def test_search_indiegogo_active_projects_dedupes_same_url():
    items = [
        _igg_item(projectHomeUrl="https://www.indiegogo.com/same"),
        _igg_item(projectHomeUrl="https://www.indiegogo.com/same"),
    ]

    def _fake_get(url, timeout=None):
        if "ecb.europa.eu" in url:
            return _EcbOkResp()
        return _IggResp(200, items)

    original = _patch_requests_get(_fake_get)
    try:
        result = kd.search_indiegogo_active_projects("")
    finally:
        _unpatch_requests_get(original)

    assert result.total_active_count == 1
    assert result.matched_count == 1


def test_search_indiegogo_active_projects_propagates_fetch_error_without_fake_data():
    import requests as _requests
    original = _patch_requests_get(
        lambda *a, **k: (_ for _ in ()).throw(
            _requests.exceptions.ConnectionError("no network in test")))
    try:
        result = kd.search_indiegogo_active_projects("camera")
    finally:
        _unpatch_requests_get(original)

    assert result.error
    assert result.products == []
    assert result.total_active_count == 0
    assert result.matched_count == 0


def test_search_indiegogo_active_projects_handles_multiple_currencies_and_missing_amount():
    items = [
        _igg_item(projectHomeUrl="https://www.indiegogo.com/usd", currencyShortName="USD",
                  fundsGathered=100.0),
        _igg_item(projectHomeUrl="https://www.indiegogo.com/eur", currencyShortName="EUR",
                  fundsGathered=100.0),
        _igg_item(projectHomeUrl="https://www.indiegogo.com/none", fundsGathered=None),
    ]

    def _fake_get(url, timeout=None):
        if "ecb.europa.eu" in url:
            return _EcbOkResp()
        return _IggResp(200, items)

    original = _patch_requests_get(_fake_get)
    try:
        result = kd.search_indiegogo_active_projects("")
    finally:
        _unpatch_requests_get(original)

    by_url = {p.url: p for p in result.products}
    assert by_url["https://www.indiegogo.com/usd"].raised_amount_jpy is not None
    assert by_url["https://www.indiegogo.com/eur"].raised_amount_jpy is not None
    none_p = by_url["https://www.indiegogo.com/none"]
    assert none_p.raised_amount_jpy is None
    assert none_p.jpy_unavailable_reason  # 「金額未取得」等の理由が残る


if __name__ == "__main__":
    sys.exit(run(dict(globals()), "kickstarter_discovery 単体テスト（外部通信なし）"))
