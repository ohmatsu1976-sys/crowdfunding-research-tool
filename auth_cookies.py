# -*- coding: utf-8 -*-
"""ブラウザ更新（F5／Ctrl+F5）後もログイン状態を復元するための、暗号化Cookie保存

auth.py はStreamlitに依存しないため、ブラウザCookieの読み書き（Streamlit
コンポーネント）はこのモジュールが担う。保存するのはrefresh tokenだけ
（access tokenは短命で、復元時は常にrefresh_session()で取り直すため不要）。

【暗号化】
Cookieの値そのものは、Streamlit Secrets の AUTH_COOKIE_KEY（Fernetキー）で
暗号化してから保存する。鍵はサーバ側（Secrets）にしかなく、ブラウザには
暗号化済みの値しか渡らない。Fernetは暗号化時刻を内部に埋め込むため、
decrypt時にもttl（MAX_AGE_SECONDS）を渡し、Cookie自体の有効期限とは独立に
「暗号文としての古さ」でも失効させる（Cookie属性の改ざんだけでは延命できない）。

【できないこと（README/報告にも明記）】
extra-streamlit-components の CookieManager は、ブラウザJS経由でCookieを
読み書きするコンポーネントのため、HttpOnly（JSから読めない属性）には
できない。XSSが起きれば暗号化済みの値自体は読めてしまうが、復号鍵は
サーバ側にしかないため、鍵の漏洩が別途無い限り平文のrefresh tokenへは
到達できない。Secure・SameSite=strict・pathは設定できる。

【鍵が無い/不正な場合】
AUTH_COOKIE_KEY が未設定または不正なFernetキーの場合、例外を一切投げず、
永続ログイン（Cookieでの復元・保存）だけを無効化する。通常のメール＋
パスワードログインは影響を受けない。鍵の状態は key_status() で（値を
出さずに）確認できる。

【保存の二段階処理（実ブラウザ特有の非同期処理への対応）】
CookieManagerはStreamlitのカスタムコンポーネントで、Cookieへの実際の
書き込み（ブラウザJSの実行）は、サーバがその指示を送ってから少し遅れて
非同期に行われる。ログイン成功直後にCookie保存を要求して「すぐに」
st.rerunして別の画面へ遷移すると、ブラウザがまだ書き込みJSを実行する
前にコンポーネントが画面から消えてしまい、保存が失われることがある
（ヘッドレスのAppTestでは実JSを実行しないため、この競合は再現しない）。

このため保存は begin_remember_login() → ensure_login_saved() の二段階で
行う。ensure_login_saved() は「保存中です」画面から複数回のrerunに
わたって呼ばれ、(1) 前回までの書き込みが実際にブラウザへ反映された
ことを確認し、(2) まだなら書き込みを（再）要求する。試行回数の上限に
達しても確認できない場合は、永続保存だけを諦めて通常ログインへ進む
（保存の失敗で画面を無限に止めない）。

【起動時の復元中にログイン画面が一瞬出る問題への対応】
起動直後（F5直後）も同じ非同期の事情があり、CookieManagerの値がまだ
届いていない1回目の実行では「Cookieが無い」のか「まだ届いていないだけ」
なのかを区別できない。これを「無い」と即断してログインフォームを出すと、
実際には有効なCookieがある場合でも一瞬ログイン画面が表示されてしまう
（復元自体は直後の自動再実行で成功するため実害は無いが、見た目が悪い）。

このため restore_login_from_cookie() は、Cookieがまだ届いていない間は
RESTORE_DONEを立てずに「確認中」の状態を保つが、無期限には待たない。
実時間での期限（RESTORE_CHECK_MAX_WAIT_SECONDS）と、rerunが極端に速く
繰り返されるような異常系に備えた試行回数の安全弁（
RESTORE_CHECK_HARD_MAX_ATTEMPTS）の両方で区切り、どちらかに達したら
「Cookie無しと確定」してRESTORE_DONEを立てる。st.rerunの間隔は実ブラウザ
との往復にかかる実時間に依存し一定ではないため、試行回数だけで区切ると
遅い応答を早期に諦めてしまう恐れがある（＝時間を主たる基準にし、回数は
あくまで保険）。呼び出し側（auth_ui.py）は is_restore_checking() が真の
間、ログインフォームの代わりに「確認しています」画面を表示してrerunする。
"""

from __future__ import annotations

import time
from typing import Any, Callable, MutableMapping, Optional

import auth

