# -*- coding: utf-8 -*-
"""送信者プロフィール（sender_profiles.py）の決定論テスト

実行: python tests/test_sender_profiles.py

Supabase への通信は偽クライアントで捕捉する。外部AI APIもネットワークも
使わない。
"""

import sys

from _harness import run  # noqa: E402
import sender_profiles as sp  # noqa: E402

_UID_1 = "user-1"
_UID_2 = "user-2"


# ── 偽クライアント（インメモリDBを模す）────────────────────────────────────────

class _FakeAPIError(Exception):
    """postgrest.exceptions.APIError を模す（.code属性と制約名を含む文言を持つ）"""

    def __init__(self, code="23505", constraint="sender_profiles_unique_per_user"):
        super().__init__(
            f'duplicate key value violates unique constraint "{constraint}" (code={code})'
        )
        self.code = code


class _FakeResult:
    def __init__(self, data):
        self.data = data


class _FakeQuery:
    def __init__(self, store, kind, payload=None):
        self._store = store
        self._kind = kind
        self._payload = payload
        self._filters = {}
        self._neq_filters = {}
        self._order = None

    def select(self, columns):
        return self

    def eq(self, column, value):
        self._filters[column] = value
        return self

    def neq(self, column, value):
        self._neq_filters[column] = value
        return self

    def order(self, column, desc=False):
        self._order = (column, desc)
        return self

    def _matches(self, row):
        for k, v in self._filters.items():
            if row.get(k) != v:
                return False
        for k, v in self._neq_filters.items():
            if row.get(k) == v:
                return False
        return True

    def _dup_exists(self, user_id, name, company, exclude_id=None):
        for r in self._store.rows:
            if exclude_id is not None and r.get("id") == exclude_id:
                continue
            if (r.get("user_id") == user_id and r.get("sender_name_en") == name
                    and r.get("company_name_en", "") == company):
                return True
        return False

    def execute(self):
        self._store.calls.append(self._kind)
        if self._store.fail:
            raise RuntimeError("connection refused: db.example.internal:5432")
        rows = self._store.rows

        if self._kind == "select":
            matched = [r for r in rows if self._matches(r)]
            if self._order:
                col, desc = self._order
                matched = sorted(matched, key=lambda r: r.get(col, ""), reverse=desc)
            return _FakeResult([dict(r) for r in matched])

        if self._kind == "insert":
            new_row = dict(self._payload or {})
            name = new_row.get("sender_name_en", "")
            company = new_row.get("company_name_en", "")
            if self._store.fail_default_conflict and new_row.get("is_default"):
                raise _FakeAPIError(constraint="sender_profiles_one_default_per_user")
            if self._dup_exists(new_row.get("user_id"), name, company):
                raise _FakeAPIError()
            new_row.setdefault("id", f"pid-{len(rows) + 1}")
            new_row.setdefault("company_name_en", "")
            new_row.setdefault("is_default", False)
            new_row.setdefault("last_used_at", None)
            new_row.setdefault("created_at", "2026-01-01T00:00:00+00:00")
            new_row.setdefault("updated_at", "2026-01-01T00:00:00+00:00")
            rows.append(new_row)
            return _FakeResult([dict(new_row)])

        if self._kind == "update":
            payload = self._payload or {}
            for forbidden in ("user_id", "id", "created_at"):
                assert forbidden not in payload, f"{forbidden} を更新しようとしている"
            matched = [r for r in rows if self._matches(r)]
            if self._store.fail_default_conflict and payload.get("is_default"):
                raise _FakeAPIError(constraint="sender_profiles_one_default_per_user")
            if "sender_name_en" in payload or "company_name_en" in payload:
                for r in matched:
                    new_name = payload.get("sender_name_en", r.get("sender_name_en"))
                    new_company = payload.get("company_name_en", r.get("company_name_en", ""))
                    if self._dup_exists(r.get("user_id"), new_name, new_company,
                                       exclude_id=r.get("id")):
                        raise _FakeAPIError()
            for r in matched:
                r.update(payload)
            return _FakeResult([dict(r) for r in matched])

        if self._kind == "delete":
            assert self._filters.get("id"), "削除対象のidが指定されていない"
            assert self._filters.get("user_id"), "削除対象のuser_idが指定されていない"
            matched = [r for r in rows if self._matches(r)]
            self._store.rows = [r for r in rows if not self._matches(r)]
            return _FakeResult([dict(r) for r in matched])

        raise AssertionError(f"未対応の操作: {self._kind}")


