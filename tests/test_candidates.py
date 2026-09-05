# -*- coding: utf-8 -*-
"""候補保存（フェーズ3B）の決定論テスト

実行: python tests/test_candidates.py

Supabase への通信は偽クライアントで捕捉する。外部AI APIもネットワークも
使わない。トークンの実値はテスト出力にも出さない。
"""

import math
import sys
from datetime import date, datetime

from _harness import run  # noqa: E402
import candidates  # noqa: E402
import candidates_ui  # noqa: E402
import product_key as pk  # noqa: E402
import research_crowdfunding as r  # noqa: E402
import result_schema as rschema  # noqa: E402

_KS_URL = "https://www.kickstarter.com/projects/x/sonarpen-2"
_KS_KEY = "https://kickstarter.com/projects/x/sonarpen-2"


def _row(name="SonarPen 2", url=_KS_URL, **overrides) -> dict:
    row = {col: "" for col in r.CSV_FIELDS}
    row.update({
        "商品名": name, "メーカー名": "Greenbulb", "掲載URL": url,
        "プラットフォーム": "Kickstarter", "調達額(円)": 1000000, "調達額(USD)": 6666,
        "支援者数": 500, "商品ジャンル": "ガジェット", "商品の特徴": "説明文",
        "日本で売れそうな理由": "理由", "日本販売時の訴求ポイント": "訴求",
        "競合する日本商品": "競合", "公式サイトURL": "https://example.com",
        "メールアドレス": "a@example.com", "問い合わせフォームURL": "未確認",
        "Facebook": "未確認", "Instagram": "未確認", "LinkedIn": "未確認",
        "優先度": "A", "判定の確度": "データ取得済み", "優先度の理由": "優先理由",
        "注意点・懸念点": "懸念", "営業メール件名(英語)": "Subject",
        "営業メール本文(英語)": "Body",
    })
    row.update(overrides)
    return row


# ── 偽クライアント（通信しない）────────────────────────────────────────────────

class _FakeResponse:
    def __init__(self, data):
        self.data = data


class _FakeRpcBuilder:
    def __init__(self, calls, fn, params, result=None, fail=False):
        self._calls = calls
        self._fn = fn
        self._params = params
        self._result = result
        self._fail = fail

    def execute(self):
        self._calls.append({"fn": self._fn, "params": self._params})
        if self._fail:
            raise RuntimeError("connection refused: db.example.internal:5432")
        return _FakeResponse(self._result)


class _FakeClient:
    """save_candidate RPC だけを持つ偽クライアント。他のメソッドは公開しない
    （products/saved_itemsへ直接書き込む経路がそもそも無いことを模す）"""

    def __init__(self, already_saved=False, fail=False, result=None):
        self.calls = []
        self._already_saved = already_saved
        self._fail = fail
        self._result = result

    def rpc(self, fn, params):
        result = self._result
        if result is None:
            result = {"saved_item_id": "sid-1", "product_id": "pid-1",
                     "already_saved": self._already_saved}
        return _FakeRpcBuilder(self.calls, fn, params, result=result, fail=self._fail)


# ── URL正規化と対象ドメイン ────────────────────────────────────────────────────

def test_saves_kickstarter_indiegogo_zeczec():
    """Kickstarter・Indiegogo・ZECZECの3ドメインを保存できる"""
    for url in ("https://www.kickstarter.com/projects/a/b",
                "https://www.indiegogo.com/projects/a-b",
                "https://www.zeczec.com/projects/a-b"):
        client = _FakeClient()
        result = candidates.save_one(client, _row(url=url))
        assert result.ok, (url, result.message)
        assert client.calls, url


