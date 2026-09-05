# -*- coding: utf-8 -*-
"""実際にインストールされている Anthropic SDK との引数互換テスト

実行: python tests/test_sdk_compat.py

偽クライアントは何でも受け取ってしまうため、SDK側の破壊的変更を検出できない。
ここでは実SDKの messages.create のシグネチャに対して、アプリが実際に渡している
引数が bind できるかを検証する。

このテストは外部AI APIへHTTPリクエストを送らない。
create() は一度も呼ばず、inspect.signature による束縛検査だけを行う。
（Anthropic クライアントの生成自体は通信を伴わない。APIキーもダミー）
"""

import inspect
import sys

from _harness import run  # noqa: E402
import auth  # noqa: E402
import research_crowdfunding as r  # noqa: E402

# アプリが現時点で messages.create に渡している引数（増減したら気づけるようにする）
EXPECTED_KWARGS = {"model", "max_tokens", "messages"}


class _FakeMessages:
    """アプリが渡した引数だけを捕まえて、送信前に中断する"""

    def __init__(self, box):
        self.box = box

    def create(self, **kwargs):
        self.box.update(kwargs)
        raise RuntimeError("captured")


class _FakeClient:
    def __init__(self, box):
        self.messages = _FakeMessages(box)


def _actual_kwargs() -> dict:
    """本番と同じ経路でアプリが組み立てた create の引数を取り出す"""
    box: dict = {}
    r.analyze_with_claude(
        {"name": "X", "maker": "M", "platform": "Kickstarter",
         "raised_usd": 1, "raised_jpy": 1, "backers": 1,
         "genre": "T", "url": "u", "description": "d"},
        _FakeClient(box),
    )
    assert box, "analyze_with_claude が messages.create を呼んでいない"
    return box


def _real_signature() -> inspect.Signature:
    """実インストール済み SDK の messages.create のシグネチャ（通信しない）"""
    import anthropic
    client = anthropic.Anthropic(api_key="dummy-key-not-used")
    return inspect.signature(client.messages.create)


# ── 実SDKとの整合 ─────────────────────────────────────────────────────────────

def test_app_kwargs_bind_to_real_sdk():
    """アプリが渡す引数が実SDKの messages.create で受け付けられる"""
    sig = _real_signature()
    try:
        sig.bind(**_actual_kwargs())
    except TypeError as e:
        raise AssertionError(f"実SDKが引数を受け付けない: {e}") from None


def test_unknown_kwarg_is_rejected():
    """未知の引数を足すと bind に失敗する（この検査が機能していることの確認）"""
    sig = _real_signature()
    kwargs = dict(_actual_kwargs())
    kwargs["definitely_not_a_real_parameter"] = 1
    try:
        sig.bind(**kwargs)
    except TypeError:
        return
    raise AssertionError("未知の引数が通ってしまい、互換検査が機能していない")


def test_temperature_is_not_passed():
    """temperature を渡さない（新しいSDKでは削除された引数）"""
    assert "temperature" not in _actual_kwargs()


def test_required_kwargs_are_kept():
    """model・max_tokens・messages が渡され続けている"""
    kwargs = _actual_kwargs()
    for key in ("model", "max_tokens", "messages"):
        assert key in kwargs, f"{key} が渡されていない"
    assert kwargs["model"] == r.MODEL_ID, str(kwargs["model"])
    assert isinstance(kwargs["max_tokens"], int) and kwargs["max_tokens"] > 0
    assert kwargs["messages"] and kwargs["messages"][0]["role"] == "user"
    assert kwargs["messages"][0]["content"].strip(), "プロンプトが空"


def test_kwarg_set_is_unchanged():
    """渡す引数の顔ぶれが変わったら気づけるようにする

    system など新たに必要な引数を足したときは EXPECTED_KWARGS も更新し、
    実SDKで bind できることを上のテストで確認すること。
    """
    assert set(_actual_kwargs()) == EXPECTED_KWARGS, \
        str(set(_actual_kwargs()) ^ EXPECTED_KWARGS)


def test_model_id_is_accepted_as_string():
    """MODEL_ID が文字列として定義されている（SDKはモデル名を文字列で受ける）"""
    assert isinstance(r.MODEL_ID, str) and r.MODEL_ID.strip()


