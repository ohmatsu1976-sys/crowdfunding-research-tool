# -*- coding: utf-8 -*-
"""送信者プロフィール選択・管理画面

従来の「氏名（英語表記）」「会社名・屋号（任意）」の手入力欄を置き換える。
利用者ごとに複数の送信者プロフィールをSupabaseへ保存し、プルダウンで選んで
営業メール生成へ渡す。DBアクセスは sender_profiles.py が行い、ここは
Streamlit の画面だけを扱う。

student・admin共通の画面。サミット特典版（summit_ui.py）には元々この
氏名・会社名入力が無いため、ここは追加しない。
"""

from __future__ import annotations

from typing import Optional, Tuple

import streamlit as st

import auth
import sender_profiles as sp

# session_state キー（他機能と衝突しないよう sprof_ で始める。
# 動的なウィジェットキーも "sprof_" で始めており、clear_state() は
# この接頭辞のキーをまとめて消す）
SELECTED_ID = "sprof_selected_id"      # 選択中のプロフィールID（""=未選択）
# st.selectbox(key=SELECTED_ID)が描画された後（同じ実行内）にSELECTED_IDへ
# 直接代入すると、Streamlitが「instantiated後のウィジェットキー変更」として
# 例外にする。新規登録・削除の成功時はこの「予約枠」に希望値を置き、次の
# 実行の冒頭（ウィジェット描画より前）で反映する。
_PENDING_SELECTED_ID = "sprof_pending_selected_id"
LAST_MESSAGE = "sprof_last_message"    # 直近の操作結果メッセージ（表示後に消す）
NEW_NAME = "sprof_new_name"
NEW_COMPANY = "sprof_new_company"
NEW_IS_DEFAULT = "sprof_new_is_default"

_PREFIX = "sprof_"

UNREGISTERED_NOTICE = (
    "送信者プロフィールが未登録です。下の「プロフィールを管理」から、"
    "営業メールに使う氏名（英語表記）を登録してください。"
)
UNSELECTED_NOTICE = (
    "送信者プロフィールが選択されていません。上のプルダウンから選ぶか、"
    "新規登録してください。選択するまで「▶ リサーチ開始」は押せません。"
)
ENGLISH_NOTICE = "海外メーカーへ送るメールに使用するため、英語表記で入力してください。"


def clear_state(state) -> None:
    """sprof_ で始まる session_state キーをすべて消す（ログアウト時に呼ぶ）

    保存済みのプロフィールそのものはSupabase側に残る
    （ここで消すのは画面側の一時状態だけ）。同じブラウザで別の利用者が
    ログインしても、前の利用者の選択・入力中の値が一瞬でも出ないようにする。
    """
    for key in [k for k in list(state.keys()) if str(k).startswith(_PREFIX)]:
        state.pop(key, None)


def _flash(state, message: str) -> None:
    state[LAST_MESSAGE] = message


def _show_flash() -> None:
    message = st.session_state.get(LAST_MESSAGE, "")
    if message:
        st.info(message)
        st.session_state[LAST_MESSAGE] = ""


def _set_session_quietly(client) -> bool:
    """この画面を描画するためだけにJWTを設定する。失敗しても認証情報は消さない

    auth.apply_session() と違い、ここでの失敗だけで利用者をログアウトさせない
    （この画面は常時描画されるため、auth.apply_session()を使うと、画面切替の
    下見と同じ理由で、描画するたびに利用者がログアウトしかねない）。
    実際の登録・更新・削除時は、呼び出し先のsender_profiles.py側の処理が
    このクライアントの状態をそのまま使う。
    """
    access = st.session_state.get(auth.ACCESS_TOKEN) or ""
    refresh = st.session_state.get(auth.REFRESH_TOKEN) or ""
    if not access or not refresh:
        return False
    try:
        client.auth.set_session(access, refresh)
        return True
    except Exception:
        return False


def render_selector(client) -> Tuple[Optional[str], Optional[str]]:
    """送信者プロフィールの選択・管理UIを描画する

    戻り値は選択中プロフィールの (氏名, 会社名)。未選択・取得失敗時は
    (None, None)。呼び出し側（streamlit_app.py）はこれを使って
    「▶ リサーチ開始」ボタンの有効・無効を切り替える。
    """
    # 前回の操作（新規登録・削除）で予約された選択を、ウィジェット描画より前に反映する
    if _PENDING_SELECTED_ID in st.session_state:
        st.session_state[SELECTED_ID] = st.session_state.pop(_PENDING_SELECTED_ID)

    with st.expander("📝 送信者プロフィール", expanded=True):
        st.caption(ENGLISH_NOTICE)
        _show_flash()

        if not _set_session_quietly(client):
            st.error(sp.LIST_FAILED)
            return None, None

        user_id = st.session_state.get(auth.USER_ID, "")
        profiles = sp.list_profiles(client, user_id)

        if profiles is None:
            st.error(sp.LIST_FAILED)
            return None, None

        if not profiles:
            # 選べるものが無いため、残っていれば古い選択状態を消す
            # （_render_dropdown はここでは呼ばないため、代わりにここで消す）
            st.session_state[SELECTED_ID] = ""
            st.info(UNREGISTERED_NOTICE)
            selected = None
        else:
            selected = _render_dropdown(profiles)
            if selected is None:
                st.info(UNSELECTED_NOTICE)

        with st.expander("✏️ プロフィールを管理（追加・編集・削除）",
                         expanded=not profiles):
            _render_create_form(client, user_id)
            if profiles:
                st.divider()
                _render_edit_delete(client, user_id, profiles)

        if selected is None:
            return None, None
        return selected.sender_name_en, selected.company_name_en