# ── Cookie・Secretsの名前 ───────────────────────────────────────────────────────
COOKIE_NAME = "cfr_auth"
SECRET_NAME = "AUTH_COOKIE_KEY"
MAX_AGE_SECONDS = 30 * 24 * 60 * 60  # 30日

# 「このStreamlitセッションでは既にCookie復元を試みて結果が確定した」ことを示す
# session_stateキー。空のCookie（まだコンポーネントの返信が届いていないだけの
# 可能性がある）では立てず、実際に値を処理した（復元成功／失敗）時だけ立てる。
RESTORE_DONE = "auth_cookie_restore_done"

# 起動時の復元確認（二段階処理）で使うsession_stateキー・上限
RESTORE_DEADLINE = "auth_cookie_restore_deadline"        # 確認を諦める実時刻（time.time()）
RESTORE_CHECK_ATTEMPTS = "auth_cookie_restore_check_attempts"
RESTORE_CHECK_MAX_WAIT_SECONDS = 5.0  # 実時間でこれだけ待っても届かなければ「無し」と確定
RESTORE_CHECK_HARD_MAX_ATTEMPTS = 20  # rerunが異常に速く続く場合の安全弁（時間切れより先には効かない想定）

# ログイン成功直後のCookie保存（二段階処理）で使うsession_stateキー
SAVE_PENDING = "auth_cookie_save_pending"    # 保存を確認し終えるまでTrue
SAVE_ATTEMPTS = "auth_cookie_save_attempts"  # 確認できないまま重ねた試行回数
SAVE_CONFIRM_MAX_ATTEMPTS = 5                # これを超えたら諦めて通常ログインへ進む

STATUS_CONFIRMED = "confirmed"  # ブラウザへの保存を確認できた
STATUS_NO_KEY = "no_key"        # AUTH_COOKIE_KEYが無い／不正（永続保存できない）
STATUS_NO_TOKEN = "no_token"    # session_stateにrefresh tokenが無い（想定外）
STATUS_TIMEOUT = "timeout"      # 試行上限に達しても確認できなかった（諦める）
STATUS_PENDING = "pending"      # まだ確認できていない（呼び出し側はrerunして良い）

# key_status() の戻り値（秘密の値そのものは一切含まない）
KEY_STATUS_MISSING = "missing"
KEY_STATUS_INVALID = "invalid"
KEY_STATUS_CONFIGURED = "configured"


# =============================================================================
# 暗号化（Streamlitに依存しない純粋関数。偽データだけでテストできる）
# =============================================================================

def load_key(secrets: Any) -> Optional[bytes]:
    """AUTH_COOKIE_KEY を読む。無い・空なら None（例外は投げない）"""
    try:
        raw = secrets[SECRET_NAME]
    except Exception:
        return None
    raw = str(raw or "").strip()
    if not raw:
        return None
    return raw.encode("utf-8")


def make_fernet(secrets: Any) -> Optional[Any]:
    """AUTH_COOKIE_KEY から Fernet を作る。無い・不正な鍵なら None（例外は投げない）"""
    key = load_key(secrets)
    if key is None:
        return None
    try:
        from cryptography.fernet import Fernet
        return Fernet(key)
    except Exception:
        return None


def encrypt_refresh_token(fernet: Any, refresh_token: str) -> str:
    """refresh tokenを暗号化し、Cookieへそのまま入れられる文字列にする"""
    return fernet.encrypt(refresh_token.encode("utf-8")).decode("utf-8")


def decrypt_refresh_token(fernet: Any, token: str) -> Optional[str]:
    """Cookieの値からrefresh tokenを復号する。改ざん・鍵不一致・ttl超過なら None

    （例外は一切外へ出さない。呼び出し側はNoneを「この値は使えない」として
    扱い、Cookieを削除する）
    """
    if not token:
        return None
    try:
        raw = fernet.decrypt(token.encode("utf-8"), ttl=MAX_AGE_SECONDS)
        return raw.decode("utf-8")
    except Exception:
        return None


def key_status(secrets: Any = None) -> str:
    """AUTH_COOKIE_KEYの状態を、秘密の値を一切出さずに返す

    KEY_STATUS_MISSING（未設定・空）／KEY_STATUS_INVALID（Fernetキーとして
    不正な形式）／KEY_STATUS_CONFIGURED（認識できている）のいずれか。
    管理者ビューの診断表示にだけ使う想定（本文・ログへは値を出さない）。
    """
    secrets = _default_secrets() if secrets is None else secrets
    if load_key(secrets) is None:
        return KEY_STATUS_MISSING
    if make_fernet(secrets) is None:
        return KEY_STATUS_INVALID
    return KEY_STATUS_CONFIGURED