def test_rejects_spoofed_domain():
    """偽装ドメイン（zeczec.com.evil.com 等）は保存前に拒否し、RPCを呼ばない"""
    for url in ("https://zeczec.com.evil.com/projects/a",
                "https://evil-kickstarter.com/projects/a",
                "https://amazon.co.jp/dp/x"):
        client = _FakeClient()
        result = candidates.save_one(client, _row(url=url))
        assert result.ok is False, url
        assert not client.calls, f"{url}: 拒否すべきなのにRPCが呼ばれた"
        assert result.message == candidates.INVALID_URL


def test_url_key_uses_product_key_normalization():
    """p_url_key は product_key.py の正規化結果と一致する"""
    client = _FakeClient()
    candidates.save_one(client, _row(url=_KS_URL + "?ref=abc#pledge"))
    params = client.calls[0]["params"]
    assert params["p_url_key"] == pk.normalize_url(_KS_URL)
    assert params["p_url_key"] == _KS_KEY


def test_source_url_is_the_original_url():
    """p_source_url には正規化前の元のURLを渡す"""
    original = _KS_URL + "?ref=abc"
    client = _FakeClient()
    candidates.save_one(client, _row(url=original))
    assert client.calls[0]["params"]["p_source_url"] == original


# ── RPCの使い方 ────────────────────────────────────────────────────────────────

def test_uses_save_candidate_rpc_only():
    """save_candidate という名前のRPCだけを使う"""
    client = _FakeClient()
    candidates.save_one(client, _row())
    assert client.calls[0]["fn"] == "save_candidate"
    assert len(client.calls) == 1


def test_rpc_params_are_exactly_three_keys():
    """RPCへ渡す引数は p_url_key / p_source_url / p_product の3つだけ"""
    client = _FakeClient()
    candidates.save_one(client, _row())
    assert set(client.calls[0]["params"]) == {"p_url_key", "p_source_url", "p_product"}


def test_does_not_send_user_id():
    """RPCへ user_id を一切渡さない（保存者はDB側のauth.uid()で決まる）"""
    client = _FakeClient()
    candidates.save_one(client, _row())
    params = client.calls[0]["params"]
    assert "user_id" not in params
    assert "user_id" not in params["p_product"]
    flat = str(params)
    assert "user_id" not in flat


def test_client_has_no_direct_table_write_methods_used():
    """偽クライアントは rpc しか持たない。candidates.py がそれ以外を呼んでいない

    products/saved_items へ直接 insert/upsert/update する経路が
    無いことを、クライアント側に該当メソッドを一切実装しないことで確認する。
    """
    client = _FakeClient()
    assert not hasattr(client, "table")
    assert not hasattr(client, "from_")
    result = candidates.save_one(client, _row())
    assert result.ok is True


def test_save_candidate_source_does_not_call_table():
    """candidates.py のソースに直接書き込みの呼び出しが無い"""
    import pathlib
    text = pathlib.Path(candidates.__file__).read_text(encoding="utf-8")
    for forbidden in (".table(", ".from_(", ".insert(", ".upsert(", ".update("):
        assert forbidden not in text, f"{forbidden} が candidates.py にある"


# ── 保存結果の判定 ─────────────────────────────────────────────────────────────

def test_new_save_reports_success():
    """新規保存では ok=True・already_saved=False になる"""
    client = _FakeClient(already_saved=False)
    result = candidates.save_one(client, _row())
    assert result.ok is True and result.already_saved is False


def test_already_saved_is_reported():
    """already_saved=true を保存済みとして扱う（事前SELECTでは調べない）"""
    client = _FakeClient(already_saved=True)
    result = candidates.save_one(client, _row())
    assert result.ok is True and result.already_saved is True


def test_rpc_exception_is_reported_as_failure_without_leaking():
    """RPCが例外を投げても、例外の中身を含めずに安全な文言で失敗を返す"""
    client = _FakeClient(fail=True)
    result = candidates.save_one(client, _row())
    assert result.ok is False
    assert result.message == candidates.SAVE_FAILED
    assert "connection refused" not in result.message
    assert "db.example.internal" not in result.message


