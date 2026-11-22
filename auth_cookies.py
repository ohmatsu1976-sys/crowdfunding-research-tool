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
パスワードログインは影響を受けない。
"""

from __future__ import annotations

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


def remember_login(
    state: MutableMapping[str, Any], *,
    secrets: Any = None, manager_factory: Optional[Callable] = None,
) -> None:
    """ログイン成功直後に呼ぶ。鍵が無ければ何もしない（通常ログインは継続する）"""
    secrets = _default_secrets() if secrets is None else secrets
    fernet = make_fernet(secrets)
    if fernet is None:
        return
    refresh_token = state.get(auth.REFRESH_TOKEN) or ""
    if not refresh_token:
        return
    manager = (manager_factory or _default_manager_factory)()
    _write_cookie(manager, fernet, refresh_token)


def forget_login(*, manager_factory: Optional[Callable] = None) -> None:
    """ログアウト・パスワード変更時に呼ぶ。永続Cookieを確実に消す"""
    manager = (manager_factory or _default_manager_factory)()
    _safe_delete(manager)


def restore_login_from_cookie(
    client, state: MutableMapping[str, Any], *,
    secrets: Any = None, manager_factory: Optional[Callable] = None,
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
    """
    if auth.is_authenticated(state):
        return
    if state.get(RESTORE_DONE):
        return

    manager = (manager_factory or _default_manager_factory)()
    raw = manager.get(COOKIE_NAME)
    if not raw:
        return  # 届いていないだけ、またはそもそもCookieが無い

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
