# -*- coding: utf-8 -*-
"""ブラウザ更新後もログイン状態を復元する、永続Cookie（暗号化）のテスト

実行: python tests/test_auth_cookies.py
実際のブラウザ・Streamlitコンポーネント・Supabaseへの通信は一切行わない。
Cookieの読み書きは偽のCookieManager（.get/.set/.delete を持つだけの
インメモリ辞書）に差し替えてテストする（auth.make_client の factory引数と
同じ、依存を外から渡すパターン）。
"""

import sys
import time

from _harness import run  # noqa: E402
import auth  # noqa: E402
import auth_cookies  # noqa: E402
import search_state as sstate  # noqa: E402

from cryptography.fernet import Fernet  # noqa: E402

_KEY = Fernet.generate_key().decode("utf-8")
_OTHER_KEY = Fernet.generate_key().decode("utf-8")
SECRETS = {auth_cookies.SECRET_NAME: _KEY}

_R = "fake-refresh-token"
_A2 = "fake-access-2"
_R2 = "fake-refresh-2"


class _Obj:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def _user(uid="user-1", email="a@example.com"):
    return _Obj(id=uid, email=email, user_metadata={"password_changed": True},
               app_metadata={})


class _FakeAuth:
    """restore_session_from_refresh_token() が呼ぶ範囲だけを模す偽auth"""

    def __init__(self, user=None, fail=False):
        self._user = user
        self._fail = fail
        self.calls = []

    def sign_in_with_password(self, credentials):
        self.calls.append(("sign_in_with_password", credentials))
        if self._fail or self._user is None:
            raise RuntimeError("invalid login")
        return _Obj(session=_Obj(access_token="fake-access", refresh_token=_R),
                    user=self._user)

    def refresh_session(self, refresh_token):
        self.calls.append(("refresh_session", refresh_token))
        if self._fail or self._user is None:
            raise RuntimeError("invalid or expired refresh token")
        return _Obj(session=_Obj(access_token=_A2, refresh_token=_R2), user=self._user)


class _FakeClient:
    def __init__(self, user=None, fail=False):
        self.auth = _FakeAuth(user, fail)


class _FakeCookieManager:
    """extra_streamlit_components.CookieManager を模す（実ブラウザ・JS不使用）

    実物のdelete()は、存在しないCookie名を渡すとKeyErrorになる実装のため、
    ここでも同じ挙動にする（auth_cookies._safe_delete がそれを握りつぶす
    ことを実際に確認するため）。
    """

    def __init__(self, cookies=None):
        self.cookies = dict(cookies or {})
        self.set_calls = []
        self.delete_calls = []

    def get(self, name):
        return self.cookies.get(name)

    def set(self, name, value, **kwargs):
        self.set_calls.append((name, value, kwargs))
        self.cookies[name] = value

    def delete(self, name, key="delete"):
        self.delete_calls.append(name)
        del self.cookies[name]  # 実物と同じ：無ければKeyError


def _new_state() -> dict:
    state: dict = {}
    sstate.init_state(state)
    return state


# ── 暗号化・復号（純粋関数） ─────────────────────────────────────────────────────

def test_encrypt_then_decrypt_round_trip():
    """暗号化したrefresh tokenは、同じ鍵で正しく復号できる"""
    fernet = auth_cookies.make_fernet(SECRETS)
    token = auth_cookies.encrypt_refresh_token(fernet, _R)
    assert auth_cookies.decrypt_refresh_token(fernet, token) == _R


def test_tampered_cookie_value_fails_to_decrypt():
    """改ざんされた（1文字変えた）Cookie値は復号に失敗しNoneを返す"""
    fernet = auth_cookies.make_fernet(SECRETS)
    token = auth_cookies.encrypt_refresh_token(fernet, _R)
    tampered = token[:-1] + ("A" if token[-1] != "A" else "B")
    assert auth_cookies.decrypt_refresh_token(fernet, tampered) is None


def test_decrypt_with_wrong_key_fails():
    """暗号化した鍵と異なる鍵では復号できない（Noneを返し例外は出さない）"""
    fernet_a = auth_cookies.make_fernet(SECRETS)
    fernet_b = auth_cookies.make_fernet({auth_cookies.SECRET_NAME: _OTHER_KEY})
    token = auth_cookies.encrypt_refresh_token(fernet_a, _R)
    assert auth_cookies.decrypt_refresh_token(fernet_b, token) is None


