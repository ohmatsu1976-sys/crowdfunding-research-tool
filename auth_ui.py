# -*- coding: utf-8 -*-
"""認証まわりの画面（ログイン・初回パスワード変更・ログアウト）

判定や通信は auth.py 側にあり、ここは Streamlit の画面だけを扱う。
未ログインのときはログイン画面を出したうえで st.stop() するため、
検索フォーム・検索結果・AI分析へは到達しない。
"""

import streamlit as st

import auth
import candidates_ui

# ログアウトや変更完了の直後に一度だけ見せる文言（認証情報とは別に持つ）
FLASH = "auth_flash"

_TITLE = "🔍 海外クラファン商品リサーチツール"


def _flash(message: str) -> None:
    st.session_state[FLASH] = message


def _show_flash() -> None:
    message = st.session_state.pop(FLASH, "")
    if message:
        st.success(message)


def _new_client():
    """Supabase クライアントを作る（毎回作り、キャッシュも共有もしない）"""
    try:
        return auth.make_client(st.secrets)
    except auth.ConfigError as e:
        st.title(_TITLE)
        st.error(str(e))
        st.caption("設定は管理者が行います。値そのものは画面には表示されません。")
        st.stop()
    except Exception:
        # 例外の中身に接続情報が含まれうるため、そのままは出さない
        st.title(_TITLE)
        st.error(auth.CONFIG_MISSING)
        st.stop()


def _login_screen(client) -> None:
    st.title(_TITLE)
    st.caption("受講生専用ツールです。管理者から配布されたアカウントでログインしてください。")
    _show_flash()

    with st.form("login_form"):
        email = st.text_input("メールアドレス")
        password = st.text_input("パスワード", type="password")
        submitted = st.form_submit_button("ログイン", type="primary")

    if submitted:
        ok, message = auth.sign_in(client, email, password, st.session_state)
        if ok:
            st.rerun()
        st.error(message)

    st.caption("パスワードを忘れた場合は、管理者に再発行を依頼してください。")
    st.caption("このツールに新規登録はありません。アカウントは管理者が作成します。")
    st.stop()


def _change_password_form(client, key_prefix: str) -> None:
    """パスワード変更フォーム（初回ログイン時と、ログイン後の任意変更で共用）"""
    with st.form(f"{key_prefix}_password_form"):
        new_password = st.text_input("新しいパスワード", type="password")
        confirm = st.text_input("新しいパスワード（確認）", type="password")
        submitted = st.form_submit_button("パスワードを変更する", type="primary")

    if not submitted:
        return

    # 変更は本人の権限で行うため、保持しているトークンをクライアントへ戻す
    if not auth.apply_session(client, st.session_state):
        _flash(auth.SESSION_EXPIRED)
        st.rerun()

    ok, message = auth.change_password(client, st.session_state,
                                       new_password, confirm)
    if ok:
        candidates_ui.clear_state(st.session_state)  # auth側でログアウト済み
        _flash(message)
        st.rerun()
    st.error(message)


def _first_login_screen(client) -> None:
    st.title(_TITLE)
    st.info(
        "初回ログインです。管理者から渡された仮パスワードのままでは利用できません。"
        f"{auth.MIN_PASSWORD_LENGTH}文字以上の新しいパスワードを設定してください。",
        icon="🔑",
    )
    _change_password_form(client, "first")
    if st.button("ログアウト"):
        auth.sign_out(client, st.session_state)
        candidates_ui.clear_state(st.session_state)
        _flash("ログアウトしました。")
        st.rerun()
    st.stop()


def _sidebar(client) -> None:
    with st.sidebar:
        st.markdown(f"**ログイン中**　{auth.current_email(st.session_state)}")
        if st.button("ログアウト", width="stretch"):
            auth.sign_out(client, st.session_state)
            candidates_ui.clear_state(st.session_state)
            _flash("ログアウトしました。")
            st.rerun()
        with st.expander("パスワードを変更する"):
            st.caption(
                f"{auth.MIN_PASSWORD_LENGTH}文字以上。変更すると一度ログアウトします。")
            _change_password_form(client, "sidebar")
        st.divider()


def require_login():
    """ログインしていなければログイン画面だけを表示して停止する

    戻り値は Supabase クライアント。ログイン済みのときだけ処理が先へ進む。
    """
    client = _new_client()

    if not auth.is_authenticated(st.session_state):
        _login_screen(client)

    if auth.must_change_password(st.session_state):
        _first_login_screen(client)

    _sidebar(client)
    _show_flash()
    return client