# =============================================================================
# ブラウザCookieの読み書き（Streamlitコンポーネントに依存）
#
# テスト時は manager_factory / secrets を差し替えられるようにする
# （auth.make_client の factory 引数と同じ考え方。実際のブラウザ・JSは
# 使わず、.get/.set/.delete を持つ偽オブジェクトで検証する）。
# =============================================================================

def _default_manager_factory():
    import extra_streamlit_components as stx
    return stx.CookieManager()


def _default_secrets():
    import streamlit as st
    return st.secrets


def _safe_delete(manager) -> None:
    """Cookieを消す。元のライブラリは未設定のCookie名に対してdeleteすると
    KeyErrorを出す実装のため、ここで必ず握りつぶす（削除自体は失敗しても
    画面を落とさない）。
    """
    try:
        manager.delete(COOKIE_NAME)
    except Exception:
        pass


def _write_cookie(manager, fernet: Any, refresh_token: str) -> None:
    if not refresh_token:
        return
    try:
        encrypted = encrypt_refresh_token(fernet, refresh_token)
        manager.set(
            COOKIE_NAME, encrypted, key="cfr_auth_set",
            path="/", max_age=MAX_AGE_SECONDS, secure=True, same_site="strict",
        )
    except Exception:
        pass  # Cookie保存に失敗しても、ログイン自体は継続させる


def begin_remember_login(state: MutableMapping[str, Any]) -> None:
    """ログイン成功直後に呼ぶ。実際の書き込み・確認は ensure_login_saved() が
    「保存中です」画面から複数回のrerunにわたって行う（モジュールdocstring
    の「保存の二段階処理」を参照）。ここではフラグを立てるだけで、
    Cookie・Secrets・Streamlitコンポーネントには一切触れない。
    """
    state[SAVE_PENDING] = True
    state[SAVE_ATTEMPTS] = 0


def is_save_pending(state: MutableMapping[str, Any]) -> bool:
    """「保存中です」画面をまだ出す必要があるか"""
    return bool(state.get(SAVE_PENDING))


def _finish_save(state: MutableMapping[str, Any], status: str) -> str:
    state[SAVE_PENDING] = False
    state[SAVE_ATTEMPTS] = 0
    return status


def ensure_login_saved(
    state: MutableMapping[str, Any], *,
    secrets: Any = None, manager_factory: Optional[Callable] = None,
) -> str:
    """永続Cookieへの保存を確認し、まだなら（再）要求する

    呼び出し側は、戻り値が STATUS_PENDING の間は画面を切り替えずに
    「保存しています」と表示してから st.rerun() し、もう一度これを呼ぶ。
    それ以外の戻り値が返った時点で保存フェーズは終わり（確認できた／
    鍵が無い／試行上限に達した、のいずれか）、通常の画面へ進んでよい。

    確認は、このmanagerの構築時点（__init__）のgetAll結果、つまり
    「前回以前にブラウザから届いた値」だけを根拠にする。今回この関数内で
    呼ぶset()の戻り値は、ブラウザの確認を経ていないローカルな楽観更新
    （元のCookieManager実装がset()直後にself.cookiesへ即時反映するだけの
    もの）にすぎず、確認の根拠にはならない。
    """
    if not state.get(SAVE_PENDING):
        return STATUS_CONFIRMED

    secrets = _default_secrets() if secrets is None else secrets
    fernet = make_fernet(secrets)
    if fernet is None:
        return _finish_save(state, STATUS_NO_KEY)

    refresh_token = state.get(auth.REFRESH_TOKEN) or ""
    if not refresh_token:
        return _finish_save(state, STATUS_NO_TOKEN)

    manager = (manager_factory or _default_manager_factory)()

    raw = manager.get(COOKIE_NAME)
    if raw and decrypt_refresh_token(fernet, raw) == refresh_token:
        return _finish_save(state, STATUS_CONFIRMED)

    _write_cookie(manager, fernet, refresh_token)

    attempts = int(state.get(SAVE_ATTEMPTS, 0)) + 1
    state[SAVE_ATTEMPTS] = attempts
    if attempts >= SAVE_CONFIRM_MAX_ATTEMPTS:
        return _finish_save(state, STATUS_TIMEOUT)
    return STATUS_PENDING