def test_decrypt_rejects_token_older_than_max_age():
    """MAX_AGE_SECONDSより古い（ttl超過の）値は、改ざんされていなくても拒否する"""
    fernet = auth_cookies.make_fernet(SECRETS)
    long_ago = int(time.time()) - auth_cookies.MAX_AGE_SECONDS - 10
    old_token = fernet.encrypt_at_time(_R.encode("utf-8"), current_time=long_ago).decode("utf-8")
    assert auth_cookies.decrypt_refresh_token(fernet, old_token) is None


def test_decrypt_empty_or_garbage_value_returns_none_without_raising():
    """空文字・意味のない文字列でも例外を出さずNoneを返す"""
    fernet = auth_cookies.make_fernet(SECRETS)
    assert auth_cookies.decrypt_refresh_token(fernet, "") is None
    assert auth_cookies.decrypt_refresh_token(fernet, "not-a-valid-fernet-token") is None


def test_missing_secret_gives_no_fernet():
    """AUTH_COOKIE_KEYが未設定なら、例外を出さずNoneを返す"""
    assert auth_cookies.load_key({}) is None
    assert auth_cookies.make_fernet({}) is None


def test_empty_secret_gives_no_fernet():
    """AUTH_COOKIE_KEYが空文字でも、例外を出さずNoneを返す"""
    assert auth_cookies.make_fernet({auth_cookies.SECRET_NAME: ""}) is None


def test_invalid_secret_value_gives_no_fernet():
    """AUTH_COOKIE_KEYが不正な形式（Fernetキーとして無効）でも、例外を出さずNoneを返す"""
    assert auth_cookies.make_fernet({auth_cookies.SECRET_NAME: "not-a-real-fernet-key"}) is None


# ── key_status（秘密の値を出さずに鍵の状態だけを確認する） ──────────────────────────

def test_key_status_missing_when_secret_absent():
    assert auth_cookies.key_status({}) == auth_cookies.KEY_STATUS_MISSING


def test_key_status_invalid_when_secret_is_not_a_fernet_key():
    assert auth_cookies.key_status(
        {auth_cookies.SECRET_NAME: "not-a-real-fernet-key"}) == auth_cookies.KEY_STATUS_INVALID


def test_key_status_configured_when_secret_is_valid():
    assert auth_cookies.key_status(SECRETS) == auth_cookies.KEY_STATUS_CONFIGURED


# ── begin_remember_login / ensure_login_saved（ログイン直後の二段階保存） ──────────
#
# 実ブラウザでは、Cookie書き込み要求（set()）の反映はサーバ→ブラウザの
# 非同期往復を経るため、「書き込みを要求した直後の読み取り」では確認できない
# ことがある。_FakeCookieManager（即時反映）だけでなく、_AsyncBrowser
# （次回のCookieManager構築まで反映されない）でもテストする。

class _AsyncBrowser:
    """実ブラウザのCookieストアを模す共有バックエンド

    set()は即座にはstoredへ反映されない。CookieManagerがready_after回
    構築されるまで（＝実ブラウザでのJS実行・応答が複数回のStreamlit
    rerunを経て届くまで）反映を遅らせる。
    """

    def __init__(self, cookies=None, ready_after=1):
        self.stored = dict(cookies or {})
        self.pending_writes = []
        self.ready_after = ready_after
        self.constructions = 0

    def new_manager(self):
        self.constructions += 1
        if self.constructions > self.ready_after:
            for name, val in self.pending_writes:
                self.stored[name] = val
            self.pending_writes = []
        return _AsyncCookieManager(self)


class _AsyncCookieManager:
    """構築時点のstoredのスナップショットだけを読む（実CookieManagerの
    __init__時getAllに相当）。set()はブラウザへの書き込み要求を記録する
    だけで、このインスタンス自身の見える値はその場では変わらない。
    """

    def __init__(self, browser: _AsyncBrowser):
        self._browser = browser
        self._snapshot = dict(browser.stored)

    def get(self, name):
        return self._snapshot.get(name)

    def set(self, name, value, **kwargs):
        self._browser.pending_writes.append((name, value))

    def delete(self, name, key="delete"):
        if name not in self._browser.stored:
            raise KeyError(name)
        del self._browser.stored[name]


