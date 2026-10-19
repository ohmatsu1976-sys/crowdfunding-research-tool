# -*- coding: utf-8 -*-
"""「商品を探す」画面（日本語キーワード→Kickstarter商品候補探索）

ログイン済みの通常版利用者（student・admin）にだけ表示する。サミット特典版
には一切影響しない。DBへの保存は行わず、結果は session_state にのみ保持する。

ロジック（Web Search/Web Fetch呼び出し・URL抽出・状態判定・計算・JPY換算）は
kickstarter_discovery.py が持つ。ここは Streamlit の画面と session_state
管理だけを扱う。
"""

from __future__ import annotations

from typing import List, Optional

import streamlit as st

import candidates_ui
import kickstarter_discovery as kd

# session_state キー（他機能と衝突しないよう kd_ で始める）
KEYWORD_INPUT = "kd_keyword_input"
STATUS_LIVE_CB = "kd_status_live_cb"
STATUS_LATE_CB = "kd_status_late_pledge_cb"
STATUS_ENDED_CB = "kd_status_ended_cb"
RESULT = "kd_result"
LAST_KEYWORD = "kd_last_keyword"
LAST_STATUSES = "kd_last_statuses"
TOP_ERROR = "kd_top_error"
PENDING_HANDOFF_URL = "kd_pending_handoff_url"   # streamlit_app.py が読んで検索欄へ渡す

_PREFIX = "kd_"

_STATUS_CHECKBOX_KEYS = {
    kd.STATUS_LIVE: STATUS_LIVE_CB,
    kd.STATUS_LATE_PLEDGE: STATUS_LATE_CB,
    kd.STATUS_ENDED: STATUS_ENDED_CB,
}

_KEYWORD_EXAMPLES = [
    "アウトドアで使える調理器具",
    "持ち運べる小型家電",
    "収納に便利な旅行用品",
    "睡眠の悩みを解決する商品",
]


def init_state(state) -> None:
    for key in (STATUS_LIVE_CB, STATUS_LATE_CB, STATUS_ENDED_CB):
        if key not in state:
            state[key] = True
    if RESULT not in state:
        state[RESULT] = None
    if TOP_ERROR not in state:
        state[TOP_ERROR] = ""


def clear_state(state) -> None:
    """kd_ で始まる session_state キーをすべて消す（ログアウト・パスワード変更時）"""
    for key in [k for k in list(state.keys()) if str(k).startswith(_PREFIX)]:
        state.pop(key, None)


def _selected_statuses(state) -> List[str]:
    selected = []
    for status, key in _STATUS_CHECKBOX_KEYS.items():
        if state.get(key, True):
            selected.append(status)
    return selected


def _make_client():
    """Anthropic クライアントを作る。失敗時は (None, エラー文言)"""
    try:
        api_key = st.secrets["ANTHROPIC_API_KEY"]
    except (KeyError, FileNotFoundError):
        return None, ("APIキーが設定されていません。管理者に問い合わせるか、"
                      ".streamlit/secrets.toml に ANTHROPIC_API_KEY を設定してください。")
    try:
        import anthropic as _anthropic
    except ImportError:
        return None, "anthropic パッケージがインストールされていません: pip install anthropic"
    try:
        client = _anthropic.Anthropic(api_key=api_key)
    except Exception as e:
        return None, f"Claude API クライアントの作成に失敗しました: {type(e).__name__}"
    return client, ""


def _format_amount(amount: Optional[float], currency: str) -> str:
    if amount is None:
        return "未取得"
    code = currency or ""
    try:
        return f"{code} {amount:,.0f}".strip()
    except (TypeError, ValueError):
        return "未取得"


def _format_jpy(p: "kd.Product") -> str:
    if p.raised_amount_jpy is None:
        return f"未取得（{p.jpy_unavailable_reason}）" if p.jpy_unavailable_reason else "未取得"
    return f"約{p.raised_amount_jpy:,.0f}円"


def _format_days(p: "kd.Product") -> str:
    if p.status == kd.STATUS_LIVE and p.days_remaining is not None:
        return f"残り{p.days_remaining}日"
    if p.days_elapsed is not None:
        return f"終了から{p.days_elapsed}日"
    return "不明"


def _badge(status: str) -> str:
    return {
        kd.STATUS_LIVE: "🟢", kd.STATUS_LATE_PLEDGE: "🟡",
        kd.STATUS_ENDED: "⚪", kd.STATUS_UNKNOWN: "❓", kd.STATUS_MISMATCH: "⚠️",
    }.get(status, "")