def forget_login(*, manager_factory: Optional[Callable] = None) -> None:
    """ログアウト・パスワード変更時に呼ぶ。永続Cookieを確実に消す"""
    manager = (manager_factory or _default_manager_factory)()
    _safe_delete(manager)


def is_restore_checking(state: MutableMapping[str, Any]) -> bool:
    """起動時のCookie復元が、まだ「確認中」（結果未確定）かどうか

    呼び出し側（auth_ui.py）はこれが真の間、ログインフォームの代わりに
    「ログイン状態を確認しています…」を表示してrerunする。認証済み、
    または復元結果が確定済み（成功／Cookie無し／無効）になった時点で偽になる。
    """
    if auth.is_authenticated(state):
        return False
    return not state.get(RESTORE_DONE)


def _give_up_restore_check(state: MutableMapping[str, Any], manager) -> None:
    """実時間・試行回数の上限に達した。「Cookie無し」と確定して待つのをやめる

    実際にはCookieが存在するのに応答が遅いだけ、という可能性も残るため
    （＝誤って「無し」と確定しても）、delete自体は失敗しても問題ない安全側の
    操作に留める。
    """
    _safe_delete(manager)
    state[RESTORE_DONE] = True
    state.pop(RESTORE_DEADLINE, None)
    state[RESTORE_CHECK_ATTEMPTS] = 0


def restore_login_from_cookie(
    client, state: MutableMapping[str, Any], *,
    secrets: Any = None, manager_factory: Optional[Callable] = None,
    now: Optional[float] = None,
) -> None:
    """起動時（ルーティングより前）に一度だけ呼ぶ。Cookieからログインを復元する

    すでにログイン済み、またはこのセッションで既に復元結果が確定している
    場合は何もしない（毎回のst.rerunでrefresh_session()を呼び直して
    refresh tokenを浪費・ローテーションさせないため）。

    Cookieがまだコンポーネントから届いていない（空）だけの場合はRESTORE_DONEを
    立てない。Streamlitのカスタムコンポーネントは初回呼び出し時にdefault値
    （空）を返し、実際の値は直後の自動再実行で届く。ここで確定扱いにしてしまうと、
    本当はCookieがあるのに1回目の空値だけを見て復元を諦めてしまう
    （無限ループは起きないが、復元に失敗するバグになる）。

    ただし無期限に待つわけではない。実時間での期限
    （RESTORE_CHECK_MAX_WAIT_SECONDS）、または試行回数の安全弁
    （RESTORE_CHECK_HARD_MAX_ATTEMPTS）のいずれかに達した時点で
    「Cookie無し」と確定する（is_restore_checking()参照）。
    """
    if auth.is_authenticated(state):
        return
    if state.get(RESTORE_DONE):
        return

    now = time.time() if now is None else now
    manager = (manager_factory or _default_manager_factory)()
    raw = manager.get(COOKIE_NAME)
    if not raw:
        deadline = state.get(RESTORE_DEADLINE)
        if deadline is None:
            deadline = now + RESTORE_CHECK_MAX_WAIT_SECONDS
            state[RESTORE_DEADLINE] = deadline
        attempts = int(state.get(RESTORE_CHECK_ATTEMPTS, 0)) + 1
        state[RESTORE_CHECK_ATTEMPTS] = attempts
        if now >= deadline or attempts >= RESTORE_CHECK_HARD_MAX_ATTEMPTS:
            _give_up_restore_check(state, manager)
        return  # 届いていないだけ、またはそもそもCookieが無い（確定前）

    # Cookieが届いた。確認中のための状態は不要になったため消す
    state.pop(RESTORE_DEADLINE, None)
    state[RESTORE_CHECK_ATTEMPTS] = 0

    secrets = _default_secrets() if secrets is None else secrets
    fernet = make_fernet(secrets)
    if fernet is None:
        # 鍵が未設定・不正：この値は今後も使えないため、ここで確定して消す
        _safe_delete(manager)
        state[RESTORE_DONE] = True
        return

    refresh_token = decrypt_refresh_token(fernet, raw)
    if not refresh_token:
        # 改ざん・鍵不一致・ttl超過：安全側に倒し、使えないCookieを残さない
        _safe_delete(manager)
        state[RESTORE_DONE] = True
        return

    ok = auth.restore_session_from_refresh_token(client, state, refresh_token)
    state[RESTORE_DONE] = True
    if ok:
        # refresh tokenはローテーションするため、新しい値でCookieも更新する
        _write_cookie(manager, fernet, state.get(auth.REFRESH_TOKEN) or "")
    else:
        _safe_delete(manager)
