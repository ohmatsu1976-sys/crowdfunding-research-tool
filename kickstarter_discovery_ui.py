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
CATEGORY_SELECT = "kd_category_select"
AMOUNT_MIN_SELECT = "kd_amount_min_select"
AMOUNT_MAX_SELECT = "kd_amount_max_select"
RESULT = "kd_result"
LAST_KEYWORD = "kd_last_keyword"
LAST_STATUSES = "kd_last_statuses"
LAST_CATEGORY = "kd_last_category"
LAST_MIN_YEN = "kd_last_min_yen"
LAST_MAX_YEN = "kd_last_max_yen"
TOP_ERROR = "kd_top_error"
PENDING_HANDOFF_URL = "kd_pending_handoff_url"   # streamlit_app.py が読んで検索欄へ渡す

# Indiegogoタブ（公式Public API。Kickstarterタブとは独立した状態）
IGG_KEYWORD_INPUT = "kd_igg_keyword_input"
IGG_RESULT = "kd_igg_result"
IGG_LAST_KEYWORD = "kd_igg_last_keyword"
IGG_TOP_ERROR = "kd_igg_top_error"

_PREFIX = "kd_"

_STATUS_CHECKBOX_KEYS = {
    kd.STATUS_LIVE: STATUS_LIVE_CB,
    kd.STATUS_LATE_PLEDGE: STATUS_LATE_CB,
    kd.STATUS_ENDED: STATUS_ENDED_CB,
}

_AMOUNT_MIN_VALUE_BY_LABEL = {label: yen for yen, label in kd.AMOUNT_MIN_CHOICES}
_AMOUNT_MAX_VALUE_BY_LABEL = {label: yen for yen, label in kd.AMOUNT_MAX_CHOICES}
_AMOUNT_MIN_LABELS = [label for _, label in kd.AMOUNT_MIN_CHOICES]
_AMOUNT_MAX_LABELS = [label for _, label in kd.AMOUNT_MAX_CHOICES]

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
    if CATEGORY_SELECT not in state:
        state[CATEGORY_SELECT] = kd.CATEGORY_ALL
    if AMOUNT_MIN_SELECT not in state:
        state[AMOUNT_MIN_SELECT] = kd.AMOUNT_NONE_LABEL
    if AMOUNT_MAX_SELECT not in state:
        state[AMOUNT_MAX_SELECT] = kd.AMOUNT_NONE_LABEL
    if RESULT not in state:
        state[RESULT] = None
    if TOP_ERROR not in state:
        state[TOP_ERROR] = ""
    if IGG_RESULT not in state:
        state[IGG_RESULT] = None
    if IGG_TOP_ERROR not in state:
        state[IGG_TOP_ERROR] = ""


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


def _format_category(p: "kd.Product") -> str:
    if p.category_parent:
        return kd.CATEGORY_LABELS_JA.get(p.category_parent, p.category_parent)
    if p.category_raw:
        return f"{p.category_raw}（親カテゴリー未確認）"
    return "未取得"


def _format_igg_date(iso: str) -> str:
    """ISO日時文字列から日付部分だけを安全に取り出す（無ければ「不明」）"""
    text = (iso or "").strip()
    if not text:
        return "不明"
    return text.split("T")[0] if "T" in text else text


def _render_indiegogo_product_card(p: "kd.IndiegogoProduct", idx) -> None:
    with st.container(border=True):
        cols = st.columns([1, 3])
        with cols[0]:
            if p.image_url:
                try:
                    st.image(p.image_url, width="stretch")
                except Exception:
                    pass
        with cols[1]:
            st.markdown(f"**{p.name or '（商品名未取得）'}**")
            st.caption(p.short_description or "（商品概要は取得できませんでした）")
            st.write(
                f"支援総額: {_format_amount(p.raised_amount, p.currency)}　／　"
                f"日本円概算: {_format_jpy(p)}"
            )
            if p.jpy_rate_date:
                st.caption(f"為替レート基準日（ECB）: {p.jpy_rate_date}")
            st.write(
                f"目標額: {_format_amount(p.goal_amount, p.currency)}　／　"
                f"達成率: {f'{p.percent_funded:.0f}%' if p.percent_funded is not None else '未取得'}　／　"
                f"支援者数: {p.backers if p.backers is not None else '未取得'}人"
            )
            st.write(
                f"開始日: {_format_igg_date(p.start_date_iso)}　／　"
                f"終了日: {_format_igg_date(p.end_date_iso)}　／　"
                f"メーカー: {p.maker or '不明'}"
            )
            st.caption(f"URL: {p.url}")

            btn_col1, btn_col2 = st.columns(2)
            with btn_col1:
                if st.button("🔍 この商品を詳しくリサーチ", key=f"kd_igg_use_{idx}_{p.url}"):
                    st.session_state[PENDING_HANDOFF_URL] = p.url
                    st.session_state[candidates_ui.PENDING_VIEW] = candidates_ui.VIEW_SEARCH
                    st.rerun()
            with btn_col2:
                st.link_button("🌐 Indiegogoで確認", p.url, width="stretch")