def test_begin_remember_login_sets_pending_flag_and_resets_attempts():
    """ログイン成功直後はis_save_pending()がTrueになり、試行回数は0から始まる"""
    state = _new_state()
    auth_cookies.begin_remember_login(state)
    assert auth_cookies.is_save_pending(state) is True
    assert state[auth_cookies.SAVE_ATTEMPTS] == 0


def test_ensure_login_saved_writes_encrypted_cookie_with_secure_attributes():
    """1回目の呼び出しで、refresh tokenが暗号化されてCookieへ書き込み要求される
    （Secure・SameSite=strict・30日・path=/。HttpOnlyはCookieManagerの制約で不可）"""
    state = _new_state()
    state[auth.REFRESH_TOKEN] = _R
    auth_cookies.begin_remember_login(state)
    manager = _FakeCookieManager()

    status = auth_cookies.ensure_login_saved(
        state, secrets=SECRETS, manager_factory=lambda: manager)

    assert status == auth_cookies.STATUS_PENDING  # 確認は次回以降
    assert len(manager.set_calls) == 1
    _, raw, options = manager.set_calls[0]
    assert raw != _R, "平文のまま保存している（暗号化されていない）"
    fernet = auth_cookies.make_fernet(SECRETS)
    assert auth_cookies.decrypt_refresh_token(fernet, raw) == _R
    assert options["secure"] is True
    assert options["same_site"] == "strict"
    assert options["path"] == "/"
    assert options["max_age"] == 30 * 24 * 60 * 60


def test_ensure_login_saved_confirms_with_simple_fake_manager_within_two_calls():
    """即時反映の偽Cookieでも、確認は（set()自身の戻り値ではなく）次回読み取りで行う"""
    state = _new_state()
    state[auth.REFRESH_TOKEN] = _R
    auth_cookies.begin_remember_login(state)
    manager = _FakeCookieManager()

    status1 = auth_cookies.ensure_login_saved(
        state, secrets=SECRETS, manager_factory=lambda: manager)
    assert status1 == auth_cookies.STATUS_PENDING
    assert auth_cookies.is_save_pending(state) is True

    status2 = auth_cookies.ensure_login_saved(
        state, secrets=SECRETS, manager_factory=lambda: manager)
    assert status2 == auth_cookies.STATUS_CONFIRMED
    assert auth_cookies.is_save_pending(state) is False
    assert state[auth_cookies.SAVE_ATTEMPTS] == 0


def test_ensure_login_saved_confirms_only_after_async_browser_write_lands():
    """非同期Cookie（書き込みが次のrerunまで反映されない）: 1回目はpending、
    実際にブラウザへ届いた2回目で初めてconfirmedになる"""
    browser = _AsyncBrowser(ready_after=1)
    state = _new_state()
    state[auth.REFRESH_TOKEN] = _R
    auth_cookies.begin_remember_login(state)

    status1 = auth_cookies.ensure_login_saved(
        state, secrets=SECRETS, manager_factory=browser.new_manager)
    assert status1 == auth_cookies.STATUS_PENDING
    assert auth_cookies.COOKIE_NAME not in browser.stored, (
        "ブラウザへ届く前なのに、届いたことにしてしまっている")

    status2 = auth_cookies.ensure_login_saved(
        state, secrets=SECRETS, manager_factory=browser.new_manager)
    assert status2 == auth_cookies.STATUS_CONFIRMED
    fernet = auth_cookies.make_fernet(SECRETS)
    assert auth_cookies.decrypt_refresh_token(fernet, browser.stored[auth_cookies.COOKIE_NAME]) == _R


def test_ensure_login_saved_does_not_confirm_on_stale_mismatched_cookie():
    """前回ログイン分など、別のrefresh tokenの暗号化Cookieが残っていても、
    一致しない限り確認済みにはしない（書き込みを要求して次回へ回す）"""
    fernet = auth_cookies.make_fernet(SECRETS)
    stale = auth_cookies.encrypt_refresh_token(fernet, "old-refresh-token")
    manager = _FakeCookieManager(cookies={auth_cookies.COOKIE_NAME: stale})
    state = _new_state()
    state[auth.REFRESH_TOKEN] = _R
    auth_cookies.begin_remember_login(state)

    status = auth_cookies.ensure_login_saved(
        state, secrets=SECRETS, manager_factory=lambda: manager)
    assert status == auth_cookies.STATUS_PENDING
    assert len(manager.set_calls) == 1