def test_missing_saved_item_id_is_treated_as_failure():
    """戻り値が想定外の形（saved_item_id無し）でも安全に失敗扱いにする"""
    client = _FakeClient(result={"unexpected": "shape"})
    result = candidates.save_one(client, _row())
    assert result.ok is False
    assert result.message == candidates.SAVE_FAILED


def test_none_response_data_is_treated_as_failure():
    client = _FakeClient(result={})
    result = candidates.save_one(client, _row())
    assert result.ok is False


# ── 複数件の集計 ───────────────────────────────────────────────────────────────

def test_summarize_counts_saved_already_and_failed():
    good = _FakeClient(already_saved=False)
    dup = _FakeClient(already_saved=True)
    bad = _FakeClient(fail=True)
    results = [
        candidates.save_one(good, _row("A", url="https://www.kickstarter.com/projects/a/a")),
        candidates.save_one(dup, _row("B", url="https://www.kickstarter.com/projects/a/b")),
        candidates.save_one(dup, _row("C", url="https://www.kickstarter.com/projects/a/c")),
        candidates.save_one(bad, _row("D", url="https://www.kickstarter.com/projects/a/d")),
    ]
    saved, already, failed = candidates.summarize(results)
    assert (saved, already, failed) == (1, 2, 1)


def test_summary_message_single_success():
    client = _FakeClient(already_saved=False)
    result = candidates.save_one(client, _row())
    assert candidates.summary_message([result]) == candidates.SAVED_ONE


def test_summary_message_single_already_saved():
    client = _FakeClient(already_saved=True)
    result = candidates.save_one(client, _row())
    assert candidates.summary_message([result]) == candidates.ALREADY_ONE


def test_summary_message_single_failure_has_no_exception_text():
    client = _FakeClient(fail=True)
    result = candidates.save_one(client, _row())
    message = candidates.summary_message([result])
    assert message == candidates.SAVE_FAILED
    assert "RuntimeError" not in message


def test_summary_message_multiple_reports_counts():
    good = _FakeClient(already_saved=False)
    dup = _FakeClient(already_saved=True)
    bad = _FakeClient(fail=True)
    results = [
        candidates.save_one(good, _row("A", url="https://www.kickstarter.com/projects/a/a")),
        candidates.save_one(dup, _row("B", url="https://www.kickstarter.com/projects/a/b")),
        candidates.save_one(bad, _row("C", url="https://www.kickstarter.com/projects/a/c")),
    ]
    message = candidates.summary_message(results)
    assert "新規保存 1件" in message
    assert "保存済み 1件" in message
    assert "失敗 1件" in message


def test_save_many_saves_each_row_once():
    client = _FakeClient()
    rows = [_row("A", url="https://www.kickstarter.com/projects/a/a"),
            _row("B", url="https://www.kickstarter.com/projects/a/b")]
    results = candidates.save_many(client, rows)
    assert len(results) == 2
    assert len(client.calls) == 2
    assert {r.name for r in results} == {"A", "B"}


# ── JSON化できない値の安全な正規化 ────────────────────────────────────────────

def test_payload_handles_nan_and_infinity():
    row = _row()
    row["調達額(円)"] = float("nan")
    row["調達額(USD)"] = float("inf")
    row["支援者数"] = float("-inf")
    payload = candidates.build_product_payload(row)
    assert payload["raised_jpy"] == 0
    assert payload["raised_usd"] == 0.0
    assert payload["backers"] == 0
    import json
    json.dumps(payload)  # 例外にならないこと（NaN/Infinityが残っていない）


def test_payload_handles_date_and_datetime_values():
    row = _row()
    row["商品の特徴"] = date(2026, 1, 2)
    row["日本で売れそうな理由"] = datetime(2026, 1, 2, 3, 4)
    payload = candidates.build_product_payload(row)
    assert payload["analysis"]["description"] == "2026-01-02"
    assert payload["analysis"]["japanese_market_reason"] == "2026-01-02T03:04:00"
    import json
    json.dumps(payload)