def _render_indiegogo_tab() -> None:
    """Indiegogoタブ（公式Public API）を描画する

    公式APIにはキーワード・カテゴリー検索が無いため、現在アクティブな
    プロジェクト一覧を取得してからキーワードで絞り込む。そのためKickstarter
    タブとは異なり、カテゴリー・国での絞り込みは表示しない。
    """
    st.info(
        "Indiegogoは、公式APIの仕様上、**現在アクティブな商品のみ**が検索対象です"
        "（終了済み・調達成功済みの商品は一覧に含まれません）。"
        "カテゴリー・国による絞り込みはできません。",
        icon="ℹ️",
    )

    err = st.session_state.get(IGG_TOP_ERROR, "")
    if err:
        st.error(err)

    with st.form("kd_igg_search_form"):
        st.text_input(
            "キーワード（英語・任意）",
            key=IGG_KEYWORD_INPUT,
            placeholder="例: camera / backpack / smart watch",
            help="商品名・概要への部分一致です（大文字小文字は区別しません）。"
                "空欄の場合は現在アクティブな商品すべてが対象になります。",
        )
        st.caption("処理には数秒程度かかります。")
        igg_submitted = st.form_submit_button("🔍 Indiegogoを検索する", type="primary")

    if igg_submitted:
        st.session_state[IGG_TOP_ERROR] = ""
        keyword = st.session_state.get(IGG_KEYWORD_INPUT, "")
        with st.spinner("Indiegogoを検索中です..."):
            result = kd.search_indiegogo_active_projects(keyword)
        if result.error:
            st.session_state[IGG_TOP_ERROR] = result.error
            st.session_state[IGG_RESULT] = None
        else:
            st.session_state[IGG_RESULT] = result
            st.session_state[IGG_LAST_KEYWORD] = keyword
        st.rerun()

    result = st.session_state.get(IGG_RESULT)
    if result is not None:
        st.divider()
        last_keyword = st.session_state.get(IGG_LAST_KEYWORD, "")
        st.caption(
            f"検索条件: キーワード「{last_keyword or '（なし）'}」／ "
            f"現在アクティブな商品 {result.total_active_count} 件のうち "
            f"{result.matched_count} 件が一致"
        )
        if not result.products:
            st.info(
                "今回の検索条件に一致する商品を確認できませんでした。"
                "これはIndiegogo上に該当商品が存在しないという意味ではありません"
                "（現在アクティブな商品だけが検索対象です）。"
                "キーワードを変えて再度お試しください。"
            )
        for i, p in enumerate(result.products):
            _render_indiegogo_product_card(p, idx=i)

        if result.jpy_rate_error:
            st.caption(f"為替レート取得エラー: {result.jpy_rate_error}（日本円概算は未取得として表示しています）")


