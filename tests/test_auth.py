# -*- coding: utf-8 -*-
"""メール＋パスワード認証の決定論テスト

実行: python tests/test_auth.py

Supabase への通信は偽クライアントで捕捉する。外部AI APIもネットワークも使わない。
トークンの実値はテスト出力にも出さない（値の有無だけを検査する）。
"""

import sys

from _harness import run  # noqa: E402
import auth  # noqa: E402
import search_state as sstate  # noqa: E402

SECRETS = {"SUPABASE_URL": "https://example.supabase.co",
           "SUPABASE_PUBLISHABLE_KEY": "sb_publishable_dummy"}

# テスト内でだけ使う偽のトークン（本物ではない）
_A = "fake-access"
_R = "fake-refresh"


def _code_of(name: str) -> str:
    """コメントと文字列リテラルを除いた「実際に動くコード」だけを返す

    説明のためのコメントに禁止語が出てくるのは問題ではない。
    検査したいのは、そのキーや機能を実際に使っていないことなので、
    コメントと文字列を落としてから調べる。
    """
    import io
    import tokenize
    from pathlib import Path as _Path
    source = (_Path(__file__).resolve().parent.parent / name).read_text(encoding="utf-8")
    kept = []
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type in (tokenize.COMMENT, tokenize.STRING):
            continue
        kept.append(token.string)
    return " ".join(kept)


class _Obj:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def _user(changed=True, uid="user-1", email="a@example.com"):
    return _Obj(id=uid, email=email,
                user_metadata=({"password_changed": True} if changed else {}))


class _FakeAuth:
    """Supabase の auth を模す。通信はせず、呼ばれた記録だけ残す"""

    def __init__(self, user=None, fail=False):
        self._user = user
        self._fail = fail
        self.calls = []
        self.updated = None
        self.session_set = None

    def sign_in_with_password(self, credentials):
        self.calls.append("sign_in_with_password")
        if self._fail or self._user is None:
            raise RuntimeError(f"Invalid login credentials for {credentials}")
        return _Obj(session=_Obj(access_token=_A, refresh_token=_R),
                    user=self._user)

    def set_session(self, access_token, refresh_token):
        self.calls.append("set_session")
        self.session_set = (access_token, refresh_token)

    def update_user(self, attributes):
        self.calls.append("update_user")
        if self._fail:
            raise RuntimeError("update failed")
        self.updated = attributes
        data = attributes.get("data") or {}
        metadata = dict(getattr(self._user, "user_metadata", {}) or {})
        metadata.update(data)
        self._user.user_metadata = metadata
        return _Obj(user=self._user)

    def sign_out(self):
        self.calls.append("sign_out")


class _FakeClient:
    def __init__(self, user=None, fail=False):
        self.auth = _FakeAuth(user, fail)


def _logged_in_state(changed=True) -> dict:
    state: dict = {}
    sstate.init_state(state)
    client = _FakeClient(_user(changed=changed))
    ok, _ = auth.sign_in(client, "a@example.com", "temp-password", state)
    assert ok
    return state


# ── Secrets ───────────────────────────────────────────────────────────────────

def test_reads_publishable_key_only():
    """新形式のキー名だけを読む"""
    url, key = auth.read_secrets(SECRETS)
    assert url == SECRETS["SUPABASE_URL"]
    assert key == SECRETS["SUPABASE_PUBLISHABLE_KEY"]


def test_never_falls_back_to_anon_key():
    """SUPABASE_ANON_KEY があってもフォールバックしない"""
    secrets = {"SUPABASE_URL": SECRETS["SUPABASE_URL"],
               "SUPABASE_ANON_KEY": "legacy-should-not-be-used"}
    try:
        auth.read_secrets(secrets)
    except auth.ConfigError as e:
        assert "SUPABASE_PUBLISHABLE_KEY" in str(e)
        assert "ANON" not in str(e).upper()
        return
    raise AssertionError("旧 anon key にフォールバックしている")


def test_missing_secrets_message_has_no_values():
    """設定不足の文言に秘密情報の値を含めない"""
    try:
        auth.read_secrets({"SUPABASE_URL": "https://example.supabase.co"})
    except auth.ConfigError as e:
        assert "https://example.supabase.co" not in str(e)
        assert "設定" in str(e)
        return
    raise AssertionError("設定不足で例外にならない")