def test_payload_handles_none_values():
    row = _row()
    row["メールアドレス"] = None
    row["調達額(円)"] = None
    payload = candidates.build_product_payload(row)
    assert payload["contact"]["email"] is None
    assert payload["raised_jpy"] == 0


def test_payload_handles_unserializable_object():
    """dict/list/文字列/数値以外の型が混ざっても例外にせず文字列化する"""
    class _Weird:
        def __str__(self):
            return "weird-value"

    row = _row()
    row["商品の特徴"] = _Weird()
    payload = candidates.build_product_payload(row)
    assert payload["analysis"]["description"] == "weird-value"
    import json
    json.dumps(payload)


def test_payload_keeps_schema_version():
    payload = candidates.build_product_payload(_row())
    assert payload["schema_version"] == rschema.SCHEMA_VERSION


def test_payload_is_always_json_serializable_via_save_one():
    """save_one に渡すペイロード全体がJSON化できる（送信直前の状態を確認）"""
    import json
    client = _FakeClient()
    row = _row()
    row["調達額(円)"] = float("nan")
    row["商品の特徴"] = date(2026, 1, 1)
    candidates.save_one(client, row)
    json.dumps(client.calls[0]["params"])  # 例外にならないこと


# ── 秘密情報・トークンの非混入 ─────────────────────────────────────────────────

def test_no_token_or_secret_in_messages():
    for message in (candidates.SAVED_ONE, candidates.ALREADY_ONE,
                    candidates.SAVE_FAILED, candidates.INVALID_URL,
                    candidates.NOTHING_SELECTED):
        assert "token" not in message.lower()
        assert "jwt" not in message.lower()


def test_candidates_module_has_no_forbidden_keys():
    import pathlib
    text = pathlib.Path(candidates.__file__).read_text(encoding="utf-8").upper()
    for word in ("SUPABASE_ANON_KEY", "SERVICE_ROLE", "SECRET_KEY"):
        assert word not in text


# ── candidates_ui の session_state（cand_ プレフィックス）───────────────────────

def test_all_cand_state_keys_start_with_prefix():
    assert candidates_ui.SAVED_URLS.startswith("cand_")
    assert candidates_ui.LAST_MESSAGE.startswith("cand_")


def test_clear_state_removes_fixed_and_dynamic_cand_keys():
    """固定キーだけでなく、動的なチェックボックスのウィジェットキーも消える"""
    state = {}
    candidates_ui.init_state(state)
    state[candidates_ui.SAVED_URLS] = {_KS_URL}
    state[candidates_ui.LAST_MESSAGE] = "何か"
    state["cand_check_0_https://example.com"] = True  # 動的ウィジェットキーを模す
    state["search_results"] = ["残ってよい"]
    state["auth_user_id"] = "残ってよい"

    candidates_ui.clear_state(state)

    assert candidates_ui.SAVED_URLS not in state
    assert candidates_ui.LAST_MESSAGE not in state
    assert "cand_check_0_https://example.com" not in state
    assert not any(str(k).startswith("cand_") for k in state)
    assert state["search_results"] == ["残ってよい"]
    assert state["auth_user_id"] == "残ってよい"


def test_clear_state_does_not_touch_supabase():
    """clear_state はローカルの表示状態を消すだけ。DB操作を一切行わない

    （関数のシグネチャが state のみを受け取ることで、DBへ触れないことを保証する）
    """
    import inspect
    params = list(inspect.signature(candidates_ui.clear_state).parameters)
    assert params == ["state"]


def test_init_state_does_not_overwrite_existing():
    state = {candidates_ui.SAVED_URLS: {_KS_URL}}
    candidates_ui.init_state(state)
    assert state[candidates_ui.SAVED_URLS] == {_KS_URL}


if __name__ == "__main__":
    sys.exit(run(dict(globals()), "候補保存テスト（偽クライアント・通信なし）"))
