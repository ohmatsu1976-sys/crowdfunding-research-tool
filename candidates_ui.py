# -*- coding: utf-8 -*-
"""候補保存パネル・マイ候補リスト画面（フェーズ3B／3C）

検索結果のサマリー表の直下・「全カラムを表示」の直前に置く、
「マイ候補リストへ保存」パネル（フェーズ3B）と、保存した候補を一覧・編集・
アーカイブ・削除できる「マイ候補リスト」画面（フェーズ3C）。

DBアクセスは candidates.py が行い、ここは Streamlit の画面だけを扱う。
管理者が全受講生の候補を見る画面（フェーズ3D）はまだ実装しない。
"""

import streamlit as st

import auth
import candidates

# session_state キー（他機能と衝突しないよう cand_ で始める。
# チェックボックス等の動的なウィジェットキーも "cand_" で始めており、
# clear_state() はこの接頭辞のキーをまとめて消す）
SAVED_URLS = "cand_saved_urls"        # 今回のセッションで保存に成功したURL
LAST_MESSAGE = "cand_last_message"    # 直近の操作結果メッセージ（表示後に消す）
VIEW = "cand_view"                    # 画面切替（商品をリサーチ／マイ候補リスト）
SHOW_ARCHIVED = "cand_show_archived"  # 一覧でアーカイブ済みも表示するか

VIEW_SEARCH = "search"
VIEW_LIST = "list"

_PREFIX = "cand_"


def init_state(state) -> None:
    """未設定のキーだけ初期化する（既存の保存済み記録は壊さない）"""
    if SAVED_URLS not in state:
        state[SAVED_URLS] = set()
    if LAST_MESSAGE not in state:
        state[LAST_MESSAGE] = ""
    if VIEW not in state:
        state[VIEW] = VIEW_SEARCH


def clear_state(state) -> None:
    """cand_ で始まる session_state キーをすべて消す

    固定キー（SAVED_URLS・LAST_MESSAGE）だけでなく、商品ごとに動的に増える
    チェックボックスのウィジェットキー（cand_check_...）も接頭辞でまとめて
    消す。ログアウト時に呼ぶ。保存済みデータそのものはSupabase側に残る
    （ここで消すのは画面側の一時状態だけ）。
    """
    for key in [k for k in list(state.keys()) if str(k).startswith(_PREFIX)]:
        state.pop(key, None)


def render(client, rows) -> None:
    """保存パネルを描画する

    rows は正規化済みの検索結果（CSV_FIELDS形式の辞書のリスト）。
    未ログイン状態でこの関数が呼ばれることは無い
    （streamlit_app.py で auth_ui.require_login() を通ってからでないと
    この画面自体に到達しない）。
    """
    init_state(st.session_state)
    if not rows:
        return

    st.markdown("**⭐ 候補リストへ保存**")

    message = st.session_state.get(LAST_MESSAGE, "")
    if message:
        st.info(message)
        st.session_state[LAST_MESSAGE] = ""

    st.caption("保存したい商品にチェックを付けて、「マイ候補リストに保存」を押してください。")

    with st.form("cand_save_form", clear_on_submit=False):
        chosen_rows = []
        for i, row in enumerate(rows):
            name = str(row.get("商品名", "") or "（商品名不明）")
            url = str(row.get("掲載URL", "") or "")
            already = url in st.session_state[SAVED_URLS]
            label = ("✓ 保存済み　" if already else "") + name
            checked = st.checkbox(
                label,
                value=False,
                key=f"cand_check_{i}_{url}",
                disabled=already,
            )
            st.caption(url)
            if checked and not already and url:
                chosen_rows.append(row)
        submitted = st.form_submit_button("⭐ マイ候補リストに保存", type="primary")

    if not submitted:
        return

    if not chosen_rows:
        st.warning(candidates.NOTHING_SELECTED)
        return

    if not auth.apply_session(client, st.session_state):
        st.session_state[LAST_MESSAGE] = auth.SESSION_EXPIRED
        st.rerun()

    results = candidates.save_many(client, chosen_rows)
    for r in results:
        if r.ok:
            st.session_state[SAVED_URLS].add(r.url)

    st.session_state[LAST_MESSAGE] = candidates.summary_message(results)
    st.rerun()


# ── 画面切替（フェーズ3C）───────────────────────────────────────────────────────

def get_view(state) -> str:
    """現在の画面（"search" または "list"）。ログイン直後は必ず search"""
    return state.get(VIEW, VIEW_SEARCH)


def render_view_switch() -> None:
    """サイドバーに画面切替を描画する（ログイン後は常に表示する）"""
    init_state(st.session_state)
    with st.sidebar:
        st.radio(
            "画面",
            (VIEW_SEARCH, VIEW_LIST),
            format_func=lambda v: "🔍 商品をリサーチ" if v == VIEW_SEARCH else "⭐ マイ候補リスト",
            key=VIEW,
            label_visibility="collapsed",
        )
        st.divider()


