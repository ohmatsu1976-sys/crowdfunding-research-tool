# -*- coding: utf-8 -*-
"""候補保存（フェーズ3B）の決定論テスト

実行: python tests/test_candidates.py

Supabase への通信は偽クライアントで捕捉する。外部AI APIもネットワークも
使わない。トークンの実値はテスト出力にも出さない。
"""

import math
import re
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


def test_candidates_source_never_inserts_or_upserts():
    """候補の新規作成はRPC経由だけ。insert/upsertはソースのどこにも無い

    saved_items の一覧・更新・削除（フェーズ3C）は .table() を使うが、
    行の作成だけは save_candidate() のRPCに限定し続ける。
    """
    import pathlib
    text = pathlib.Path(candidates.__file__).read_text(encoding="utf-8")
    for forbidden in (".insert(", ".upsert("):
        assert forbidden not in text, f"{forbidden} が candidates.py にある"


def test_candidates_source_never_uses_select_star():
    """products / saved_items に select("*") を使っていない

    説明コメントの中の文字列は対象外にする（コード自体に無いことを見る）。
    """
    import pathlib
    text = pathlib.Path(candidates.__file__).read_text(encoding="utf-8")
    lines = [re.sub(r"#.*$", "", line) for line in text.splitlines()]
    code_only = "\n".join(lines)
    assert 'select("*")' not in code_only
    assert "select('*')" not in code_only


def test_candidates_source_never_searches_products_by_pattern():
    """products をLIKE/ILIKE/text_searchで探索していない"""
    import pathlib
    text = pathlib.Path(candidates.__file__).read_text(encoding="utf-8")
    for forbidden in (".ilike(", ".like(", ".text_search(", ".or_("):
        assert forbidden not in text, f"{forbidden} が candidates.py にある"


def test_candidates_source_only_tables_saved_items_directly():
    """.table() で直接触れるのは saved_items だけ（products は埋め込みでしか見ない）"""
    import pathlib
    import re
    text = pathlib.Path(candidates.__file__).read_text(encoding="utf-8")
    tables = re.findall(r'\.table\(\s*["\']([^"\']+)["\']', text)
    assert tables, "table() 呼び出しが見つからない"
    assert set(tables) == {"saved_items"}, set(tables)


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


# =============================================================================
# マイ候補リスト（フェーズ3C）
# =============================================================================

# ── DB制約との一致 ─────────────────────────────────────────────────────────────

def _read_check_values(constraint_name: str) -> set:
    """0001_tables.sql の CHECK 制約から許可値の集合を取り出す（簡易パーサ）"""
    import pathlib
    import re
    path = (pathlib.Path(__file__).resolve().parent.parent
            / "sql" / "migrations" / "0001_tables.sql")
    text = path.read_text(encoding="utf-8")
    m = re.search(constraint_name + r"\s+check\s*\(([^;]*?)\)\s*(?:,|\))\s*\n", text, re.S)
    assert m, f"{constraint_name} が見つからない"
    return {v.strip().strip("'") for v in re.findall(r"'([^']*)'", m.group(1))}


def test_status_options_match_db_check_constraint():
    """ステータス8値がDBのCHECK制約と完全一致する"""
    db_values = _read_check_values("saved_items_status_ok")
    assert db_values == set(candidates.STATUS_OPTIONS), (
        f"DB={db_values} / candidates.py={set(candidates.STATUS_OPTIONS)}"
    )
    assert len(candidates.STATUS_OPTIONS) == 8


def test_priority_options_match_db_check_constraint():
    """優先度の選択肢がDBのCHECK制約と完全一致する"""
    db_values = _read_check_values("saved_items_priority_ok")
    assert db_values == set(candidates.PRIORITY_OPTIONS), (
        f"DB={db_values} / candidates.py={set(candidates.PRIORITY_OPTIONS)}"
    )


# ── 一覧・更新・削除用の偽クライアント（インメモリDBを模す）───────────────────