def test_ensure_login_saved_gives_up_after_max_attempts_without_blocking_login():
    """確認できる見込みが無い場合でも、試行上限でtimeoutし通常ログインを止めない"""
    state = _new_state()
    state[auth.REFRESH_TOKEN] = _R
    auth_cookies.begin_remember_login(state)

    class _NeverConfirmsManager(_FakeCookieManager):
        def get(self, name):
            return None  # ブラウザへ一切届かない状況を模す

    status = auth_cookies.STATUS_PENDING
    calls = 0
    while status == auth_cookies.STATUS_PENDING:
        status = auth_cookies.ensure_login_saved(
            state, secrets=SECRETS, manager_factory=_NeverConfirmsManager)
        calls += 1
        assert calls <= auth_cookies.SAVE_CONFIRM_MAX_ATTEMPTS, "試行上限で終わっていない"

    assert status == auth_cookies.STATUS_TIMEOUT
    assert auth_cookies.is_save_pending(state) is False


def test_ensure_login_saved_without_key_finishes_immediately_without_writing():
    """AUTH_COOKIE_KEY未設定なら、Cookieへは一切書き込まず即座に終わる（通常ログインは継続）"""
    state = _new_state()
    state[auth.REFRESH_TOKEN] = _R
    auth_cookies.begin_remember_login(state)
    manager = _FakeCookieManager()

    status = auth_cookies.ensure_login_saved(
        state, secrets={}, manager_factory=lambda: manager)

    assert status == auth_cookies.STATUS_NO_KEY
    assert manager.set_calls == []
    assert auth_cookies.is_save_pending(state) is False


def test_ensure_login_saved_without_refresh_token_finishes_as_no_token():
    """session_stateにrefresh tokenが無ければ、書き込まずno_tokenで終わる（想定外ケース）"""
    state = _new_state()
    auth_cookies.begin_remember_login(state)
    manager = _FakeCookieManager()

    status = auth_cookies.ensure_login_saved(
        state, secrets=SECRETS, manager_factory=lambda: manager)

    assert status == auth_cookies.STATUS_NO_TOKEN
    assert manager.set_calls == []
    assert auth_cookies.is_save_pending(state) is False


def test_ensure_login_saved_is_noop_when_not_pending():
    """保存待ちで無い状態（既に確定済み）で呼んでも何もしない"""
    state = _new_state()

    class _Tripwire(_FakeCookieManager):
        def get(self, name):
            raise AssertionError("保存待ちでないのにCookieへ読みに行った")

    status = auth_cookies.ensure_login_saved(
        state, secrets=SECRETS, manager_factory=lambda: _Tripwire())
    assert status == auth_cookies.STATUS_CONFIRMED


# ── forget_login（ログアウト・パスワード変更時にCookieを消す） ───────────────────

def test_forget_login_deletes_existing_cookie():
    """既にCookieがある場合、ログアウトで確実に消える"""
    fernet = auth_cookies.make_fernet(SECRETS)
    token = auth_cookies.encrypt_refresh_token(fernet, _R)
    manager = _FakeCookieManager(cookies={auth_cookies.COOKIE_NAME: token})
    auth_cookies.forget_login(manager_factory=lambda: manager)
    assert auth_cookies.COOKIE_NAME not in manager.cookies


def test_forget_login_without_existing_cookie_does_not_raise():
    """Cookieがそもそも無い状態でログアウトしても例外を出さない

    （元のCookieManager.delete()は未設定の名前に対してKeyErrorになる実装の
    ため、auth_cookies側でこれを握りつぶせていることを確認する）
    """
    manager = _FakeCookieManager()  # 空
    auth_cookies.forget_login(manager_factory=lambda: manager)  # 例外が出なければ成功


# ── restore_login_from_cookie（起動時の復元） ─────────────────────────────────────

