# -*- coding: utf-8 -*-
"""候補保存パネル（フェーズ3B）

検索結果のサマリー表の直下・「全カラムを表示」の直前に置く、
「マイ候補リストへ保存」パネル。保存処理そのものは candidates.py が行う。

候補一覧画面・メモ／ステータス編集・削除・管理者画面はフェーズ3Bの範囲外
（まだ実装しない）。
"""

import streamlit as st

import auth
import candidates

# 候補保存まわりの session_state キー（他機能と衝突しないよう cand_ で始める。
# チェックボックスのウィジェットキーも "cand_check_" で始めており、
# clear_state() はこの接頭辞のキーをまとめて消す）
SAVED_URLS = "cand_saved_urls"      # 今回のセッションで保存に成功したURL
LAST_MESSAGE = "cand_last_message"  # 直近の保存結果メッセージ（表示後に消す）

_PREFIX = "cand_"


def init_state(state) -> None:
    """未設定のキーだけ初期化する（既存の保存済み記録は壊さない）"""
    if SAVED_URLS not in state:
        state[SAVED_URLS] = set()
    if LAST_MESSAGE not in state:
        state[LAST_MESSAGE] = ""


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