# ── マイ候補リスト画面（フェーズ3C）─────────────────────────────────────────────

_PRIORITY_UNSET_LABEL = "（未設定：元の判定を使う）"
_PRIORITY_CHOICES = (_PRIORITY_UNSET_LABEL,) + candidates.PRIORITY_OPTIONS
_BADGE = {"A": "🟢", "B": "🟡", "C": "🔴"}


def _flash(state, message: str) -> None:
    state[LAST_MESSAGE] = message


def render_list_screen(client) -> None:
    """マイ候補リスト画面を描画する

    ログイン中の本人が保存した候補だけを一覧・編集・アーカイブ・削除できる。
    管理者であっても、この画面には本人の分だけを表示する
    （全受講生分の閲覧はフェーズ3Dの別画面で行う）。
    """
    init_state(st.session_state)

    st.subheader("⭐ マイ候補リスト")

    message = st.session_state.get(LAST_MESSAGE, "")
    if message:
        st.info(message)
        st.session_state[LAST_MESSAGE] = ""

    show_archived = st.checkbox("アーカイブ済みも表示", key=SHOW_ARCHIVED)

    if not auth.apply_session(client, st.session_state):
        _flash(st.session_state, auth.SESSION_EXPIRED)
        st.rerun()

    user_id = st.session_state.get(auth.USER_ID, "")
    items = candidates.list_saved_items(client, user_id, include_archived=show_archived)

    if not items:
        if show_archived:
            st.info("アーカイブ済みの候補もありません。")
        else:
            st.info("まだ候補がありません。「商品をリサーチ」から検索して保存してください。")
        return

    for item in items:
        badge = _BADGE.get(item.priority_override or item.priority, "")
        title = f"{badge} {item.name}"
        if item.archived:
            title += "（アーカイブ済み）"
        with st.expander(title):
            if item.source_url:
                st.caption(item.source_url)
            st.write(f"プラットフォーム: {item.platform or '不明'}　／　"
                    f"メーカー: {item.maker}")
            st.caption(f"元の判定優先度: {item.priority or '未評価'}　／　"
                      f"保存日時: {candidates.format_saved_at_jst(item.saved_at)}")

            memo = st.text_area("活動メモ", value=item.memo,
                               key=f"cand_memo_{item.saved_item_id}")

            status_index = (candidates.STATUS_OPTIONS.index(item.status)
                           if item.status in candidates.STATUS_OPTIONS else 0)
            status = st.selectbox("ステータス", candidates.STATUS_OPTIONS,
                                  index=status_index,
                                  key=f"cand_status_{item.saved_item_id}")

            current_choice = item.priority_override or _PRIORITY_UNSET_LABEL
            priority_index = (_PRIORITY_CHOICES.index(current_choice)
                              if current_choice in _PRIORITY_CHOICES else 0)
            priority_choice = st.selectbox("本人が設定した優先度", _PRIORITY_CHOICES,
                                           index=priority_index,
                                           key=f"cand_priority_{item.saved_item_id}")

            col_update, col_archive, col_delete = st.columns(3)

            with col_update:
                if st.button("更新する", key=f"cand_update_{item.saved_item_id}",
                            width="stretch"):
                    new_priority = (None if priority_choice == _PRIORITY_UNSET_LABEL
                                   else priority_choice)
                    ok = candidates.update_saved_item(
                        client, user_id, item.saved_item_id,
                        memo=memo, status=status, priority_override=new_priority)
                    _flash(st.session_state,
                          candidates.UPDATE_OK if ok else candidates.UPDATE_FAILED)
                    st.rerun()

            with col_archive:
                archive_label = "アーカイブを解除" if item.archived else "アーカイブする"
                if st.button(archive_label, key=f"cand_archive_{item.saved_item_id}",
                            width="stretch"):
                    ok = candidates.update_saved_item(
                        client, user_id, item.saved_item_id, archived=not item.archived)
                    if ok:
                        _flash(st.session_state,
                              candidates.UNARCHIVE_OK if item.archived
                              else candidates.ARCHIVE_OK)
                    else:
                        _flash(st.session_state, candidates.UPDATE_FAILED)
                    st.rerun()

            with col_delete:
                confirm_key = f"cand_delconfirm_{item.saved_item_id}"
                confirmed = st.checkbox(f"「{item.name}」を削除する", key=confirm_key)
                if st.button("削除する", key=f"cand_delete_{item.saved_item_id}",
                            disabled=not confirmed, width="stretch"):
                    ok = candidates.delete_saved_item(client, user_id, item.saved_item_id)
                    _flash(st.session_state,
                          candidates.DELETE_OK if ok else candidates.DELETE_FAILED)
                    st.rerun()