def test_restore_login_from_cookie_success_and_rotates_cookie():
    """有効なCookieから復元でき、復元後は新しいrefresh tokenでCookieも更新される"""
    fernet = auth_cookies.make_fernet(SECRETS)
    token = auth_cookies.encrypt_refresh_token(fernet, _R)
    manager = _FakeCookieManager(cookies={auth_cookies.COOKIE_NAME: token})
    state = _new_state()
    client = _FakeClient(_user())

    auth_cookies.restore_login_from_cookie(
        client, state, secrets=SECRETS, manager_factory=lambda: manager)

    assert auth.is_authenticated(state) is True
    assert state[auth.USER_ID] == "user-1"
    assert state[auth.REFRESH_TOKEN] == _R2  # ローテーション後の値
    # Cookieも新しい値へ更新されている（古いrefresh tokenはもう無効なため）
    new_raw = manager.cookies[auth_cookies.COOKIE_NAME]
    assert auth_cookies.decrypt_refresh_token(fernet, new_raw) == _R2
    assert state[auth_cookies.RESTORE_DONE] is True


def test_restore_login_from_cookie_with_expired_refresh_token_deletes_cookie():
    """Supabase側で失効・無効と判定されたrefresh tokenは、Cookieを削除してログイン画面へ戻す"""
    fernet = auth_cookies.make_fernet(SECRETS)
    token = auth_cookies.encrypt_refresh_token(fernet, _R)
    manager = _FakeCookieManager(cookies={auth_cookies.COOKIE_NAME: token})
    state = _new_state()
    client = _FakeClient(fail=True)  # refresh_session()が例外を投げる

    auth_cookies.restore_login_from_cookie(
        client, state, secrets=SECRETS, manager_factory=lambda: manager)

    assert auth.is_authenticated(state) is False
    assert auth_cookies.COOKIE_NAME not in manager.cookies
    assert state[auth_cookies.RESTORE_DONE] is True


def test_restore_login_from_cookie_tampered_value_deletes_cookie_without_calling_supabase():
    """改ざんされたCookieは復号できず、Supabaseへは一切問い合わせずに削除する"""
    manager = _FakeCookieManager(cookies={auth_cookies.COOKIE_NAME: "garbage-not-fernet"})
    state = _new_state()
    client = _FakeClient(_user())

    auth_cookies.restore_login_from_cookie(
        client, state, secrets=SECRETS, manager_factory=lambda: manager)

    assert auth.is_authenticated(state) is False
    assert auth_cookies.COOKIE_NAME not in manager.cookies
    assert client.auth.calls == [], "復号に失敗した値でSupabaseへ問い合わせてしまっている"


def test_restore_login_from_cookie_without_key_deletes_cookie_and_keeps_normal_login_usable():
    """AUTH_COOKIE_KEY未設定時は、永続Cookieだけ無効化し、クラッシュしない"""
    manager = _FakeCookieManager(cookies={auth_cookies.COOKIE_NAME: "whatever"})
    state = _new_state()
    client = _FakeClient(_user())

    auth_cookies.restore_login_from_cookie(
        client, state, secrets={}, manager_factory=lambda: manager)

    assert auth.is_authenticated(state) is False
    assert auth_cookies.COOKIE_NAME not in manager.cookies
    assert client.auth.calls == []


def test_restore_login_from_cookie_with_no_cookie_present_does_not_finalize():
    """Cookieが無い（まだコンポーネントから届いていない可能性も含む）場合は、
    確定フラグを立てずに通常ログイン画面へ進む（後で本当の値が届けば次回また試せる）"""
    manager = _FakeCookieManager()  # Cookie無し
    state = _new_state()
    client = _FakeClient(_user())

    auth_cookies.restore_login_from_cookie(
        client, state, secrets=SECRETS, manager_factory=lambda: manager)

    assert auth.is_authenticated(state) is False
    assert auth_cookies.RESTORE_DONE not in state
    assert client.auth.calls == []


def test_restore_login_from_cookie_does_not_retry_once_failed_in_same_session():
    """同じセッション内で一度失敗が確定したら、再実行（rerun）でも再試行しない

    （無限ループの防止、およびrefresh tokenの無駄な消費・ローテーションの防止）
    """
    manager = _FakeCookieManager(cookies={auth_cookies.COOKIE_NAME: "garbage"})
    state = _new_state()
    client = _FakeClient(_user())

    auth_cookies.restore_login_from_cookie(
        client, state, secrets=SECRETS, manager_factory=lambda: manager)
    assert state[auth_cookies.RESTORE_DONE] is True
    calls_after_first = len(client.auth.calls)

    # 同じ(壊れた)Cookieがまだ残っていても、2回目は何もしない
    manager.cookies[auth_cookies.COOKIE_NAME] = "garbage-again"
    auth_cookies.restore_login_from_cookie(
        client, state, secrets=SECRETS, manager_factory=lambda: manager)
    assert len(client.auth.calls) == calls_after_first