def test_source_does_not_reference_forbidden_keys():
    """SUPABASE_ANON_KEY・service_role・secret key を参照していない"""
    for name in ("auth.py", "auth_ui.py", "streamlit_app.py"):
        upper = _code_of(name).upper()
        for word in ("SUPABASE_ANON_KEY", "SERVICE_ROLE", "SECRET_KEY", "SB_SECRET"):
            assert word not in upper, f"{name} が {word} を参照している"
    # 実際に読み取る Secrets 名は新形式の2つだけ
    assert auth.KEY_SECRET == "SUPABASE_PUBLISHABLE_KEY"
    assert auth.URL_SECRET == "SUPABASE_URL"


def test_client_is_created_per_call():
    """クライアントを毎回作り、モジュール側に保持しない"""
    made = []

    def factory(url, key):
        made.append((url, key))
        return _FakeClient()

    first = auth.make_client(SECRETS, factory=factory)
    second = auth.make_client(SECRETS, factory=factory)
    assert first is not second, "同じクライアントを使い回している"
    assert len(made) == 2


def test_module_holds_no_client_or_token():
    """認証クライアントやトークンをモジュール変数に置いていない"""
    state = _logged_in_state()
    for name in dir(auth):
        if name.startswith("__"):
            continue
        value = getattr(auth, name)
        if isinstance(value, str):
            assert _A not in value and _R not in value, f"{name} にトークンが残っている"
        assert not isinstance(value, _FakeClient), f"{name} にクライアントが残っている"
    assert state[auth.ACCESS_TOKEN] == _A       # トークンはセッション側にだけある


def test_no_cache_resource_on_auth():
    """認証まわりで st.cache_resource / cache_data を使っていない

    キャッシュは全セッションで共有されるため、認証状態を載せてはいけない。
    """
    for name in ("auth.py", "auth_ui.py", "streamlit_app.py"):
        code = _code_of(name)
        assert "cache_resource" not in code, name
        assert "cache_data" not in code, name


def test_auth_gate_runs_before_anthropic():
    """認証ゲートが Anthropic API の利用より前にある

    未ログインの時点で st.stop() するため、AI処理にも検索にも到達しない。
    """
    from pathlib import Path
    app = (Path(__file__).resolve().parent.parent / "streamlit_app.py").read_text(
        encoding="utf-8")
    gate = app.index("auth_ui.require_login()")
    for marker in ('st.secrets["ANTHROPIC_API_KEY"]', "analyze_with_claude(p,",
                   "st.text_area("):
        assert gate < app.index(marker), f"{marker} が認証ゲートより前にある"


# ── ログイン ──────────────────────────────────────────────────────────────────

def test_sign_in_success():
    """ログインに成功すると本人の情報が保持される"""
    state: dict = {}
    sstate.init_state(state)
    client = _FakeClient(_user())
    ok, message = auth.sign_in(client, "a@example.com", "password-1234", state)
    assert ok and message == ""
    assert auth.is_authenticated(state) is True
    assert auth.current_email(state) == "a@example.com"
    assert state[auth.USER_ID] == "user-1"


def test_sign_in_failure_leaves_no_credentials():
    """ログイン失敗時に認証情報を残さない"""
    state = _logged_in_state()
    client = _FakeClient(fail=True)
    ok, message = auth.sign_in(client, "a@example.com", "wrong", state)
    assert ok is False
    assert auth.is_authenticated(state) is False
    for key in auth.KEYS:
        assert key not in state, f"{key} が残っている"
    assert message == auth.LOGIN_FAILED


def test_failure_message_does_not_reveal_account():
    """エラー文から利用者の存在を推測できない"""
    state: dict = {}
    sstate.init_state(state)
    unknown = auth.sign_in(_FakeClient(fail=True), "nobody@example.com", "x", state)[1]
    wrong_password = auth.sign_in(_FakeClient(fail=True), "a@example.com", "x", state)[1]
    empty = auth.sign_in(_FakeClient(_user()), "", "", state)[1]
    assert unknown == wrong_password == empty == auth.LOGIN_FAILED
    for message in (unknown, wrong_password, empty):
        for word in ("未登録", "存在しません", "not found", "Invalid login", "確認"):
            assert word not in message, message


def test_failure_message_has_no_exception_detail():
    """例外の中身（認証情報を含みうる）を利用者へ出さない"""
    state: dict = {}
    sstate.init_state(state)
    ok, message = auth.sign_in(_FakeClient(fail=True), "a@example.com", "secret-pw", state)
    assert ok is False
    assert "secret-pw" not in message
    assert "RuntimeError" not in message and "Traceback" not in message


