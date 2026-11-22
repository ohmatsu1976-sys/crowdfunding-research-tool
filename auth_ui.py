# -*- coding: utf-8 -*-
"""認証まわりの画面（ログイン・初回パスワード変更・ログアウト）

判定や通信は auth.py 側にあり、ここは Streamlit の画面だけを扱う。
未ログインのときはログイン画面を出したうえで st.stop() するため、
検索フォーム・検索結果・AI分析へは到達しない。
"""

import streamlit as st

import auth
import auth_cookies
import candidates_ui
import cases_ui
import kickstarter_discovery_ui
import sender_profiles_ui
import summit_ui

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


def _checking_login_screen() -> None:
    """起動直後、Cookieからの復元結果がまだ確定していない間に表示する画面

    auth_cookies.is_restore_checking() が真の間だけ呼ばれる。ここで
    ログインフォームを出さずに済ませることで、実際には有効なCookieが
    あるのに一瞬ログイン画面が表示されてしまう問題を防ぐ。結果が確定
    （復元成功／Cookie無し・無効の確定）すれば、次のrerunでは呼ばれない。
    """
    st.title(_TITLE)
    st.info("ログイン状態を確認しています…", icon="🔐")
    st.rerun()


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
            # 実際のCookie書き込み・確認は require_login() の「保存中」
            # 画面（_saving_login_screen）が複数回のrerunにわたって行う。
            # ここで書き込みを要求して即rerunすると、実ブラウザでは
            # CookieManagerがまだ書き込みを実行する前に画面が切り替わり、
            # 保存が失われることがあるため。
            auth_cookies.begin_remember_login(st.session_state)
            st.rerun()
        st.error(message)

    st.caption("パスワードを忘れた場合は、管理者に再発行を依頼してください。")
    st.caption("このツールに新規登録はありません。アカウントは管理者が作成します。")
    st.stop()


def _saving_login_screen() -> None:
    """ログイン直後、永続Cookieへの保存が確認できるまで表示する画面

    auth_cookies.ensure_login_saved() が「保存の二段階処理」を一歩進める。
    確認できた・鍵が無い・試行上限に達した、のいずれかで戻り値が
    STATUS_PENDING以外になるまで、通常画面へは進ませない。
    """
    status = auth_cookies.ensure_login_saved(st.session_state)
    if status != auth_cookies.STATUS_PENDING:
        return
    st.title(_TITLE)
    st.info("ログイン状態を保存しています…", icon="💾")
    st.rerun()


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
        cases_ui.clear_state(st.session_state)
        summit_ui.clear_state(st.session_state)
        sender_profiles_ui.clear_state(st.session_state)
        kickstarter_discovery_ui.clear_state(st.session_state)
        auth_cookies.forget_login()  # 古いパスワードの永続Cookieを残さない
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
        cases_ui.clear_state(st.session_state)
        summit_ui.clear_state(st.session_state)
        sender_profiles_ui.clear_state(st.session_state)
        kickstarter_discovery_ui.clear_state(st.session_state)
        auth_cookies.forget_login()
        _flash("ログアウトしました。")
        st.rerun()
    st.stop()


def _sidebar(client) -> None:
    with st.sidebar:
        st.markdown(f"**ログイン中**　{auth.current_email(st.session_state)}")
        if st.button("ログアウト", width="stretch"):
            auth.sign_out(client, st.session_state)
            candidates_ui.clear_state(st.session_state)
            cases_ui.clear_state(st.session_state)
            summit_ui.clear_state(st.session_state)
            sender_profiles_ui.clear_state(st.session_state)
            kickstarter_discovery_ui.clear_state(st.session_state)
            auth_cookies.forget_login()
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

    # ブラウザ更新（F5/Ctrl+F5）で新しいStreamlitセッションになっても、
    # 永続Cookie（暗号化済み）があればログイン状態を復元する。
    # ルーティング（画面切替・検索フォーム等）より前に、ここで一度だけ行う。
    auth_cookies.restore_login_from_cookie(client, st.session_state)

    # 復元結果がまだ確定していない間は、ログインフォームではなく
    # 「確認しています」を見せる（結果が確定するまで一瞬ログイン画面が
    # 出てしまう問題への対応）。
    if auth_cookies.is_restore_checking(st.session_state):
        _checking_login_screen()

    if not auth.is_authenticated(st.session_state):
        _login_screen(client)

    if auth_cookies.is_save_pending(st.session_state):
        _saving_login_screen()

    if auth.must_change_password(st.session_state):
        _first_login_screen(client)

    _sidebar(client)
    _show_flash()
    return client