class _FakeQuery:
    """select/update/delete 共通のチェーン可能なクエリビルダを模す"""

    def __init__(self, store, kind, table, payload=None):
        self._store = store
        self._kind = kind
        self._table = table
        self._payload = payload
        self._filters = {}
        self._order = None
        self._select_cols = None

    def select(self, columns):
        self._select_cols = columns
        return self

    def eq(self, column, value):
        self._filters[column] = value
        return self

    def order(self, column, desc=False):
        self._order = (column, desc)
        return self

    def _matches(self, row):
        return all(row.get(k) == v for k, v in self._filters.items())

    def execute(self):
        self._store.calls.append({
            "kind": self._kind, "table": self._table,
            "filters": dict(self._filters), "select": self._select_cols,
            "payload": self._payload,
        })
        if self._store.fail:
            raise RuntimeError("connection refused: db.example.internal:5432")

        rows = self._store.tables[self._table]
        matched = [r for r in rows if self._matches(r)]

        if self._kind == "select":
            if self._order:
                col, desc = self._order
                matched = sorted(matched, key=lambda r: r.get(col, ""), reverse=desc)
            out = []
            for row in matched:
                item = dict(row)
                product = self._store.tables["products_by_id"].get(row.get("product_id"), {})
                item["products"] = {
                    "id": product.get("id", ""),
                    "source_url": product.get("source_url", ""),
                    "platform": product.get("platform", ""),
                    "name": product.get("name", ""),
                    "maker": product.get("maker", ""),
                    "priority": product.get("priority", ""),
                }
                out.append(item)
            return _FakeExecResult(out)

        if self._kind == "update":
            assert self._filters.get("id"), "更新対象のidが指定されていない"
            assert self._filters.get("user_id"), "更新対象のuser_idが指定されていない"
            for forbidden in ("user_id", "product_id", "saved_at", "updated_at"):
                assert forbidden not in (self._payload or {}), (
                    f"{forbidden} を更新しようとしている"
                )
            for row in matched:
                row.update(self._payload or {})
            return _FakeExecResult([dict(r) for r in matched])

        if self._kind == "delete":
            assert self._filters.get("id"), "削除対象のidが指定されていない"
            assert self._filters.get("user_id"), "削除対象のuser_idが指定されていない"
            remaining = [r for r in rows if not self._matches(r)]
            self._store.tables[self._table] = remaining
            return _FakeExecResult([dict(r) for r in matched])

        raise AssertionError(f"未対応の操作: {self._kind}")


class _FakeExecResult:
    def __init__(self, data):
        self.data = data


class _FakeTable:
    def __init__(self, store, name):
        self._store = store
        self._name = name

    def select(self, columns):
        return _FakeQuery(self._store, "select", self._name).select(columns)

    def update(self, payload):
        return _FakeQuery(self._store, "update", self._name, payload)

    def delete(self):
        return _FakeQuery(self._store, "delete", self._name)


class _FakeListClient:
    """saved_items の一覧・更新・削除だけを持つ偽クライアント（RLSは模さない。
    その代わり呼び出し側が必ず user_id を渡していることをここで検証する）"""

    def __init__(self, saved_items, products, fail=False):
        self.tables = {
            "saved_items": [dict(r) for r in saved_items],
            "products_by_id": {p["id"]: p for p in products},
        }
        self.calls = []
        self.fail = fail

    def table(self, name):
        return _FakeTable(self, name)


def _make_products():
    return [
        {"id": "pid-a", "source_url": "https://www.kickstarter.com/projects/x/a",
         "platform": "Kickstarter", "name": "商品A", "maker": "メーカーA", "priority": "A"},
        {"id": "pid-b", "source_url": "https://www.kickstarter.com/projects/x/b",
         "platform": "Kickstarter", "name": "商品B", "maker": "メーカーB", "priority": "B"},
    ]


def _make_saved_items():
    return [
        {"id": "sid-a-1", "user_id": "user-1", "product_id": "pid-a",
         "memo": "メモA", "status": "候補", "priority_override": None,
         "archived": False, "saved_at": "2026-01-02T00:00:00", "updated_at": "2026-01-02T00:00:00"},
        {"id": "sid-a-2", "user_id": "user-1", "product_id": "pid-b",
         "memo": "メモB", "status": "交渉中", "priority_override": "A",
         "archived": True, "saved_at": "2026-01-01T00:00:00", "updated_at": "2026-01-01T00:00:00"},
        {"id": "sid-b-1", "user_id": "user-2", "product_id": "pid-a",
         "memo": "他人のメモ", "status": "契約済み", "priority_override": None,
         "archived": False, "saved_at": "2026-01-03T00:00:00", "updated_at": "2026-01-03T00:00:00"},
    ]


# ── 一覧取得 ─────────────────────────────────────────────────────────────────

def test_list_returns_only_own_items():
    """他人の候補（saved_items）が一覧に出ない"""
    client = _FakeListClient(_make_saved_items(), _make_products())
    items = candidates.list_saved_items(client, "user-1")
    assert {i.saved_item_id for i in items} == {"sid-a-1"}  # archived=False の自分の分だけ
    for i in items:
        assert "他人" not in i.memo