def _render_product_card(p: "kd.Product", idx, show_match_badge: bool = False) -> None:
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
            if show_match_badge:
                st.success("✅ 検索条件に一致")
            st.caption(p.summary_ja or "（商品概要は取得できませんでした）")
            st.write(f"Kickstarterカテゴリー: {_format_category(p)}")
            st.write(
                f"支援総額: {_format_amount(p.raised_amount, p.currency)}　／　"
                f"日本円概算: {_format_jpy(p)}"
            )
            if p.jpy_rate_date:
                st.caption(f"為替レート基準日（ECB）: {p.jpy_rate_date}")
            st.write(
                f"目標額: {_format_amount(p.goal_amount, p.currency)}　／　"
                f"達成率: {f'{p.percent_funded:.0f}%' if p.percent_funded is not None else '未取得'}　／　"
                f"支援者数: {p.backers if p.backers is not None else '未取得'}人"
            )
            st.write(f"{_format_days(p)}　／　メーカー: {p.maker or '不明'}　／　国: {p.country or '不明'}")
            st.caption(f"取得日時: {p.retrieved_at or '不明'}　／　URL: {p.url}")
            if p.status == kd.STATUS_MISMATCH and p.status_mismatch_reason:
                st.warning(p.status_mismatch_reason)
            if p.exclusion_reason:
                st.warning(f"条件外・判定不能の理由: {p.exclusion_reason}")

            btn_col1, btn_col2 = st.columns(2)
            with btn_col1:
                if st.button("🔍 この商品を詳しくリサーチ", key=f"kd_use_{idx}_{p.url}"):
                    st.session_state[PENDING_HANDOFF_URL] = p.url
                    st.session_state[candidates_ui.PENDING_VIEW] = candidates_ui.VIEW_SEARCH
                    st.rerun()
            with btn_col2:
                st.link_button("🌐 Kickstarterで確認", p.url, width="stretch")


def _render_unconfirmed_candidate_card(card: "kd.CandidateCard") -> None:
    """個別ページ取得が未確認の候補を、確実な情報だけで最小限に表示する

    商品名はWeb Searchの検索結果タイトルをそのまま使う。タイトルが
    無い・不明確な場合は商品名を創作せず、確実な情報であるURLを表示する。
    """
    display_name = card.title.strip() if card.title and card.title.strip() else card.url
    with st.container(border=True):
        st.markdown(f"**{display_name}**")
        st.caption("カテゴリー・支援総額・支援者数・募集状況・終了日: 未確認")
        st.caption(f"URL: {card.url}")
        st.link_button("🌐 Kickstarterで確認", card.url, width="stretch")


def _render_candidate_section(result: "kd.DiscoveryResult") -> None:
    """検索で見つかった候補を、個別ページ取得の成否に関わらず一覧表示する

    - 候補0件：候補が無い旨を案内する。
    - 候補あり・詳細未確認：タイトル・URLだけの最小限カード。
    - 詳細確認済み：既存の商品カード（条件一致が確認できた場合のみ
      「✅ 検索条件に一致」を表示する）。
    """
    st.markdown(f"#### 条件に近い商品候補（{result.candidate_count}件）")
    if result.candidate_count == 0:
        st.info(
            "今回の検索では候補を確認できませんでした。"
            "これはKickstarter上に該当商品が存在しないという意味ではありません。"
            "キーワードや選択ステータスを変えて再度お試しください。"
        )
        return
    st.caption(
        "検索条件をもとに見つかった商品候補です。支援金額や募集状況など、"
        "一部の条件はまだ確認できていません。気になる商品はKickstarterで詳細をご確認ください。"
    )
    for i, card in enumerate(result.candidate_cards):
        if card.detail_confirmed and card.product is not None:
            _render_product_card(card.product, idx=f"cand_{i}",
                                 show_match_badge=card.condition_matched)
        else:
            _render_unconfirmed_candidate_card(card)


def _render_summary_counts(result: "kd.DiscoveryResult") -> None:
    c1, c2, c3 = st.columns(3)
    with c1:
        st.metric("取得候補数", result.candidate_count)
        st.metric("カテゴリー判定不能数", result.category_unknown_count)
    with c2:
        st.metric("個別ページ取得成功数", result.fetch_success_count)
        st.metric("金額判定不能数", result.amount_unknown_count)
    with c3:
        st.metric("条件一致数", result.matched_count)
        st.metric("条件外の商品数", result.off_condition_count)


def _render_result(result: "kd.DiscoveryResult", selected_statuses: List[str],
                   category: str = "all", min_yen: Optional[int] = None,
                   max_yen: Optional[int] = None) -> None:
    _render_summary_counts(result)
    st.divider()

    _render_candidate_section(result)

    with st.expander("取得できなかった・除外した候補の詳細", expanded=False):
        if result.fetch_failed:
            st.markdown("**個別ページの取得に失敗した候補**")
            for e in result.fetch_failed:
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
        if not any([result.fetch_failed, result.excluded_duplicate,
                   result.excluded_invalid_url, result.candidates_dropped_by_cap]):
            st.caption("該当するものはありませんでした。")

    if result.search_errors:
        for msg in result.search_errors:
            st.warning(f"検索中にエラーが発生しました: {msg}")

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