def test_restore_login_from_cookie_skips_when_already_authenticated():
    """既にログイン済みのセッションでは、Cookieを読みにも行かない"""
    state = _new_state()
    client = _FakeClient(_user())
    ok, _ = auth.sign_in(client, "a@example.com", "temp-password", state)
    assert ok
    client.auth.calls.clear()

    class _Tripwire(_FakeCookieManager):
        def get(self, name):
            raise AssertionError("ログイン済みなのにCookieを読みに行った")

    auth_cookies.restore_login_from_cookie(
        client, state, secrets=SECRETS, manager_factory=lambda: _Tripwire())
    assert auth.is_authenticated(state) is True


# ── 起動時の復元確認（is_restore_checking）：Cookie未到達時に即断しない ──────────────
#
# F5直後、CookieManagerの値がまだ届いていない1回目の実行で「Cookie無し」と
# 即断してログインフォームを出すと、実際には有効なCookieがある場合でも
# 一瞬ログイン画面が表示されてしまう。試行回数だけで早期に打ち切らないことを
# 確認するため、now（time.time()の代わり）を差し替えて実時間の経過を模す。

class _NeverArrivesCookieManager(_FakeCookieManager):
    """ブラウザにCookieが全く無い（または応答がまだ届かない）状況を模す"""


def test_is_restore_checking_true_while_cookie_not_yet_arrived():
    """Cookieがまだ届かない間は、認証もRESTORE_DONEも確定せず「確認中」のまま"""
    state = _new_state()
    client = _FakeClient(_user())
    manager = _NeverArrivesCookieManager()

    auth_cookies.restore_login_from_cookie(
        client, state, secrets=SECRETS, manager_factory=lambda: manager, now=1000.0)

    assert auth_cookies.is_restore_checking(state) is True
    assert auth_cookies.RESTORE_DONE not in state
    assert client.auth.calls == [], "Cookieが届いていないのにSupabaseへ問い合わせている"