def test_list_excludes_archived_by_default():
    """通常はアーカイブ済みを表示しない"""
    client = _FakeListClient(_make_saved_items(), _make_products())
    items = candidates.list_saved_items(client, "user-1", include_archived=False)
    assert all(not i.archived for i in items)
    assert "sid-a-2" not in {i.saved_item_id for i in items}


def test_list_includes_archived_when_requested():
    """「アーカイブ済みも表示」で確認できる"""
    client = _FakeListClient(_make_saved_items(), _make_products())
    items = candidates.list_saved_items(client, "user-1", include_archived=True)
    assert {i.saved_item_id for i in items} == {"sid-a-1", "sid-a-2"}


def test_list_shows_required_fields():
    """商品名・URL・メーカー・優先度・ステータス・活動メモ・保存日時を表示できる"""
    client = _FakeListClient(_make_saved_items(), _make_products())
    items = candidates.list_saved_items(client, "user-1", include_archived=True)
    by_id = {i.saved_item_id: i for i in items}
    a = by_id["sid-a-1"]
    assert a.name == "商品A"
    assert a.source_url == "https://www.kickstarter.com/projects/x/a"
    assert a.platform == "Kickstarter"
    assert a.maker == "メーカーA"
    assert a.priority == "A"                 # 元の判定優先度
    assert a.priority_override is None        # 本人が設定した優先度（未設定）
    assert a.status == "候補"
    assert a.memo == "メモA"
    assert a.saved_at == "2026-01-02T00:00:00"

    b = by_id["sid-a-2"]
    assert b.priority_override == "A"         # 本人が上書きした優先度


def test_list_does_not_use_select_star_in_call():
    """一覧取得で select("*") を使わない（実際の呼び出し引数を検査）"""
    client = _FakeListClient(_make_saved_items(), _make_products())
    candidates.list_saved_items(client, "user-1")
    select_calls = [c for c in client.calls if c["kind"] == "select"]
    assert select_calls
    for call in select_calls:
        assert call["select"] != "*"
        assert "*" not in call["select"]


def test_list_filters_by_user_id_explicitly():
    """呼び出し側が明示的に user_id で絞り込む（RLS任せにしない）"""
    client = _FakeListClient(_make_saved_items(), _make_products())
    candidates.list_saved_items(client, "user-1")
    assert client.calls[-1]["filters"].get("user_id") == "user-1"


def test_list_returns_empty_on_missing_user_id():
    client = _FakeListClient(_make_saved_items(), _make_products())
    assert candidates.list_saved_items(client, "") == []
    assert candidates.list_saved_items(client, None) == []


def test_list_returns_empty_on_failure_without_raising():
    client = _FakeListClient(_make_saved_items(), _make_products(), fail=True)
    assert candidates.list_saved_items(client, "user-1") == []


# ── 更新 ─────────────────────────────────────────────────────────────────────

def test_update_memo_status_priority():
    """活動メモ・ステータス・優先度を更新できる"""
    client = _FakeListClient(_make_saved_items(), _make_products())
    ok = candidates.update_saved_item(client, "user-1", "sid-a-1",
                                      memo="新しいメモ", status="連絡済み",
                                      priority_override="B")
    assert ok is True
    row = client.tables["saved_items"][0]
    assert row["memo"] == "新しいメモ"
    assert row["status"] == "連絡済み"
    assert row["priority_override"] == "B"


def test_update_can_clear_priority_override():
    """優先度の上書きを「未設定」に戻せる（Noneを送る）"""
    client = _FakeListClient(_make_saved_items(), _make_products())
    ok = candidates.update_saved_item(client, "user-1", "sid-a-2", priority_override=None)
    assert ok is True
    row = next(r for r in client.tables["saved_items"] if r["id"] == "sid-a-2")
    assert row["priority_override"] is None


def test_update_rejects_invalid_status():
    client = _FakeListClient(_make_saved_items(), _make_products())
    ok = candidates.update_saved_item(client, "user-1", "sid-a-1", status="でたらめ")
    assert ok is False
    update_calls = [c for c in client.calls if c["kind"] == "update"]
    assert not update_calls, "不正な値なのにDBへ送信している"


def test_update_rejects_invalid_priority():
    client = _FakeListClient(_make_saved_items(), _make_products())
    ok = candidates.update_saved_item(client, "user-1", "sid-a-1", priority_override="Z")
    assert ok is False


