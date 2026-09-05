# -*- coding: utf-8 -*-
"""候補保存パネル・マイ候補リスト画面・管理者ビュー（フェーズ3B／3C／3D）

検索結果のサマリー表の直下・「全カラムを表示」の直前に置く、
「マイ候補リストへ保存」パネル（フェーズ3B）、保存した候補を一覧・編集・
アーカイブ・削除できる「マイ候補リスト」画面（フェーズ3C）、そして
管理者だけが全利用者の候補を閲覧専用で見られる「管理者ビュー」（フェーズ3D）。

DBアクセスは candidates.py が行い、ここは Streamlit の画面だけを扱う。
管理者判定はここでは行わず、毎回 candidates.is_admin() のRPC結果だけに従う
（session_state に判定結果を保存して使い回すことはしない）。
"""

import streamlit as st

import auth
import candidates

# session_state キー（他機能と衝突しないよう cand_ で始める。
# チェックボックス等の動的なウィジェットキーも "cand_" で始めており、
# clear_state() はこの接頭辞のキーをまとめて消す）
SAVED_URLS = "cand_saved_urls"        # 今回のセッションで保存に成功したURL
LAST_MESSAGE = "cand_last_message"    # 直近の操作結果メッセージ（表示後に消す）
VIEW = "cand_view"                    # 画面切替（商品をリサーチ／マイ候補リスト／管理者ビュー）
SHOW_ARCHIVED = "cand_show_archived"  # 一覧でアーカイブ済みも表示するか

# 管理者ビュー（フェーズ3D）の絞り込み・ページ状態
ADMIN_FILTER_USER = "cand_admin_filter_user"
ADMIN_FILTER_STATUS = "cand_admin_filter_status"
ADMIN_FILTER_PRIORITY = "cand_admin_filter_priority"
ADMIN_SHOW_ARCHIVED = "cand_admin_show_archived"
ADMIN_PAGE = "cand_admin_page"

VIEW_SEARCH = "search"
VIEW_LIST = "list"
VIEW_ADMIN = "admin"

_VIEW_LABELS = {
    VIEW_SEARCH: "🔍 商品をリサーチ",
    VIEW_LIST: "⭐ マイ候補リスト",
    VIEW_ADMIN: "🛡️ 管理者ビュー",
}

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