def test_is_restore_checking_many_fast_calls_does_not_finalize_before_deadline():
    """st.rerunが速く繰り返されても、実時間の期限前・試行回数の安全弁未満なら
    「無し」と断定しない（回数だけで早期に打ち切らない、という要件の確認）"""
    state = _new_state()
    client = _FakeClient(_user())
    manager = _NeverArrivesCookieManager()
    base = 1000.0
    calls = max(1, auth_cookies.RESTORE_CHECK_HARD_MAX_ATTEMPTS // 2)  # 安全弁の半分程度

    for i in range(calls):
        auth_cookies.restore_login_from_cookie(
            client, state, secrets=SECRETS, manager_factory=lambda: manager,
            now=base + i * 0.01)  # 実時間はほとんど進んでいない

    assert auth_cookies.is_restore_checking(state) is True
    assert auth_cookies.RESTORE_DONE not in state


def test_restore_check_finalizes_as_absent_after_real_time_deadline():
    """実時間で期限（RESTORE_CHECK_MAX_WAIT_SECONDS）を過ぎたら、Cookie無しと確定する"""
    state = _new_state()
    client = _FakeClient(_user())
    manager = _NeverArrivesCookieManager()
    base = 1000.0

    auth_cookies.restore_login_from_cookie(
        client, state, secrets=SECRETS, manager_factory=lambda: manager, now=base)
    assert auth_cookies.is_restore_checking(state) is True

    auth_cookies.restore_login_from_cookie(
        client, state, secrets=SECRETS, manager_factory=lambda: manager,
        now=base + auth_cookies.RESTORE_CHECK_MAX_WAIT_SECONDS + 0.1)

    assert auth_cookies.is_restore_checking(state) is False
    assert state[auth_cookies.RESTORE_DONE] is True
    assert client.auth.calls == [], "Cookieが無いのにSupabaseへ問い合わせている"


def test_restore_check_finalizes_via_hard_attempt_cap_if_clock_never_advances():
    """時計が進まない異常系でも、試行回数の安全弁で必ず終わる（無限ループ防止）"""
    state = _new_state()
    client = _FakeClient(_user())
    manager = _NeverArrivesCookieManager()
    frozen_now = 2000.0

    for _ in range(auth_cookies.RESTORE_CHECK_HARD_MAX_ATTEMPTS - 1):
        auth_cookies.restore_login_from_cookie(
            client, state, secrets=SECRETS, manager_factory=lambda: manager, now=frozen_now)
    assert auth_cookies.is_restore_checking(state) is True, "安全弁の手前で確定してしまっている"

    auth_cookies.restore_login_from_cookie(
        client, state, secrets=SECRETS, manager_factory=lambda: manager, now=frozen_now)
    assert auth_cookies.is_restore_checking(state) is False
    assert state[auth_cookies.RESTORE_DONE] is True


def test_restore_check_succeeds_on_delayed_cookie_arrival_without_early_timeout():
    """数回待った後に実際にCookieが届けば、タイムアウト扱いにせず正しく復元する
    （遅延応答時に早期タイムアウトしないことの確認）"""
    fernet = auth_cookies.make_fernet(SECRETS)
    token = auth_cookies.encrypt_refresh_token(fernet, _R)

    class _DelayedCookieManager(_FakeCookieManager):
        def __init__(self, arrive_after):
            super().__init__()
            self._arrive_after = arrive_after
            self._calls = 0

        def get(self, name):
            self._calls += 1
            if self._calls < self._arrive_after:
                return None
            return token

    state = _new_state()
    client = _FakeClient(_user())
    manager = _DelayedCookieManager(arrive_after=3)
    base = 1000.0

    for i in range(3):
        auth_cookies.restore_login_from_cookie(
            client, state, secrets=SECRETS, manager_factory=lambda: manager,
            now=base + i * 0.5)  # 期限(5秒)内

    assert auth.is_authenticated(state) is True
    assert state[auth.REFRESH_TOKEN] == _R2  # ローテーション後
    assert auth_cookies.is_restore_checking(state) is False


def test_is_restore_checking_false_once_already_authenticated():
    """既にログイン済みなら確認中ではない（Cookieを読みに行かない既存仕様の再確認）"""
    state = _new_state()
    client = _FakeClient(_user())
    ok, _ = auth.sign_in(client, "a@example.com", "temp-password", state)
    assert ok
    assert auth_cookies.is_restore_checking(state) is False


# ── 複数利用者の分離 ─────────────────────────────────────────────────────────────

def test_two_users_cookies_restore_independently_without_cross_contamination():
    """2人分の暗号化Cookie・state・偽クライアントは、互いに混ざらず独立して復元される"""
    fernet = auth_cookies.make_fernet(SECRETS)

    state_a = _new_state()
    manager_a = _FakeCookieManager(
        cookies={auth_cookies.COOKIE_NAME: auth_cookies.encrypt_refresh_token(fernet, "r-a")})
    client_a = _FakeClient(_user(uid="user-a", email="a@example.com"))

    state_b = _new_state()
    manager_b = _FakeCookieManager(
        cookies={auth_cookies.COOKIE_NAME: auth_cookies.encrypt_refresh_token(fernet, "r-b")})
    client_b = _FakeClient(_user(uid="user-b", email="b@example.com"))

    auth_cookies.restore_login_from_cookie(
        client_a, state_a, secrets=SECRETS, manager_factory=lambda: manager_a)
    auth_cookies.restore_login_from_cookie(
        client_b, state_b, secrets=SECRETS, manager_factory=lambda: manager_b)

    assert state_a[auth.USER_ID] == "user-a"
    assert state_b[auth.USER_ID] == "user-b"
    assert state_a[auth.ACCESS_TOKEN] != "" and state_b[auth.ACCESS_TOKEN] != ""

    # Aをログアウトしても、Bのstate・Cookieには一切触れない
    auth.clear_auth(state_a)
    auth_cookies.forget_login(manager_factory=lambda: manager_a)
    assert auth.is_authenticated(state_b) is True
    assert auth_cookies.COOKIE_NAME in manager_b.cookies


if __name__ == "__main__":
    sys.exit(run(dict(globals()), "永続ログインCookie（auth_cookies.py）のテスト"))
