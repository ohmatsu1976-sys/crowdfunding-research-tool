# -*- coding: utf-8 -*-
"""メール＋パスワード認証（Supabase Auth）

受講生ごとにアカウントを分けるための認証。Streamlit に依存しないため、
session_state の代わりにただの dict を渡して外部通信なしでテストできる。

このモジュールの約束事:

- 使う秘密情報は SUPABASE_URL と SUPABASE_PUBLISHABLE_KEY だけ。
  旧 anon key へのフォールバックは作らない（動かなければ設定不足として止める）。
- service_role / secret key は一切使わない。
- Supabase クライアントをモジュール変数や st.cache_resource に置かない。
  クライアントは呼ばれるたびに作り、利用者間で共有しない。
- アクセストークン・リフレッシュトークンは session_state の中だけに置き、
  画面・ログ・例外メッセージへ出さない。
- 認証の失敗理由は区別せず、常に同じ文言を返す
  （利用者が存在するかどうかを推測させない）。

管理者が Supabase 管理画面でユーザーを作り、仮パスワードを個別に渡す運用。
アプリ側に新規登録・招待メール・パスワード再設定メールは置かない。
"""

from typing import Any, Callable, MutableMapping, Optional, Tuple

import search_state as sstate

# ── Secrets のキー名 ──────────────────────────────────────────────────────────
# 新形式のキー名のみを使う。SUPABASE_ANON_KEY は参照しない
URL_SECRET = "SUPABASE_URL"
KEY_SECRET = "SUPABASE_PUBLISHABLE_KEY"

# ── session_state のキー（他機能と衝突しないよう auth_ で始める）──────────────
USER_ID = "auth_user_id"
EMAIL = "auth_email"
ACCESS_TOKEN = "auth_access_token"
REFRESH_TOKEN = "auth_refresh_token"
MUST_CHANGE = "auth_must_change_password"

KEYS = (USER_ID, EMAIL, ACCESS_TOKEN, REFRESH_TOKEN, MUST_CHANGE)
# 画面やログへ出してはいけない session_state のキー
NEVER_DISPLAY = (ACCESS_TOKEN, REFRESH_TOKEN)

# 初回ログインかどうかの判定にだけ使う。管理者権限の判定には使わない
PASSWORD_CHANGED_FLAG = "password_changed"

MIN_PASSWORD_LENGTH = 10

# ── 利用者へ見せる文言（例外の中身は出さない）────────────────────────────────
LOGIN_FAILED = (
    "メールアドレスまたはパスワードが正しくありません。"
    "パスワードを忘れた場合は、管理者に再発行を依頼してください。"
)
CONFIG_MISSING = (
    "認証の設定が不足しています。アプリの Secrets に "
    f"{URL_SECRET} と {KEY_SECRET} を設定してください。"
)
SESSION_EXPIRED = "ログインの有効期限が切れました。もう一度ログインしてください。"
PASSWORD_TOO_SHORT = f"パスワードは{MIN_PASSWORD_LENGTH}文字以上にしてください。"
PASSWORD_MISMATCH = "確認用のパスワードが一致しません。"
PASSWORD_CHANGE_FAILED = "パスワードを変更できませんでした。もう一度お試しください。"
PASSWORD_CHANGED = (
    "パスワードを変更しました。安全のため一度ログアウトします。"
    "新しいパスワードでログインし直してください。"
)


class ConfigError(RuntimeError):
    """Secrets が足りないなど、利用者では直せない設定の問題"""


# ── Secrets とクライアント ────────────────────────────────────────────────────

def read_secrets(secrets: Any) -> Tuple[str, str]:
    """SUPABASE_URL と SUPABASE_PUBLISHABLE_KEY を読む

    旧 anon key への切り替えは行わない。値そのものは戻り値以外に出さない。
    """
    def _get(name: str) -> str:
        try:
            value = secrets[name]
        except Exception:
            return ""
        return str(value or "").strip()

    url, key = _get(URL_SECRET), _get(KEY_SECRET)
    if not url or not key:
        raise ConfigError(CONFIG_MISSING)
    return url, key


def make_client(secrets: Any, factory: Optional[Callable] = None):
    """Supabase クライアントを新しく作る

    呼ばれるたびに作る。モジュール変数にも st.cache_resource にも置かないため、
    利用者間で認証状態やトークンが共有されない。
    """
    url, key = read_secrets(secrets)
    if factory is None:
        from supabase import create_client  # 起動を軽くするため遅延import
        factory = create_client
    return factory(url, key)