# ── Supabase SDK との整合 ─────────────────────────────────────────────────────
# 認証は通信せず、メソッドのシグネチャに対する束縛検査だけを行う。
# クライアントの生成自体は通信を伴わない（URL・キーはダミー）。

def _supabase_client():
    import supabase
    return supabase.create_client("https://example.supabase.co",
                                  "sb_publishable_dummy-not-a-real-key")


def _bind(func, *args, **kwargs):
    try:
        inspect.signature(func).bind(*args, **kwargs)
    except TypeError as e:
        raise AssertionError(f"実SDKが引数を受け付けない: {func.__name__}: {e}") from None


def test_create_client_signature():
    """create_client に URL とキーを渡せる"""
    import supabase
    _bind(supabase.create_client, "https://example.supabase.co", "sb_publishable_dummy")


def test_sign_in_with_password_signature():
    """ログインの引数が実SDKで受け付けられる"""
    client = _supabase_client()
    _bind(client.auth.sign_in_with_password,
          {"email": "a@example.com", "password": "dummy-password"})


def test_set_session_signature():
    """セッション復元の引数が実SDKで受け付けられる（将来のRLS用）"""
    client = _supabase_client()
    _bind(client.auth.set_session, "dummy-access", "dummy-refresh")


def test_update_user_signature():
    """パスワードと user_metadata の更新が実SDKで受け付けられる"""
    client = _supabase_client()
    _bind(client.auth.update_user,
          {"password": "dummy-password", "data": {auth.PASSWORD_CHANGED_FLAG: True}})


def test_sign_out_signature():
    """ログアウトが引数なしで呼べる"""
    _bind(_supabase_client().auth.sign_out)


def test_unknown_supabase_kwarg_is_rejected():
    """未知の引数を足すと bind に失敗する（検査が機能していることの確認）"""
    client = _supabase_client()
    try:
        inspect.signature(client.auth.set_session).bind(
            "a", "b", definitely_not_a_real_parameter=1)
    except TypeError:
        return
    raise AssertionError("未知の引数が通ってしまい、互換検査が機能していない")


# ── save_candidate RPC（候補保存・フェーズ3B）─────────────────────────────────
# rpc() 自体は postgrest 経由の一般的な呼び出しなので、実際にアプリが渡す
# 引数（fn名・paramsの辞書）で bind できることと、返ってきたビルダーに
# execute() があることを実SDKで確認する。通信はしない。

def test_rpc_call_signature_accepts_fn_and_params():
    """client.rpc(fn, params) の形でアプリが呼べる"""
    client = _supabase_client()
    _bind(client.rpc, "save_candidate", {"p_url_key": "x", "p_source_url": "y",
                                        "p_product": {}})


def test_rpc_builder_has_execute():
    """rpc(...) の戻り値に execute() があり、引数なしで呼べる"""
    client = _supabase_client()
    builder = client.rpc("save_candidate", {"p_url_key": "x", "p_source_url": "y",
                                            "p_product": {}})
    assert hasattr(builder, "execute"), "rpcの戻り値にexecuteが無い"
    _bind(builder.execute)


def test_candidates_module_uses_the_same_rpc_name():
    """candidates.py が実際に呼ぶRPC名が save_candidate である"""
    import candidates
    assert candidates.RPC_NAME == "save_candidate"


# ── マイ候補リストのselect/update/delete（フェーズ3C）───────────────────────────
# candidates.list_saved_items / update_saved_item / delete_saved_item が
# 実際に組み立てる呼び出し（table().select()/.update()/.delete() → .eq() →
# .order() → .execute()）が実SDKのシグネチャに束縛できることを確認する。
# 通信はしない（テーブル・クライアントの実体には一切触れない）。

def test_table_select_eq_order_execute_signature():
    """一覧取得の組み立て方（select→eq→order→execute）が実SDKで通る"""
    client = _supabase_client()
    q = client.table("saved_items").select(
        "id,product_id,memo,status,priority_override,archived,saved_at,updated_at,"
        "products(id,source_url,platform,name,maker,priority)"
    )
    _bind(q.eq, "user_id", "u1")
    q = q.eq("user_id", "u1")
    _bind(q.eq, "archived", False)
    q = q.eq("archived", False)
    _bind(q.order, "saved_at", desc=True)
    q = q.order("saved_at", desc=True)
    assert hasattr(q, "execute")
    _bind(q.execute)