def _set_session_for_admin_check(client) -> bool:
    """サイドバーの選択肢を出すかどうかを判定するためだけにJWTを設定する

    auth.apply_session() と違い、失敗しても認証情報（session_state）は
    消さない。ここはサイドバーの表示を決めるだけの下見であり、実際に
    管理者データを取得する各画面（render_admin_screen等）は、そちらで
    改めて auth.apply_session() を呼んで本人のJWTを設定し直す。
    ここで消してしまうと、画面切替を描画するたびに（毎回！）利用者が
    ログアウトさせられかねない。
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


def render_view_switch(client) -> None:
    """サイドバーに画面切替を描画する（ログイン後は常に表示する）

    「🛡️ 管理者ビュー」は is_admin() のRPCが真を返した場合だけ表示する。
    ここでの判定結果は session_state へ保存せず、描画するたびに毎回
    問い合わせ直す（利用者が書き換えられる状態だけに頼らないため）。
    RPCが失敗した場合は安全側に倒し、管理者向けの選択肢を出さない。
    実際に管理者ビューへ入るときは、render_admin_screen が改めて
    is_admin() を確認するため、ここでの判定はあくまで表示の下見。
    """
    init_state(st.session_state)

    is_admin_now = False
    if _set_session_for_admin_check(client):
        is_admin_now = candidates.is_admin(client)

    options = (VIEW_SEARCH, VIEW_LIST, VIEW_ADMIN) if is_admin_now else (VIEW_SEARCH, VIEW_LIST)
    if st.session_state.get(VIEW, VIEW_SEARCH) not in options:
        st.session_state[VIEW] = VIEW_SEARCH

    with st.sidebar:
        st.radio(
            "画面",
            options,
            format_func=lambda v: _VIEW_LABELS.get(v, v),
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


# ── 管理者ビュー（フェーズ3D）────────────────────────────────────────────────
#
# 閲覧専用。ここには更新・削除・アーカイブのボタンや、活動メモ・ステータス・
# 優先度を編集できるウィジェット（text_area/selectbox等の入力欄）を一切置かない
# （tests/test_candidates_ui.py 相当の検査は test_candidates.py 側で行う）。
# 大松さん自身の候補が含まれていても、この画面では他の受講生の行と同じく
# 表示するだけで、編集したい場合は「マイ候補リスト」へ移動する設計とする。

_ADMIN_ALL_LABEL = "すべて"
_ADMIN_ALL_USERS_LABEL = "すべての利用者"


def render_admin_screen(client) -> None:
    """管理者ビューを描画する（全利用者の候補を閲覧専用で表示する）

    is_admin() のRPC結果をここで改めて確認する。サイドバーに選択肢が
    出ていた（=直前の描画では管理者だった）としても、その事実だけを信用
    せず、この画面を実際に描画する直前にもう一度問い合わせ直す。
    """
    init_state(st.session_state)

    st.subheader("🛡️ 管理者ビュー（閲覧専用）")

    message = st.session_state.get(LAST_MESSAGE, "")
    if message:
        st.info(message)
        st.session_state[LAST_MESSAGE] = ""

    if not auth.apply_session(client, st.session_state):
        _flash(st.session_state, auth.SESSION_EXPIRED)
        st.rerun()

    if not candidates.is_admin(client):
        st.error("この画面は管理者専用です。")
        return

    st.caption(
        "全利用者の候補を閲覧専用で表示します。更新・削除・アーカイブはできません。"
        "自分の候補を操作する場合は「マイ候補リスト」を開いてください。"
    )

    profiles = candidates.list_admin_profiles(client)
    label_map = {"": _ADMIN_ALL_USERS_LABEL}
    for uid, p in profiles.items():
        display_name = p.get("display_name") or ""
        email = p.get("email") or ""
        label_map[uid] = f"{display_name}（{email}）" if display_name else email
    user_choices = [""] + sorted(profiles.keys(), key=lambda uid: label_map[uid])
    if st.session_state.get(ADMIN_FILTER_USER, "") not in user_choices:
        st.session_state[ADMIN_FILTER_USER] = ""

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        selected_user = st.selectbox(
            "利用者", user_choices,
            format_func=lambda uid: label_map.get(uid, uid),
            key=ADMIN_FILTER_USER,
        )
    with col2:
        status_choices = ("",) + candidates.STATUS_OPTIONS
        selected_status = st.selectbox(
            "ステータス", status_choices,
            format_func=lambda s: s or _ADMIN_ALL_LABEL,
            key=ADMIN_FILTER_STATUS,
        )
    with col3:
        priority_choices = ("",) + candidates.PRIORITY_OPTIONS
        selected_priority = st.selectbox(
            "本人が設定した優先度", priority_choices,
            format_func=lambda p: p or _ADMIN_ALL_LABEL,
            key=ADMIN_FILTER_PRIORITY,
        )
    with col4:
        show_archived = st.checkbox("アーカイブ済みも表示", key=ADMIN_SHOW_ARCHIVED)

    page_size = candidates.ADMIN_PAGE_SIZE
    page = int(st.session_state.get(ADMIN_PAGE, 0) or 0)

    def _fetch(p: int):
        return candidates.list_admin_saved_items(
            client,
            user_id=selected_user, status=selected_status,
            priority_override=selected_priority, include_archived=show_archived,
            page=p, page_size=page_size,
        )

    items, total = _fetch(page)
    if items is None:
        st.error(candidates.ADMIN_LIST_FAILED)
        return

    max_page = (total - 1) // page_size if total > 0 else 0
    if page > max_page:
        page = max_page
        st.session_state[ADMIN_PAGE] = page
        items, total = _fetch(page)
        if items is None:
            st.error(candidates.ADMIN_LIST_FAILED)
            return

    shown_from = page * page_size + 1 if total else 0
    shown_to = min(total, (page + 1) * page_size)
    st.caption(f"{total}件中 {shown_from}〜{shown_to}件を表示")

    if not items:
        st.info("条件に一致する候補がありません。")
        return

    for item in items:
        badge = _BADGE.get(item.priority_override or item.priority, "")
        title = f"{badge} {item.user_display_name}／{item.name}"
        if item.archived:
            title += "（アーカイブ済み）"
        with st.expander(title):
            st.write(f"利用者: {item.user_display_name}　／　メール: {item.user_email}")
            if item.source_url:
                st.caption(item.source_url)
            st.write(f"プラットフォーム: {item.platform or '不明'}　／　"
                    f"メーカー: {item.maker}")
            st.write(f"元の判定優先度: {item.priority or '未評価'}　／　"
                    f"本人が設定した優先度: {item.priority_override or '未設定'}")
            st.write(f"ステータス: {item.status}")
            st.caption("活動メモ（閲覧専用）")
            st.text(item.memo or "（メモなし）")
            st.caption(f"保存日時: {candidates.format_saved_at_jst(item.saved_at)}")

    col_prev, col_page, col_next = st.columns([1, 2, 1])
    with col_prev:
        if st.button("← 前へ", key="cand_admin_prev_page", disabled=(page <= 0)):
            st.session_state[ADMIN_PAGE] = max(0, page - 1)
            st.rerun()
    with col_page:
        st.write(f"ページ {page + 1} / {max_page + 1}")
    with col_next:
        if st.button("次へ →", key="cand_admin_next_page", disabled=(page >= max_page)):
            st.session_state[ADMIN_PAGE] = min(max_page, page + 1)
            st.rerun()