def _render_kickstarter_tab(client) -> None:
    """Kickstarterタブ（既存のAI検索。ロジック・UIともに変更なし）を描画する"""
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
        category = st.selectbox(
            "Kickstarterカテゴリー",
            kd.CATEGORY_OPTIONS,
            format_func=lambda c: kd.CATEGORY_LABELS_JA.get(c, c),
            key=CATEGORY_SELECT,
        )
        _keyword_required = (category == kd.CATEGORY_ALL)
        st.text_input(
            "日本語キーワード" + ("（必須）" if _keyword_required else "（任意）"),
            key=KEYWORD_INPUT,
            placeholder="例：" + " / ".join(_KEYWORD_EXAMPLES),
            help=(
                "最大200文字。カテゴリーを「すべて」以外に選ぶと空欄でも検索できます。 "
                + " ".join(f"例：{e}" for e in _KEYWORD_EXAMPLES)
            ),
        )

        col_min, col_max = st.columns(2)
        with col_min:
            st.selectbox("支援総額（下限）", _AMOUNT_MIN_LABELS, key=AMOUNT_MIN_SELECT)
        with col_max:
            st.selectbox("支援総額（上限）", _AMOUNT_MAX_LABELS, key=AMOUNT_MAX_SELECT)

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
        category = st.session_state.get(CATEGORY_SELECT, kd.CATEGORY_ALL)
        min_yen = _AMOUNT_MIN_VALUE_BY_LABEL.get(
            st.session_state.get(AMOUNT_MIN_SELECT, kd.AMOUNT_NONE_LABEL))
        max_yen = _AMOUNT_MAX_VALUE_BY_LABEL.get(
            st.session_state.get(AMOUNT_MAX_SELECT, kd.AMOUNT_NONE_LABEL))

        keyword, kw_error = kd.validate_keyword(
            st.session_state.get(KEYWORD_INPUT, ""), category)
        statuses, status_error = kd.validate_statuses(_selected_statuses(st.session_state))
        amount_error = kd.validate_amount_range(min_yen, max_yen)
        error = kw_error or status_error or amount_error
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
            result = kd.run_discovery(api_client, keyword, statuses,
                                      category=category, min_yen=min_yen, max_yen=max_yen)
        st.session_state[RESULT] = result
        st.session_state[LAST_KEYWORD] = keyword
        st.session_state[LAST_STATUSES] = statuses
        st.session_state[LAST_CATEGORY] = category
        st.session_state[LAST_MIN_YEN] = min_yen
        st.session_state[LAST_MAX_YEN] = max_yen
        st.rerun()

    result = st.session_state.get(RESULT)
    if result is not None:
        st.divider()
        last_keyword = st.session_state.get(LAST_KEYWORD, "")
        last_statuses = st.session_state.get(LAST_STATUSES, [])
        last_category = st.session_state.get(LAST_CATEGORY, kd.CATEGORY_ALL)
        last_min_yen = st.session_state.get(LAST_MIN_YEN)
        last_max_yen = st.session_state.get(LAST_MAX_YEN)
        amount_txt = kd.describe_amount_range_ja(last_min_yen, last_max_yen) or "指定なし"
        st.caption(
            f"検索条件: キーワード「{last_keyword or '（なし）'}」／ "
            f"カテゴリー「{kd.CATEGORY_LABELS_JA.get(last_category, last_category)}」／ "
            f"支援総額「{amount_txt}」／ "
            + "・".join(kd.STATUS_LABELS_JA[s] for s in last_statuses)
        )
        _render_result(result, last_statuses, category=last_category,
                       min_yen=last_min_yen, max_yen=last_max_yen)


def render_screen(client) -> None:
    """「商品を探す」画面を描画する（streamlit_app.py から呼ぶ）

    Kickstarter（既存のAI検索）とIndiegogo（公式Public API）をタブで分ける。
    タブを分けるのは、両者の検索条件（カテゴリー・国の絞り込み可否、検索対象
    範囲）が異なり、1つの検索条件に混在させると誤解を招くため。
    """
    init_state(st.session_state)

    st.subheader("🔎 商品を探す")

    tab_ks, tab_igg = st.tabs(["Kickstarter", "Indiegogo"])
    with tab_ks:
        _render_kickstarter_tab(client)
    with tab_igg:
        _render_indiegogo_tab()