def test_table_update_eq_execute_signature():
    """更新の組み立て方（update→eq→eq→execute）が実SDKで通る"""
    client = _supabase_client()
    u = client.table("saved_items").update({"memo": "x", "status": "候補"})
    _bind(u.eq, "id", "sid-1")
    u = u.eq("id", "sid-1")
    _bind(u.eq, "user_id", "u1")
    u = u.eq("user_id", "u1")
    assert hasattr(u, "execute")
    _bind(u.execute)


def test_table_delete_eq_execute_signature():
    """削除の組み立て方（delete→eq→eq→execute）が実SDKで通る"""
    client = _supabase_client()
    d = client.table("saved_items").delete()
    _bind(d.eq, "id", "sid-1")
    d = d.eq("id", "sid-1")
    _bind(d.eq, "user_id", "u1")
    d = d.eq("user_id", "u1")
    assert hasattr(d, "execute")
    _bind(d.execute)


def test_unknown_table_kwarg_is_rejected():
    """未知の引数を足すと bind に失敗する（検査が機能していることの確認）"""
    client = _supabase_client()
    q = client.table("saved_items").select("id")
    try:
        inspect.signature(q.eq).bind("id", "sid-1", definitely_not_a_real_arg=1)
    except TypeError:
        return
    raise AssertionError("未知の引数が通ってしまい、互換検査が機能していない")


# ── 管理者ビュー（フェーズ3D）: is_admin RPC・count・range ────────────────────────
# candidates.is_admin / list_admin_profiles / list_admin_saved_items が
# 実際に組み立てる呼び出しが実SDKのシグネチャに束縛できることを確認する。
# 通信はしない。

def test_is_admin_rpc_call_signature_accepts_fn_only():
    """client.rpc(fn) を引数無しで呼べる（is_admin()はパラメータを取らない）"""
    client = _supabase_client()
    _bind(client.rpc, "is_admin")


def test_is_admin_rpc_builder_has_execute():
    client = _supabase_client()
    builder = client.rpc("is_admin")
    assert hasattr(builder, "execute"), "rpcの戻り値にexecuteが無い"
    _bind(builder.execute)


def test_candidates_module_uses_the_same_is_admin_rpc_name():
    import candidates
    assert candidates.IS_ADMIN_RPC_NAME == "is_admin"


def test_profiles_select_signature_accepts_columns():
    """profiles の select が列名の文字列を受け付ける"""
    client = _supabase_client()
    _bind(client.table("profiles").select, "user_id,email,display_name")


def test_saved_items_select_accepts_count_exact_kwarg():
    """count='exact' を select に渡せる（管理者ビューの件数取得に使う）"""
    client = _supabase_client()
    _bind(client.table("saved_items").select,
          "id,user_id,memo,status,priority_override,archived,saved_at,"
          "products(id,source_url,platform,name,maker,priority)",
          count="exact")


def test_saved_items_select_eq_order_range_execute_signature():
    """管理者ビューの一覧取得の組み立て方（select→eq→order→range→execute）が
    実SDKで通る"""
    client = _supabase_client()
    q = client.table("saved_items").select(
        "id,user_id,memo,status,priority_override,archived,saved_at,"
        "products(id,source_url,platform,name,maker,priority)",
        count="exact",
    )
    _bind(q.eq, "archived", False)
    q = q.eq("archived", False)
    _bind(q.order, "saved_at", desc=True)
    q = q.order("saved_at", desc=True)
    _bind(q.range, 0, 49)
    q = q.range(0, 49)
    assert hasattr(q, "execute")
    _bind(q.execute)


def test_unknown_count_value_is_rejected_by_bind_check():
    """countに未知のキーワード名を渡すと束縛検査が失敗する（検査自体の健全性確認）"""
    client = _supabase_client()
    try:
        inspect.signature(client.table("saved_items").select).bind(
            "id", definitely_not_a_real_kwarg="exact")
    except TypeError:
        return
    raise AssertionError("未知の引数が通ってしまい、互換検査が機能していない")


if __name__ == "__main__":
    sys.exit(run(dict(globals()), "実SDK引数互換テスト（HTTP通信なし）"))