def _render_product_card(p: "kd.Product", idx: int) -> None:
    badge = _badge(p.status)
    label_status = kd.STATUS_LABELS_JA.get(p.status, p.status)
    with st.container(border=True):
        cols = st.columns([1, 3])
        with cols[0]:
            if p.image_url:
                try:
                    st.image(p.image_url, width="stretch")
                except Exception:
                    pass
        with cols[1]:
            st.markdown(f"**{badge} {p.name or '（商品名未取得）'}**　{label_status}")
            st.caption(p.summary_ja or "（商品概要は取得できませんでした）")
            st.write(
                f"支援総額: {_format_amount(p.raised_amount, p.currency)}　／　"
                f"日本円概算: {_format_jpy(p)}"
            )
            st.write(
                f"目標額: {_format_amount(p.goal_amount, p.currency)}　／　"
                f"達成率: {f'{p.percent_funded:.0f}%' if p.percent_funded is not None else '未取得'}　／　"
                f"支援者数: {p.backers if p.backers is not None else '未取得'}人"
            )
            st.write(f"{_format_days(p)}　／　メーカー: {p.maker or '不明'}　／　国: {p.country or '不明'}")
            st.caption(f"取得日時: {p.retrieved_at or '不明'}　／　URL: {p.url}")
            if p.status == kd.STATUS_MISMATCH and p.status_mismatch_reason:
                st.warning(p.status_mismatch_reason)

            btn_col1, btn_col2 = st.columns(2)
            with btn_col1:
                if st.button("🔍 この商品を詳しくリサーチ", key=f"kd_use_{idx}_{p.url}"):
                    st.session_state[PENDING_HANDOFF_URL] = p.url
                    st.session_state[candidates_ui.PENDING_VIEW] = candidates_ui.VIEW_SEARCH
                    st.rerun()
            with btn_col2:
                st.link_button("🌐 Kickstarterで確認", p.url, width="stretch")


def _render_result(result: "kd.DiscoveryResult", selected_statuses: List[str]) -> None:
    any_shown = False
    for status in (kd.STATUS_LIVE, kd.STATUS_LATE_PLEDGE, kd.STATUS_ENDED):
        if status not in selected_statuses:
            continue
        items = result.by_status.get(status, [])
        st.markdown(f"#### {_badge(status)} {kd.STATUS_LABELS_JA[status]}（{len(items)}件）")
        if not items:
            if status == kd.STATUS_LIVE:
                st.info(
                    "今回の検索では、条件に合うライブ商品を確認できませんでした。"
                    "キーワードを変えるか、終了済みも検索対象に加えてお試しください。"
                )
            else:
                st.caption("該当する候補はありませんでした。")
        for i, p in enumerate(items):
            _render_product_card(p, idx=f"{status}_{i}")
            any_shown = True

    if result.unknown_status:
        st.markdown(f"#### ❓ 状態を確認できなかった候補（{len(result.unknown_status)}件）")
        for i, p in enumerate(result.unknown_status):
            _render_product_card(p, idx=f"unknown_{i}")
            any_shown = True

    if result.mismatch:
        with st.expander(f"⚠️ 終了日時と表示文言が矛盾した候補（{len(result.mismatch)}件）"):
            for i, p in enumerate(result.mismatch):
                _render_product_card(p, idx=f"mismatch_{i}")

    if result.off_selection:
        with st.expander(
            f"検索では見つかりましたが、選択したステータスと一致しなかった候補"
            f"（{len(result.off_selection)}件）"
        ):
            for i, p in enumerate(result.off_selection):
                _render_product_card(p, idx=f"off_{i}")

    with st.expander("取得できなかった・除外した候補の詳細", expanded=False):
        if result.fetch_failed:
            st.markdown("**個別ページの取得に失敗した候補**")
            for e in result.fetch_failed:
                st.caption(f"- {e['url']}（{e['reason']}）")
        if result.excluded_non_physical:
            st.markdown("**物理商品でないため除外した候補**")
            for e in result.excluded_non_physical:
                st.caption(f"- {e['url']}（{e['reason']}）")
        if result.excluded_duplicate:
            st.markdown("**重複のため除外したURL**")
            for e in result.excluded_duplicate:
                st.caption(f"- {e['url']}")
        if result.excluded_invalid_url:
            st.markdown("**個別プロジェクトページと確認できなかったURL**")
            for e in result.excluded_invalid_url:
                st.caption(f"- {e['url']}（{e['reason']}）")
        if result.candidates_dropped_by_cap:
            st.markdown("**件数上限のため確認しなかった候補**")
            for e in result.candidates_dropped_by_cap:
                st.caption(f"- {e['url']}（{e['reason']}）")
        if not any([result.fetch_failed, result.excluded_non_physical,
                   result.excluded_duplicate, result.excluded_invalid_url,
                   result.candidates_dropped_by_cap]):
            st.caption("該当するものはありませんでした。")

    if result.search_errors:
        for msg in result.search_errors:
            st.warning(f"検索中にエラーが発生しました: {msg}")

    if not any_shown and not result.unknown_status:
        st.info(
            "今回の検索では候補を確認できませんでした。"
            "これはKickstarter上に該当商品が存在しないという意味ではありません。"
            "キーワードや選択ステータスを変えて再度お試しください。"
        )

    with st.expander("処理コスト・時間（概算）", expanded=False):
        u = result.usage or {}
        st.caption(
            f"Web Search呼び出し: {u.get('search_calls', 0)}回　／　"
            f"Web Fetch呼び出し: {u.get('fetch_calls', 0)}回　／　"
            f"入力トークン: {u.get('input_tokens', 0):,}　／　"
            f"出力トークン: {u.get('output_tokens', 0):,}　／　"
            f"検索処理時間: 約{u.get('search_elapsed_sec', 0)}秒　／　"
            f"取得処理時間: 約{u.get('fetch_elapsed_sec', 0)}秒"
        )
        if result.jpy_rate_date:
            st.caption(f"為替レート基準日（ECB）: {result.jpy_rate_date}")
        if result.jpy_rate_error:
            st.caption(f"為替レート取得エラー: {result.jpy_rate_error}（日本円概算は未取得として表示しています）")