class _FakeTable:
    def __init__(self, store, name):
        assert name == "sender_profiles", name
        self._store = store

    def select(self, columns):
        return _FakeQuery(self._store, "select")

    def insert(self, payload):
        return _FakeQuery(self._store, "insert", payload)

    def update(self, payload):
        return _FakeQuery(self._store, "update", payload)

    def delete(self):
        return _FakeQuery(self._store, "delete")


class _FakeClient:
    def __init__(self, rows=None, fail=False, fail_default_conflict=False):
        self.rows = [dict(r) for r in (rows or [])]
        self.calls = []
        self.fail = fail
        self.fail_default_conflict = fail_default_conflict

    def table(self, name):
        return _FakeTable(self, name)


def _profile(pid="p1", user_id=_UID_1, name="Taro Yamada", company="",
            is_default=False, last_used_at=None):
    return {"id": pid, "user_id": user_id, "sender_name_en": name,
            "company_name_en": company, "is_default": is_default,
            "last_used_at": last_used_at,
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-01T00:00:00+00:00"}


# ── 入力の整形・検証 ───────────────────────────────────────────────────────────

def test_normalize_text_strips_surrounding_whitespace():
    assert sp.normalize_text("  Hirohito Ohmatsu  ") == "Hirohito Ohmatsu"
    assert sp.normalize_text(None) == ""
    assert sp.normalize_text("") == ""


def test_validate_fields_rejects_empty_name():
    assert sp.validate_fields("", "Base on Base LLC") == sp.EMPTY_NAME
    assert sp.validate_fields("   ", "") == sp.EMPTY_NAME


def test_validate_fields_accepts_empty_company():
    """会社名は任意（空でもOK）"""
    assert sp.validate_fields("Hirohito Ohmatsu", "") == ""


def test_validate_fields_rejects_too_long_name():
    assert sp.validate_fields("A" * 201, "") == sp.NAME_TOO_LONG


def test_validate_fields_rejects_too_long_company():
    assert sp.validate_fields("Taro", "B" * 201) == sp.COMPANY_TOO_LONG


def test_validate_fields_allows_common_latin_name_characters():
    """アポストロフィ・ハイフン・ピリオド・カンマ・アンパサンド・数字・
    アクセント記号など、英語圏の氏名・会社名で通常使われる文字は拒否しない"""
    assert sp.validate_fields("Seán O'Brien-Smith", "Müller & Sons, Ltd. 2nd") == ""


def test_contains_japanese_detects_hiragana_katakana_kanji():
    assert sp.contains_japanese("田中太郎") is True
    assert sp.contains_japanese("タロウ") is True
    assert sp.contains_japanese("たろう") is True


def test_contains_japanese_false_for_english_with_symbols():
    assert sp.contains_japanese("Seán O'Brien-Smith & Co., Ltd. 2nd") is False
    assert sp.contains_japanese("") is False
    assert sp.contains_japanese(None) is False


def test_japanese_warning_empty_when_both_fields_are_english():
    assert sp.japanese_warning("Taro Yamada", "Acme LLC") == ""


def test_japanese_warning_empty_when_company_is_blank():
    """会社名が未入力（空）なら、会社名側の警告は出ない（入力された項目だけが対象）"""
    assert sp.japanese_warning("Taro Yamada", "") == ""


def test_japanese_warning_names_only_the_name_field():
    msg = sp.japanese_warning("田中太郎", "Acme LLC")
    assert msg != ""
    assert "氏名" in msg
    assert "会社名" not in msg


def test_japanese_warning_names_only_the_company_field():
    msg = sp.japanese_warning("Taro Yamada", "株式会社サンプル")
    assert msg != ""
    assert "会社名" in msg
    assert "氏名" not in msg


def test_japanese_warning_names_both_fields_when_both_contain_japanese():
    msg = sp.japanese_warning("田中太郎", "株式会社サンプル")
    assert "氏名" in msg
    assert "会社名" in msg


# ── 自動選択 ───────────────────────────────────────────────────────────────────

def test_pick_auto_selected_single_profile():
    p = _profile()
    assert sp.pick_auto_selected([sp.Profile(**p)]).id == "p1"


def test_pick_auto_selected_no_profiles_returns_none():
    assert sp.pick_auto_selected([]) is None


def test_pick_auto_selected_prefers_default_over_last_used():
    profiles = [
        sp.Profile(**_profile("p1", last_used_at="2026-02-01T00:00:00+00:00")),
        sp.Profile(**_profile("p2", is_default=True, last_used_at="2026-01-01T00:00:00+00:00")),
    ]
    assert sp.pick_auto_selected(profiles).id == "p2"


def test_pick_auto_selected_falls_back_to_most_recently_used():
    profiles = [
        sp.Profile(**_profile("p1", last_used_at="2026-01-01T00:00:00+00:00")),
        sp.Profile(**_profile("p2", last_used_at="2026-03-01T00:00:00+00:00")),
    ]
    assert sp.pick_auto_selected(profiles).id == "p2"


def test_pick_auto_selected_returns_none_when_ambiguous():
    """2件以上あり、既定も使用履歴も無ければ推測で選ばない"""
    profiles = [sp.Profile(**_profile("p1")), sp.Profile(**_profile("p2"))]
    assert sp.pick_auto_selected(profiles) is None


def test_pick_auto_selected_multiple_defaults_is_deterministic():
    """異常系（複数defaultが同時に存在）でも決定論的に1件選ぶ"""
    profiles = [
        sp.Profile(**_profile("p1", is_default=True, last_used_at="2026-01-01T00:00:00+00:00")),
        sp.Profile(**_profile("p2", is_default=True, last_used_at="2026-02-01T00:00:00+00:00")),
    ]
    assert sp.pick_auto_selected(profiles).id == "p2"


# ── 一覧取得・user_idごとのデータ分離 ─────────────────────────────────────────

def test_list_profiles_returns_only_own_rows():
    client = _FakeClient([_profile("p1", _UID_1), _profile("p2", _UID_2)])
    result = sp.list_profiles(client, _UID_1)
    assert [p.id for p in result] == ["p1"]


def test_list_profiles_filters_by_user_id_explicitly():
    """呼び出しが.eq("user_id", ...)で絞り込んでいる（RLS任せにしない）"""
    import inspect
    text = inspect.getsource(sp.list_profiles)
    assert '.eq("user_id", user_id)' in text


def test_list_profiles_empty_for_user_with_no_profiles():
    client = _FakeClient([_profile("p1", _UID_2)])
    assert sp.list_profiles(client, _UID_1) == []


def test_list_profiles_returns_none_on_failure():
    client = _FakeClient(fail=True)
    assert sp.list_profiles(client, _UID_1) is None


def test_list_profiles_returns_empty_on_missing_user_id():
    client = _FakeClient([_profile()])
    assert sp.list_profiles(client, "") == []


def test_list_profiles_does_not_use_select_star():
    import pathlib
    text = pathlib.Path(sp.__file__).read_text(encoding="utf-8")
    assert 'select("*")' not in text and "select('*')" not in text


# ── 新規登録 ───────────────────────────────────────────────────────────────────

def test_create_profile_success():
    client = _FakeClient()
    pid, error = sp.create_profile(client, _UID_1, "Hirohito Ohmatsu", "Base on Base LLC")
    assert error == ""
    assert pid is not None
    assert client.rows[0]["user_id"] == _UID_1
    assert client.rows[0]["sender_name_en"] == "Hirohito Ohmatsu"
    assert client.rows[0]["company_name_en"] == "Base on Base LLC"


def test_create_profile_strips_whitespace_before_saving():
    client = _FakeClient()
    sp.create_profile(client, _UID_1, "  Taro Yamada  ", "  Acme  ")
    assert client.rows[0]["sender_name_en"] == "Taro Yamada"
    assert client.rows[0]["company_name_en"] == "Acme"


def test_create_profile_rejects_empty_name_without_calling_db():
    client = _FakeClient()
    pid, error = sp.create_profile(client, _UID_1, "   ", "Acme")
    assert pid is None
    assert error == sp.EMPTY_NAME
    assert client.rows == [], "空文字なのにDBへ保存されている"
    assert client.calls == [], "空文字なのにDBを呼んでいる"


def test_create_profile_rejects_duplicate_name_and_company():
    """同一利用者が氏名+会社名の完全に同じ組み合わせを重複登録できない"""
    client = _FakeClient([_profile("p1", _UID_1, "Taro Yamada", "Acme")])
    pid, error = sp.create_profile(client, _UID_1, "Taro Yamada", "Acme")
    assert pid is None
    assert error == sp.DUPLICATE
    assert len(client.rows) == 1, "重複登録されてしまっている"


def test_create_profile_same_name_different_company_is_not_duplicate():
    client = _FakeClient([_profile("p1", _UID_1, "Taro Yamada", "Acme")])
    pid, error = sp.create_profile(client, _UID_1, "Taro Yamada", "Other LLC")
    assert error == ""
    assert pid is not None


def test_create_profile_same_name_and_company_different_user_is_allowed():
    """別利用者であれば同じ氏名+会社名を登録できる（一意制約はuser_id込み）"""
    client = _FakeClient([_profile("p1", _UID_1, "Taro Yamada", "Acme")])
    pid, error = sp.create_profile(client, _UID_2, "Taro Yamada", "Acme")
    assert error == ""
    assert pid is not None


def test_create_profile_failure_does_not_raise():
    client = _FakeClient(fail=True)
    pid, error = sp.create_profile(client, _UID_1, "Taro Yamada", "")
    assert pid is None
    assert error == sp.SAVE_FAILED


def test_create_profile_with_name_only_succeeds():
    """会社名・屋号は任意。氏名だけでも登録できる（個人事業主など会社名を持たない利用者向け）"""
    client = _FakeClient()
    pid, error = sp.create_profile(client, _UID_1, "Hirohito Ohmatsu", "")
    assert error == ""
    assert pid is not None
    assert client.rows[0]["company_name_en"] == ""


def test_create_profile_whitespace_only_company_saved_as_empty():
    client = _FakeClient()
    sp.create_profile(client, _UID_1, "Taro Yamada", "   ")
    assert client.rows[0]["company_name_en"] == ""


def test_profile_label_without_company_has_no_trailing_separator():
    """会社名なしのプロフィールが自然な表示になる（末尾に｜が残らない）"""
    p = sp.Profile(**_profile(name="Hirohito Ohmatsu", company=""))
    assert p.label == "Hirohito Ohmatsu"
    assert "｜" not in p.label


def test_profile_label_with_company_shows_both():
    p = sp.Profile(**_profile(name="Hirohito Ohmatsu", company="Base on Base LLC"))
    assert p.label == "Hirohito Ohmatsu｜Base on Base LLC"


def test_create_profile_with_is_default_conflict_returns_default_conflict_message():
    """is_defaultの部分一意索引への同時更新の競合は、氏名・会社名重複とは別の文言になる"""
    client = _FakeClient(fail_default_conflict=True)
    pid, error = sp.create_profile(client, _UID_1, "Taro Yamada", "", is_default=True)
    assert pid is None
    assert error == sp.DEFAULT_CONFLICT
    assert error != sp.DUPLICATE


def test_update_profile_with_is_default_conflict_returns_default_conflict_message():
    client = _FakeClient([_profile("p1", _UID_1, "Taro", "Co")],
                         fail_default_conflict=True)
    error = sp.update_profile(client, _UID_1, "p1", "Taro", "Co", is_default=True)
    assert error == sp.DEFAULT_CONFLICT


def test_create_profile_with_is_default_clears_other_defaults():
    client = _FakeClient([_profile("p1", _UID_1, "A", is_default=True)])
    pid, error = sp.create_profile(client, _UID_1, "B", "", is_default=True)
    assert error == ""
    by_id = {r["id"]: r for r in client.rows}
    assert by_id["p1"]["is_default"] is False
    assert by_id[pid]["is_default"] is True


# ── 更新 ──────────────────────────────────────────────────────────────────────

def test_update_profile_success():
    client = _FakeClient([_profile("p1", _UID_1, "Taro", "Old Co")])
    error = sp.update_profile(client, _UID_1, "p1", "Taro Yamada", "New Co")
    assert error == ""
    assert client.rows[0]["sender_name_en"] == "Taro Yamada"
    assert client.rows[0]["company_name_en"] == "New Co"


def test_update_profile_strips_whitespace():
    client = _FakeClient([_profile("p1", _UID_1, "Taro", "Co")])
    sp.update_profile(client, _UID_1, "p1", "  Taro Yamada  ", "  New Co  ")
    assert client.rows[0]["sender_name_en"] == "Taro Yamada"
    assert client.rows[0]["company_name_en"] == "New Co"


def test_update_profile_rejects_empty_name():
    client = _FakeClient([_profile("p1", _UID_1, "Taro", "Co")])
    error = sp.update_profile(client, _UID_1, "p1", "", "Co")
    assert error == sp.EMPTY_NAME
    assert client.rows[0]["sender_name_en"] == "Taro"  # 変更されていない


def test_update_profile_rejects_rename_into_duplicate():
    client = _FakeClient([
        _profile("p1", _UID_1, "Taro", "Acme"),
        _profile("p2", _UID_1, "Jiro", "Acme"),
    ])
    error = sp.update_profile(client, _UID_1, "p2", "Taro", "Acme")
    assert error == sp.DUPLICATE


def test_update_profile_cannot_modify_other_users_profile():
    """他人のuser_idでは更新対象が無い（idだけでなくuser_idでも絞る）"""
    client = _FakeClient([_profile("p1", _UID_2, "Taro", "Co")])
    error = sp.update_profile(client, _UID_1, "p1", "Hacked", "Co")
    assert client.rows[0]["sender_name_en"] == "Taro"  # 変更されていない


def test_update_profile_targets_id_and_user_id_explicitly():
    import inspect
    text = inspect.getsource(sp.update_profile)
    assert '.eq("id", profile_id)' in text
    assert '.eq("user_id", user_id)' in text


def test_update_profile_failure_does_not_raise():
    client = _FakeClient(fail=True)
    error = sp.update_profile(client, _UID_1, "p1", "Taro", "")
    assert error == sp.UPDATE_FAILED


def test_update_profile_setting_default_clears_other_defaults():
    client = _FakeClient([
        _profile("p1", _UID_1, "A", is_default=True),
        _profile("p2", _UID_1, "B"),
    ])
    error = sp.update_profile(client, _UID_1, "p2", "B", "", is_default=True)
    assert error == ""
    by_id = {r["id"]: r for r in client.rows}
    assert by_id["p1"]["is_default"] is False
    assert by_id["p2"]["is_default"] is True


# ── 削除 ──────────────────────────────────────────────────────────────────────

def test_delete_profile_success():
    client = _FakeClient([_profile("p1", _UID_1)])
    ok = sp.delete_profile(client, _UID_1, "p1")
    assert ok is True
    assert client.rows == []


def test_delete_profile_cannot_remove_other_users_profile():
    client = _FakeClient([_profile("p1", _UID_2)])
    sp.delete_profile(client, _UID_1, "p1")
    assert len(client.rows) == 1, "他人のプロフィールが削除されてしまっている"


def test_delete_profile_targets_id_and_user_id_explicitly():
    import inspect
    text = inspect.getsource(sp.delete_profile)
    assert '.eq("id", profile_id)' in text
    assert '.eq("user_id", user_id)' in text


def test_delete_profile_failure_does_not_raise():
    client = _FakeClient(fail=True)
    assert sp.delete_profile(client, _UID_1, "p1") is False


def test_delete_profile_missing_ids_rejected_before_any_call():
    client = _FakeClient([_profile("p1", _UID_1)])
    assert sp.delete_profile(client, "", "p1") is False
    assert sp.delete_profile(client, _UID_1, "") is False
    assert client.calls == []


# ── 最後に使用した記録（touch_last_used）──────────────────────────────────────

def test_touch_last_used_sets_timestamp():
    client = _FakeClient([_profile("p1", _UID_1)])
    ok = sp.touch_last_used(client, _UID_1, "p1")
    assert ok is True
    assert client.rows[0]["last_used_at"]


def test_touch_last_used_cannot_touch_other_users_profile():
    client = _FakeClient([_profile("p1", _UID_2)])
    sp.touch_last_used(client, _UID_1, "p1")
    assert client.rows[0]["last_used_at"] is None


def test_touch_last_used_failure_does_not_raise():
    client = _FakeClient(fail=True)
    assert sp.touch_last_used(client, _UID_1, "p1") is False


# ── 文言・権限との独立性 ───────────────────────────────────────────────────────

def test_messages_have_no_token_or_secret():
    for message in (sp.LIST_FAILED, sp.SAVE_FAILED, sp.UPDATE_FAILED, sp.DELETE_FAILED,
                   sp.DUPLICATE, sp.DEFAULT_CONFLICT, sp.EMPTY_NAME, sp.NAME_TOO_LONG,
                   sp.COMPANY_TOO_LONG, sp.CREATE_OK, sp.UPDATE_OK, sp.DELETE_OK,
                   sp.japanese_warning("田中太郎", "株式会社サンプル")):
        assert "token" not in message.lower()
        assert "exception" not in message.lower()
        assert "traceback" not in message.lower()


def test_module_never_references_role_or_is_admin():
    """このモジュールは画面モード判定・管理者判定の仕組みには一切関わらない
    （調査・保存だけを行う）"""
    import pathlib
    text = pathlib.Path(sp.__file__).read_text(encoding="utf-8").lower()
    assert "app_metadata" not in text
    assert "is_admin" not in text


def test_module_only_tables_sender_profiles_directly():
    import pathlib
    import re
    text = pathlib.Path(sp.__file__).read_text(encoding="utf-8")
    tables = re.findall(r'\.table\(\s*["\']([^"\']+)["\']', text)
    assert tables, "table() 呼び出しが見つからない"
    assert set(tables) == {"sender_profiles"}, set(tables)


if __name__ == "__main__":
    sys.exit(run(dict(globals()), "送信者プロフィールテスト（偽クライアント・通信なし）"))