def test_sign_in_without_session_is_rejected():
    """セッションが返らなければログイン成立とみなさない"""
    class _NoSession(_FakeAuth):
        def sign_in_with_password(self, credentials):
            return _Obj(session=None, user=_user())

    state: dict = {}
    sstate.init_state(state)
    client = _FakeClient()
    client.auth = _NoSession(_user())
    ok, message = auth.sign_in(client, "a@example.com", "pw", state)
    assert ok is False and message == auth.LOGIN_FAILED
    assert auth.is_authenticated(state) is False


# ── 初回パスワード変更 ────────────────────────────────────────────────────────

def test_first_login_requires_password_change():
    """password_changed が無いユーザーは初回ログイン扱いになる"""
    state = _logged_in_state(changed=False)
    assert auth.is_authenticated(state) is True
    assert auth.must_change_password(state) is True


def test_changed_user_does_not_require_change():
    """password_changed=true のユーザーは初回扱いにしない"""
    assert auth.must_change_password(_logged_in_state(changed=True)) is False


def test_password_shorter_than_minimum_is_rejected():
    """10文字未満を拒否する"""
    short = "a" * (auth.MIN_PASSWORD_LENGTH - 1)
    assert auth.validate_password(short, short) == auth.PASSWORD_TOO_SHORT
    state = _logged_in_state(changed=False)
    client = _FakeClient(_user(changed=False))
    ok, message = auth.change_password(client, state, short, short)
    assert ok is False and message == auth.PASSWORD_TOO_SHORT
    assert "update_user" not in client.auth.calls, "検証前に変更を送っている"
    assert auth.must_change_password(state) is True


def test_password_confirmation_mismatch_is_rejected():
    """確認入力の不一致を拒否する"""
    state = _logged_in_state(changed=False)
    client = _FakeClient(_user(changed=False))
    ok, message = auth.change_password(client, state, "abcdefghij", "abcdefghik")
    assert ok is False and message == auth.PASSWORD_MISMATCH
    assert "update_user" not in client.auth.calls


def test_password_change_sets_flag_and_signs_out():
    """変更成功で password_changed=true になり、必ずログアウトする"""
    state = _logged_in_state(changed=False)
    user = _user(changed=False)
    client = _FakeClient(user)
    sstate.save_success(state, [{"商品名": "検索結果"}], ["https://example.com"])
    ok, message = auth.change_password(client, state, "new-password-1", "new-password-1")
    assert ok is True and message == auth.PASSWORD_CHANGED
    assert client.auth.updated["data"] == {"password_changed": True}
    assert client.auth.updated["password"] == "new-password-1"
    assert user.user_metadata.get("password_changed") is True
    assert "sign_out" in client.auth.calls, "変更後にログアウトしていない"
    assert auth.is_authenticated(state) is False
    assert sstate.has_results(state) is False


def test_password_change_failure_keeps_login():
    """変更に失敗しても勝手にログアウトせず、内部の理由も出さない"""
    state = _logged_in_state(changed=False)
    client = _FakeClient(_user(changed=False), fail=True)
    ok, message = auth.change_password(client, state, "new-password-1", "new-password-1")
    assert ok is False and message == auth.PASSWORD_CHANGE_FAILED
    assert "update failed" not in message
    assert auth.is_authenticated(state) is True


def test_password_change_requires_login():
    """未ログインではパスワードを変更できない"""
    state: dict = {}
    sstate.init_state(state)
    client = _FakeClient(_user())
    ok, message = auth.change_password(client, state, "new-password-1", "new-password-1")
    assert ok is False and message == auth.SESSION_EXPIRED
    assert "update_user" not in client.auth.calls


def test_normal_user_can_change_password():
    """通常ログイン後（初回ではない）でも本人がパスワードを変更できる"""
    state = _logged_in_state(changed=True)
    client = _FakeClient(_user(changed=True))
    ok, _ = auth.change_password(client, state, "another-password", "another-password")
    assert ok is True
    assert client.auth.updated["data"] == {"password_changed": True}
    assert auth.is_authenticated(state) is False, "変更後はログアウトする"