def render_screen(client) -> None:
    """「商品を探す」画面を描画する（streamlit_app.py から呼ぶ）"""
    init_state(st.session_state)

    st.subheader("🔎 商品を探す")
    st.info(
        "検索条件に合う候補をAIが探して表示します。"
        "Kickstarter上のすべての商品を網羅する検索ではありません。",
        icon="ℹ️",
    )
    st.caption(
        "終了済みの商品も重要な候補です。過去に大きな実績を持つメーカーの"
        "新商品・改良版・日本未展開商品へ交渉を広げられる可能性があるため、"
        "古いという理由だけでは除外していません。"
    )

    err = st.session_state.get(TOP_ERROR, "")
    if err:
        st.error(err)

    with st.form("kd_search_form"):
        st.text_input(
            "日本語キーワード",
            key=KEYWORD_INPUT,
            placeholder="例：" + " / ".join(_KEYWORD_EXAMPLES),
            help="最大200文字。" + " ".join(f"例：{e}" for e in _KEYWORD_EXAMPLES),
        )
        st.markdown("**検索対象ステータス**")
        c1, c2, c3 = st.columns(3)
        with c1:
            st.checkbox("ライブ", key=STATUS_LIVE_CB)
        with c2:
            st.checkbox("遅延プレッジ", key=STATUS_LATE_CB)
        with c3:
            st.checkbox("終了済み", key=STATUS_ENDED_CB)
        st.caption("「今後登場予定」は対象外です（検索しません）。")
        st.caption("処理に30〜60秒程度かかる場合があります。ブラウザを閉じずにお待ちください。")
        submitted = st.form_submit_button("🔍 検索する", type="primary")

    if submitted:
        keyword, kw_error = kd.validate_keyword(st.session_state.get(KEYWORD_INPUT, ""))
        statuses, status_error = kd.validate_statuses(_selected_statuses(st.session_state))
        error = kw_error or status_error
        if error:
            st.session_state[TOP_ERROR] = error
            st.session_state[RESULT] = None
            st.rerun()

        api_client, client_error = _make_client()
        if client_error:
            st.session_state[TOP_ERROR] = client_error
            st.session_state[RESULT] = None
            st.rerun()

        st.session_state[TOP_ERROR] = ""
        with st.spinner("Kickstarterを検索中です（30〜60秒程度かかります）..."):
            result = kd.run_discovery(api_client, keyword, statuses)
        st.session_state[RESULT] = result
        st.session_state[LAST_KEYWORD] = keyword
        st.session_state[LAST_STATUSES] = statuses
        st.rerun()

    result = st.session_state.get(RESULT)
    if result is not None:
        st.divider()
        last_keyword = st.session_state.get(LAST_KEYWORD, "")
        last_statuses = st.session_state.get(LAST_STATUSES, [])
        st.caption(
            f"検索条件: 「{last_keyword}」／ "
            + "・".join(kd.STATUS_LABELS_JA[s] for s in last_statuses)
        )
        _render_result(result, last_statuses)