def test_update_never_sends_user_id_or_product_id():
    """user_id・product_id・saved_at・updated_at を更新しない"""
    client = _FakeListClient(_make_saved_items(), _make_products())
    candidates.update_saved_item(client, "user-1", "sid-a-1",
                                 memo="x", status="候補", archived=True)
    payload = client.calls[-1]["payload"]
    for forbidden in ("user_id", "product_id", "saved_at", "updated_at"):
        assert forbidden not in payload


def test_update_targets_id_and_user_id_explicitly():
    """更新対象を id と user_id の両方で明示する"""
    client = _FakeListClient(_make_saved_items(), _make_products())
    candidates.update_saved_item(client, "user-1", "sid-a-1", memo="x")
    filters = client.calls[-1]["filters"]
    assert filters == {"id": "sid-a-1", "user_id": "user-1"}


def test_update_cannot_modify_other_users_item():
    """他人のsaved_itemsのidを指定しても、自分のuser_idでは更新対象が無い"""
    client = _FakeListClient(_make_saved_items(), _make_products())
    candidates.update_saved_item(client, "user-1", "sid-b-1", memo="乗っ取り")
    row = next(r for r in client.tables["saved_items"] if r["id"] == "sid-b-1")
    assert row["memo"] == "他人のメモ", "他人の行が書き換えられている"


def test_update_failure_does_not_raise():
    client = _FakeListClient(_make_saved_items(), _make_products(), fail=True)
    ok = candidates.update_saved_item(client, "user-1", "sid-a-1", memo="x")
    assert ok is False


# ── アーカイブ ────────────────────────────────────────────────────────────────

def test_archive_and_unarchive():
    client = _FakeListClient(_make_saved_items(), _make_products())
    assert candidates.update_saved_item(client, "user-1", "sid-a-1", archived=True) is True
    row = next(r for r in client.tables["saved_items"] if r["id"] == "sid-a-1")
    assert row["archived"] is True

    assert candidates.update_saved_item(client, "user-1", "sid-a-1", archived=False) is True
    row = next(r for r in client.tables["saved_items"] if r["id"] == "sid-a-1")
    assert row["archived"] is False


# ── 削除 ─────────────────────────────────────────────────────────────────────

def test_delete_removes_only_saved_item_not_product():
    """saved_itemsだけを削除し、productsの共有商品行は削除しない"""
    client = _FakeListClient(_make_saved_items(), _make_products())
    ok = candidates.delete_saved_item(client, "user-1", "sid-a-1")
    assert ok is True
    remaining_ids = {r["id"] for r in client.tables["saved_items"]}
    assert "sid-a-1" not in remaining_ids
    assert "pid-a" in client.tables["products_by_id"], "productsの行まで消えている"


def test_delete_targets_id_and_user_id_explicitly():
    client = _FakeListClient(_make_saved_items(), _make_products())
    candidates.delete_saved_item(client, "user-1", "sid-a-1")
    filters = [c["filters"] for c in client.calls if c["kind"] == "delete"][0]
    assert filters == {"id": "sid-a-1", "user_id": "user-1"}


def test_delete_cannot_remove_other_users_item():
    client = _FakeListClient(_make_saved_items(), _make_products())
    candidates.delete_saved_item(client, "user-1", "sid-b-1")
    remaining_ids = {r["id"] for r in client.tables["saved_items"]}
    assert "sid-b-1" in remaining_ids, "他人の行が削除されてしまった"


def test_delete_failure_does_not_raise():
    client = _FakeListClient(_make_saved_items(), _make_products(), fail=True)
    ok = candidates.delete_saved_item(client, "user-1", "sid-a-1")
    assert ok is False


def test_missing_ids_are_rejected_before_any_call():
    client = _FakeListClient(_make_saved_items(), _make_products())
    assert candidates.update_saved_item(client, "", "sid-a-1", memo="x") is False
    assert candidates.update_saved_item(client, "user-1", "", memo="x") is False
    assert candidates.delete_saved_item(client, "", "sid-a-1") is False
    assert candidates.delete_saved_item(client, "user-1", "") is False
    assert client.calls == [], "id/user_idが空なのにDBへ問い合わせている"


# ── 秘密情報・メッセージの安全性 ───────────────────────────────────────────────

def test_list_messages_have_no_token_or_secret():
    for message in (candidates.LIST_FAILED, candidates.UPDATE_OK,
                    candidates.UPDATE_FAILED, candidates.ARCHIVE_OK,
                    candidates.UNARCHIVE_OK, candidates.DELETE_OK,
                    candidates.DELETE_FAILED):
        assert "token" not in message.lower()
        assert "exception" not in message.lower()
        assert "traceback" not in message.lower()