def test_metadata_is_not_used_for_admin_rights():
    """user_metadata は初回変更判定にだけ使い、管理者判定には使わない"""
    code = _code_of("auth.py")
    for word in ("admin", "is_staff", "権限"):
        assert word not in code.lower(), f"auth.py が {word} を扱っている"
    # user_metadata から読むキーは password_changed だけ
    assert auth.PASSWORD_CHANGED_FLAG == "password_changed"
    state = _logged_in_state(changed=True)
    assert set(state) & {"auth_is_admin", "auth_role"} == set()


# ── セッション復元（将来のRLS用）──────────────────────────────────────────────

def test_apply_session_uses_user_token():
    """保持中のトークンをクライアントへ戻す（本人のJWTでDBへ行く構造）"""
    state = _logged_in_state()
    client = _FakeClient(_user())
    assert auth.apply_session(client, state) is True
    assert client.auth.session_set == (_A, _R)


def test_apply_session_failure_logs_out():
    """復元に失敗したら安全側に倒してログアウト扱いにする"""
    class _Broken(_FakeAuth):
        def set_session(self, access_token, refresh_token):
            raise RuntimeError(f"expired token {access_token}")

    state = _logged_in_state()
    client = _FakeClient()
    client.auth = _Broken()
    assert auth.apply_session(client, state) is False
    assert auth.is_authenticated(state) is False


def test_apply_session_without_tokens_logs_out():
    """トークンが無ければログアウト扱いにする"""
    state: dict = {}
    sstate.init_state(state)
    assert auth.apply_session(_FakeClient(), state) is False
    assert auth.is_authenticated(state) is False


# ── ログアウト ────────────────────────────────────────────────────────────────

def test_sign_out_clears_everything():
    """ログアウトで認証情報も検索状態もすべて消える"""
    state = _logged_in_state()
    sstate.save_success(state, [{"商品名": "消える"}], ["https://example.com"])
    sstate.record_failure(state, "エラー")
    client = _FakeClient(_user())
    auth.sign_out(client, state)
    assert "sign_out" in client.auth.calls
    for key in auth.KEYS:
        assert key not in state, f"{key} が残っている"
    assert sstate.has_results(state) is False
    assert sstate.get_query(state) == []
    assert sstate.get_error(state) == ""


def test_sign_out_clears_even_if_supabase_fails():
    """Supabase 側の失敗にかかわらず手元の情報は必ず消す"""
    class _Broken(_FakeAuth):
        def sign_out(self):
            raise RuntimeError("network down")

    state = _logged_in_state()
    client = _FakeClient()
    client.auth = _Broken()
    auth.sign_out(client, state)
    assert auth.is_authenticated(state) is False


def test_sign_out_keeps_unrelated_input():
    """ログアウトは入力欄など無関係なセッション項目に触らない"""
    state = _logged_in_state()
    state["urls_text_widget"] = "https://example.com/keep"
    auth.sign_out(_FakeClient(_user()), state)
    assert state["urls_text_widget"] == "https://example.com/keep"


# ── トークンの取り扱い ────────────────────────────────────────────────────────

def test_tokens_never_appear_in_messages():
    """画面へ出す文言にトークンが混ざらない"""
    state = _logged_in_state(changed=False)
    client = _FakeClient(_user(changed=False), fail=True)
    messages = [auth.LOGIN_FAILED, auth.CONFIG_MISSING, auth.SESSION_EXPIRED,
                auth.PASSWORD_TOO_SHORT, auth.PASSWORD_MISMATCH,
                auth.PASSWORD_CHANGE_FAILED, auth.PASSWORD_CHANGED,
                auth.sign_in(client, "a@example.com", "pw", dict(state))[1],
                auth.change_password(client, state, "abcdefghij", "abcdefghij")[1]]
    for message in messages:
        assert _A not in message and _R not in message, message
        assert "token" not in message.lower(), message


def test_hidden_keys_are_declared():
    """画面・ログへ出してはいけないキーが明示されている"""
    assert auth.ACCESS_TOKEN in auth.NEVER_DISPLAY
    assert auth.REFRESH_TOKEN in auth.NEVER_DISPLAY


def test_sessions_do_not_share_credentials():
    """別セッションの認証情報と混ざらない"""
    a = _logged_in_state()
    b: dict = {}
    sstate.init_state(b)
    assert auth.is_authenticated(a) is True
    assert auth.is_authenticated(b) is False
    auth.sign_out(_FakeClient(_user()), a)
    assert auth.is_authenticated(b) is False


if __name__ == "__main__":
    sys.exit(run(dict(globals()), "認証テスト（偽クライアント・通信なし）"))