def apply_session(client, state: MutableMapping[str, Any]) -> bool:
    """保持しているトークンをクライアントへ復元する

    以後の DB アクセスが本人の JWT で行われ、RLS が本人の行だけに効くようにする。
    復元できなければ安全側に倒してログアウト扱いにする。
    """
    access = state.get(ACCESS_TOKEN) or ""
    refresh = state.get(REFRESH_TOKEN) or ""
    if not access or not refresh:
        clear_auth(state)
        return False
    try:
        client.auth.set_session(access, refresh)
        return True
    except Exception:
        # 例外の中身にはトークンが含まれうるため、一切外へ出さない
        clear_auth(state)
        return False


# ── 認証状態 ──────────────────────────────────────────────────────────────────

def clear_auth(state: MutableMapping[str, Any]) -> None:
    """認証情報だけを session_state から消す"""
    for key in KEYS:
        state.pop(key, None)


def is_authenticated(state: MutableMapping[str, Any]) -> bool:
    return bool(state.get(USER_ID)) and bool(state.get(ACCESS_TOKEN))


def must_change_password(state: MutableMapping[str, Any]) -> bool:
    """初回ログイン（管理者が作った仮パスワードのまま）かどうか"""
    return is_authenticated(state) and bool(state.get(MUST_CHANGE))


def current_email(state: MutableMapping[str, Any]) -> str:
    return str(state.get(EMAIL) or "")


def _needs_password_change(user) -> bool:
    """user_metadata に password_changed が無ければ初回ログイン扱い"""
    metadata = getattr(user, "user_metadata", None) or {}
    if not isinstance(metadata, dict):
        return True
    return not bool(metadata.get(PASSWORD_CHANGED_FLAG))


def _store(state: MutableMapping[str, Any], session, user) -> bool:
    access = getattr(session, "access_token", "") or ""
    refresh = getattr(session, "refresh_token", "") or ""
    user_id = getattr(user, "id", "") or ""
    if not access or not refresh or not user_id:
        clear_auth(state)
        return False
    state[USER_ID] = user_id
    state[EMAIL] = getattr(user, "email", "") or ""
    state[ACCESS_TOKEN] = access
    state[REFRESH_TOKEN] = refresh
    state[MUST_CHANGE] = _needs_password_change(user)
    return True


# ── ログイン・ログアウト ──────────────────────────────────────────────────────

def sign_in(client, email: str, password: str,
            state: MutableMapping[str, Any]) -> Tuple[bool, str]:
    """メールアドレスとパスワードでログインする

    失敗の理由（未登録・パスワード違い・メール未確認）は区別せず、
    常に同じ文言を返す。失敗時は認証情報を残さない。
    """
    email = (email or "").strip()
    password = password or ""
    if not email or not password:
        clear_auth(state)
        return False, LOGIN_FAILED
    try:
        result = client.auth.sign_in_with_password(
            {"email": email, "password": password})
    except Exception:
        # 例外文には認証情報が含まれうるため、利用者へは出さない
        clear_auth(state)
        return False, LOGIN_FAILED
    session = getattr(result, "session", None)
    user = getattr(result, "user", None)
    if session is None or user is None or not _store(state, session, user):
        clear_auth(state)
        return False, LOGIN_FAILED
    return True, ""


def sign_out(client, state: MutableMapping[str, Any]) -> None:
    """ログアウトする

    Supabase 側のサインアウトが失敗しても、手元の認証情報と検索状態は必ず消す。
    """
    if client is not None:
        try:
            client.auth.sign_out()
        except Exception:
            pass                      # 失敗しても手元は必ず消す
    clear_auth(state)
    sstate.clear_results(state)       # 検索結果・検索条件・エラーも残さない


# ── パスワード変更 ────────────────────────────────────────────────────────────

def validate_password(new_password: str, confirm_password: str) -> str:
    """アプリ側の検証。問題があればその文言、無ければ空文字を返す"""
    new_password = new_password or ""
    if len(new_password) < MIN_PASSWORD_LENGTH:
        return PASSWORD_TOO_SHORT
    if new_password != (confirm_password or ""):
        return PASSWORD_MISMATCH
    return ""


def change_password(client, state: MutableMapping[str, Any],
                    new_password: str, confirm_password: str) -> Tuple[bool, str]:
    """パスワードを変更し、変更できたら必ずログアウトする

    user_metadata の password_changed は、次回から初回ログイン画面を出さない
    ためだけに使う（管理者権限の判定には使わない）。
    """
    problem = validate_password(new_password, confirm_password)
    if problem:
        return False, problem
    if not is_authenticated(state):
        return False, SESSION_EXPIRED
    try:
        client.auth.update_user({
            "password": new_password,
            "data": {PASSWORD_CHANGED_FLAG: True},
        })
    except Exception:
        return False, PASSWORD_CHANGE_FAILED
    # 新しいパスワードで入り直してもらう（古いトークンを残さない）
    sign_out(client, state)
    return True, PASSWORD_CHANGED