def test_update_exception_text_does_not_leak():
    client = _FakeListClient(_make_saved_items(), _make_products(), fail=True)
    ok = candidates.update_saved_item(client, "user-1", "sid-a-1", memo="x")
    assert ok is False  # 呼び出し側は文言のみを表示する（例外を保持しない設計）


# ── candidates_ui の画面切替・cand_ 状態 ──────────────────────────────────────

def test_view_switch_state_keys_have_cand_prefix():
    assert candidates_ui.VIEW.startswith("cand_")
    assert candidates_ui.SHOW_ARCHIVED.startswith("cand_")


def test_get_view_defaults_to_search():
    """ログイン直後（画面切替キーが未設定）は必ず商品をリサーチ"""
    state = {}
    assert candidates_ui.get_view(state) == candidates_ui.VIEW_SEARCH


def test_clear_state_removes_view_and_list_widget_keys():
    """画面切替・編集中の値・削除確認状態もログアウトで消える"""
    state = {}
    candidates_ui.init_state(state)
    state[candidates_ui.VIEW] = candidates_ui.VIEW_LIST
    state[candidates_ui.SHOW_ARCHIVED] = True
    state["cand_memo_sid-a-1"] = "編集中の下書き"
    state["cand_status_sid-a-1"] = "交渉中"
    state["cand_delconfirm_sid-a-1"] = True

    candidates_ui.clear_state(state)

    assert not any(str(k).startswith("cand_") for k in state)


# ── 保存日時のJST表示（saved_at表示のみ・DB値は変更しない）────────────────────

def test_format_saved_at_converts_utc_plus_offset_to_jst():
    """本番で確認した値が「2026年9月5日 11:27」になる（+00:00形式）"""
    assert candidates.format_saved_at_jst("2026-09-05T02:27:01.516747+00:00") == \
        "2026年9月5日 11:27"


def test_format_saved_at_accepts_z_suffix():
    """Z形式（UTC終端）にも対応する"""
    assert candidates.format_saved_at_jst("2026-09-05T02:27:01Z") == "2026年9月5日 11:27"


def test_format_saved_at_accepts_other_timezone_offset():
    """UTC以外のタイムゾーン付きでも正しくJSTへ変換する"""
    # 2026-09-05T20:00:00-05:00 は UTC 2026-09-06T01:00:00 -> JST 2026-09-06T10:00:00
    assert candidates.format_saved_at_jst("2026-09-05T20:00:00-05:00") == \
        "2026年9月6日 10:00"


def test_format_saved_at_crosses_date_boundary_forward():
    """UTC夜の時刻がJSTで翌日になる（日付をまたぐ変換）"""
    assert candidates.format_saved_at_jst("2026-09-05T20:00:00+00:00") == \
        "2026年9月6日 5:00"


def test_format_saved_at_naive_string_is_treated_as_utc():
    """タイムゾーンが無い文字列はUTCとして扱う"""
    assert candidates.format_saved_at_jst("2026-09-05T02:27:01") == "2026年9月5日 11:27"


def test_format_saved_at_none_returns_dash():
    assert candidates.format_saved_at_jst(None) == "－"


def test_format_saved_at_empty_string_returns_dash():
    assert candidates.format_saved_at_jst("") == "－"


def test_format_saved_at_blank_string_returns_dash():
    assert candidates.format_saved_at_jst("   ") == "－"


def test_format_saved_at_garbage_string_returns_dash():
    assert candidates.format_saved_at_jst("not-a-date") == "－"


def test_format_saved_at_non_string_returns_dash_without_raising():
    """int・dict等が渡っても例外を出さず「－」を返す"""
    assert candidates.format_saved_at_jst(12345) == "－"
    assert candidates.format_saved_at_jst({"a": 1}) == "－"
    assert candidates.format_saved_at_jst([]) == "－"


def test_format_saved_at_uses_only_stdlib_no_new_dependency():
    """datetime/timezone/timedelta以外の外部ライブラリを追加していない"""
    import pathlib
    text = pathlib.Path(candidates.__file__).read_text(encoding="utf-8")
    for forbidden in ("import pytz", "import dateutil", "from dateutil"):
        assert forbidden not in text, f"{forbidden} が candidates.py にある"


if __name__ == "__main__":
    sys.exit(run(dict(globals()), "候補保存テスト（偽クライアント・通信なし）"))