def _render_dropdown(profiles) -> Optional["sp.Profile"]:
    """プルダウンを描画し、選択中のProfileオブジェクト（未選択ならNone）を返す"""
    state = st.session_state
    by_id = {p.id: p for p in profiles}
    option_ids = [""] + list(by_id.keys())

    # 自動選択をやり直すのは「未初期化」または「選択中のIDがもう存在しない
    # （削除された等）」場合だけ。""（本人が明示的に選んだ「未選択」）は
    # 常にoption_idsに含まれるため、ここでは再選択されない
    # （本人が未選択に戻した状態を、再描画のたびに勝手に上書きしない）。
    if SELECTED_ID not in state or state.get(SELECTED_ID, "") not in option_ids:
        auto = sp.pick_auto_selected(profiles)
        state[SELECTED_ID] = auto.id if auto else ""

    label_map = {"": "選択してください"}
    label_map.update({pid: p.label for pid, p in by_id.items()})

    st.selectbox(
        "送信者プロフィール",
        option_ids,
        format_func=lambda pid: label_map.get(pid, pid),
        key=SELECTED_ID,
    )
    return by_id.get(state.get(SELECTED_ID, ""))


def _render_create_form(client, user_id: str) -> None:
    st.markdown("**新規登録**")
    name = st.text_input("氏名（英語表記）", key=NEW_NAME,
                         placeholder="例：Hirohito Ohmatsu")
    company = st.text_input("会社名・屋号（英語表記）", key=NEW_COMPANY,
                            placeholder="例：Base on Base LLC（空欄でもOK）")
    is_default = st.checkbox("既定のプロフィールにする", key=NEW_IS_DEFAULT)
    warning = sp.japanese_warning(name, company)
    if warning:
        st.warning(warning)

    if st.button("プロフィールを登録する", key="sprof_create_btn", type="primary"):
        profile_id, error = sp.create_profile(client, user_id, name, company,
                                              is_default=is_default)
        if error:
            _flash(st.session_state, error)
        else:
            st.session_state[_PENDING_SELECTED_ID] = profile_id
            st.session_state.pop(NEW_NAME, None)
            st.session_state.pop(NEW_COMPANY, None)
            st.session_state.pop(NEW_IS_DEFAULT, None)
            _flash(st.session_state, sp.CREATE_OK)
        st.rerun()


def _render_edit_delete(client, user_id: str, profiles) -> None:
    st.markdown("**既存プロフィールの編集・削除**")
    for p in profiles:
        name_key = f"sprof_edit_name_{p.id}"
        company_key = f"sprof_edit_company_{p.id}"
        default_key = f"sprof_edit_default_{p.id}"
        confirm_key = f"sprof_delconfirm_{p.id}"

        with st.expander(p.label):
            name = st.text_input("氏名（英語表記）", value=p.sender_name_en, key=name_key)
            company = st.text_input("会社名・屋号（英語表記）",
                                    value=p.company_name_en, key=company_key)
            is_default = st.checkbox("既定のプロフィールにする",
                                     value=p.is_default, key=default_key)
            warning = sp.japanese_warning(name, company)
            if warning:
                st.warning(warning)

            col_update, col_delete = st.columns(2)
            with col_update:
                if st.button("更新する", key=f"sprof_update_{p.id}", width="stretch"):
                    error = sp.update_profile(client, user_id, p.id, name, company,
                                              is_default=is_default)
                    _flash(st.session_state, error or sp.UPDATE_OK)
                    st.rerun()

            with col_delete:
                confirmed = st.checkbox(f"「{p.label}」を削除する", key=confirm_key)
                if st.button("削除する", key=f"sprof_delete_{p.id}",
                            disabled=not confirmed, width="stretch"):
                    ok = sp.delete_profile(client, user_id, p.id)
                    if ok:
                        # 選択中のIDを明示的に書き換える必要はない。削除後は
                        # option_idsからそのIDが消えるため、次の実行で
                        # _render_dropdown側が自動的に選び直す（1件だけ残れば
                        # それを、2件以上かつ既定/使用履歴が無ければ未選択に）。
                        _flash(st.session_state, sp.DELETE_OK)
                    else:
                        _flash(st.session_state, sp.DELETE_FAILED)
                    st.rerun()
