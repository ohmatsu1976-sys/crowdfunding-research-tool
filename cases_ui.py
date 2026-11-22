# -*- coding: utf-8 -*-
"""商品案件管理（第1段階「返信獲得」）の画面

ダッシュボード（通常版の既定画面）・案件一覧・案件詳細（基本表示）を描画する。
DBアクセスは cases.py が行い、ここは Streamlit の画面だけを扱う
（candidates_ui.py と同じ方針）。

この第1回実装では、AI返信アシスタント・訂正／伏せ字化／取消しの画面は
含まない（AI APIは一切呼び出さない。履歴の訂正等は今後の実装）。
管理項目を増やしすぎないため、案件詳細の操作ボタンは現在の状態ごとに
1〜2個の「次にやること」だけを出す。
"""

from datetime import date as _date

import streamlit as st

import approach_messages
import auth
import cases
import sender_profiles as sp
# 画面切替・案件管理との受け渡しキーは、依存の無い共通モジュールが正本
# （candidates_ui.pyを直接importしない。以前はここでcandidates_ui.pyの
#  属性をモジュール読み込み時に参照しており、初期化順序によって
#  AttributeErrorになる不具合があった。再発防止のため、画面切替の親である
#  candidates_ui.py側も同じui_state_keys.pyから読む構成にしている）。
import ui_state_keys as uikeys

# session_state キー（他機能と衝突しないよう case_ で始める。
# uikeys.SELECTED_CASE_ID も同じ接頭辞のため、clear_state() で一括消去される）
# LAST_MESSAGEはuikeys.CASES_FLASH_MESSAGEと同じ値にする（マイ候補リスト
# 画面の「商品案件を作成」ボタンから、遷移先のこの画面へメッセージを渡すため）。
LAST_MESSAGE = uikeys.CASES_FLASH_MESSAGE
SHOW_ARCHIVED = "case_show_archived"   # 案件一覧でアーカイブ済みも表示するか
STATUS_FILTER = "case_status_filter"   # 案件一覧のステータス絞り込み

_PREFIX = "case_"

_ALL_LABEL = "すべて"
_STATUS_BADGE = {
    cases.STATUS_NOT_CONTACTED: "⚪",
    cases.STATUS_AWAITING_FIRST_REPLY: "🔵",
    cases.STATUS_AWAITING_OUR_REPLY: "🟠",
    cases.STATUS_AWAITING_NEXT_REPLY: "🔵",
    cases.STATUS_ADVANCED: "🟢",
    cases.STATUS_ON_HOLD: "🟡",
    cases.STATUS_LOST: "🔴",
}
_CHANNEL_LABELS_JA = {
    "email": "メール", "facebook": "Facebook", "instagram": "Instagram",
    "form": "問い合わせフォーム", "linkedin": "LinkedIn", "official": "公式サイト",
    "listing": "掲載ページ", "none": "未設定",
}
_CONTACT_FIELD_LABELS = (
    ("email", "メールアドレス"), ("facebook", "Facebook"), ("instagram", "Instagram"),
    ("contact_form", "問い合わせフォーム"), ("linkedin", "LinkedIn"),
    ("official_url", "公式サイト"),
)


def clear_state(state) -> None:
    """case_ で始まる session_state キーをすべて消す（ログアウト時に呼ぶ）"""
    for key in [k for k in list(state.keys()) if str(k).startswith(_PREFIX)]:
        state.pop(key, None)


def _flash(message: str) -> None:
    st.session_state[LAST_MESSAGE] = message


def _show_flash() -> None:
    message = st.session_state.pop(LAST_MESSAGE, "")
    if message:
        st.info(message)


def _open_case(case_id: str) -> None:
    """案件一覧画面の、指定した案件の詳細を開く（別画面からの遷移にも使う）

    candidates_ui.VIEW は既にst.radio(key=VIEW)で描画済みのため、直接代入すると
    Streamlitが例外にする。PENDING_VIEW経由で次回実行の冒頭に反映する
    （candidates_ui.render_view_switchがstop()より前に処理する）。
    """
    st.session_state[uikeys.PENDING_VIEW] = uikeys.VIEW_CASES
    st.session_state[uikeys.SELECTED_CASE_ID] = case_id


# =============================================================================
# ダッシュボード（通常版の既定画面）
# =============================================================================

def render_dashboard_screen(client) -> None:
    st.subheader("🏠 ダッシュボード")
    _show_flash()

    if not auth.apply_session(client, st.session_state):
        _flash(auth.SESSION_EXPIRED)
        st.rerun()

    user_id = st.session_state.get(auth.USER_ID, "")
    all_cases = cases.list_cases(client, user_id, include_archived=True)

    if not all_cases:
        st.info(
            "まだ商品案件がありません。「⭐ マイ候補リスト」の商品で"
            "「📋 商品案件を作成」を押すと、ここにアプローチの進み具合が表示されます。"
        )
        return

    month = cases.current_month_start()
    stats = cases.compute_monthly_stats(all_cases, month)
    target = cases.get_month_target(client, user_id, month)

    st.markdown(f"#### 今月の実績（{month.year}年{month.month}月）")
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        approach_text = (f"{stats.approach_count} / {target.approach_target}"
                         if target.approach_target else f"{stats.approach_count}")
        st.metric("アプローチ数", approach_text)
    with c2:
        reply_text = (f"{stats.reply_count} / {target.reply_target}"
                      if target.reply_target else f"{stats.reply_count}")
        st.metric("返信数", reply_text)
    with c3:
        rate_text = f"{stats.reply_rate * 100:.0f}%" if stats.reply_rate is not None else "－"
        st.metric("返信率", rate_text)
    with c4:
        st.metric("商談移行数", str(stats.advanced_count))

    with st.expander("🎯 今月の目標を設定する"):
        with st.form("case_target_form"):
            approach_target = st.number_input(
                "アプローチ数の目標", min_value=0, max_value=100000,
                value=int(target.approach_target), step=1)
            reply_target = st.number_input(
                "返信数の目標", min_value=0, max_value=100000,
                value=int(target.reply_target), step=1)
            submitted = st.form_submit_button("目標を保存する", type="primary")
        if submitted:
            ok = cases.set_month_target(client, user_id, month,
                                        int(approach_target), int(reply_target))
            _flash("今月の目標を保存しました。" if ok else cases.TARGET_SAVE_FAILED)
            st.rerun()

    st.divider()
    st.markdown("#### 📝 今日やること（最大5件）")
    todo = cases.todays_todo(all_cases, limit=5)
    if not todo:
        st.success("今日やることはありません。お疲れさまでした！")
    else:
        for item in todo:
            c = item.case
            with st.container(border=True):
                col_info, col_btn = st.columns([4, 1])
                with col_info:
                    st.markdown(f"**{c.product_name}** ／ {c.maker_name}")
                    st.caption(f"{item.label}　（現在: {cases.STATUS_LABELS_JA.get(c.status, c.status)}）")
                with col_btn:
                    if st.button("この案件を開く", key=f"case_todo_{c.id}", width="stretch"):
                        _open_case(c.id)
                        st.rerun()


# =============================================================================
# 案件一覧・案件詳細（基本表示）
# =============================================================================

def render_cases_screen(client) -> None:
    _show_flash()

    if not auth.apply_session(client, st.session_state):
        _flash(auth.SESSION_EXPIRED)
        st.rerun()

    user_id = st.session_state.get(auth.USER_ID, "")
    selected_id = st.session_state.get(uikeys.SELECTED_CASE_ID, "")

    if selected_id:
        case = cases.get_case(client, user_id, selected_id)
        if case is None:
            st.session_state[uikeys.SELECTED_CASE_ID] = ""
            _flash("指定の案件が見つかりませんでした。")
            st.rerun()
        _render_detail(client, user_id, case)
        return

    _render_list(client, user_id)


def _render_list(client, user_id: str) -> None:
    st.subheader("📋 案件一覧")

    col_filter, col_archived = st.columns([3, 1])
    with col_filter:
        status_choices = ("",) + cases.STATUS_OPTIONS
        status_filter = st.selectbox(
            "ステータスで絞り込み", status_choices,
            format_func=lambda s: cases.STATUS_LABELS_JA.get(s, _ALL_LABEL) if s else _ALL_LABEL,
            key=STATUS_FILTER,
        )
    with col_archived:
        show_archived = st.checkbox("アーカイブ済みも表示", key=SHOW_ARCHIVED)

    all_cases = cases.list_cases(client, user_id, include_archived=show_archived)
    if status_filter:
        all_cases = [c for c in all_cases if c.status == status_filter]

    if not all_cases:
        st.info("条件に一致する商品案件がありません。")
        return

    for c in all_cases:
        badge = _STATUS_BADGE.get(c.status, "")
        title = f"{badge} {c.product_name} ／ {c.maker_name}"
        if c.archived:
            title += "（アーカイブ済み）"
        with st.container(border=True):
            col_info, col_btn = st.columns([4, 1])
            with col_info:
                st.markdown(f"**{title}**")
                next_at = cases.to_jst_date(c.next_action_at) if c.next_action_at else None
                detail = f"状態: {cases.STATUS_LABELS_JA.get(c.status, c.status)}"
                if next_at:
                    detail += f"　／　次の予定: {next_at.isoformat()}" + (
                        f"（{c.next_action_label}）" if c.next_action_label else "")
                st.caption(detail)
            with col_btn:
                if st.button("詳細を見る", key=f"case_open_{c.id}", width="stretch"):
                    st.session_state[uikeys.SELECTED_CASE_ID] = c.id
                    st.rerun()


def _recipient_default(case: cases.Case) -> str:
    info = cases.resolve_contact_display(case.contact_snapshot, case.source_url, case.platform)
    return info.get("email") or info.get("url") or ""


_SENDER_PROFILE_SELECT_KEY = "case_sender_profile_id"


def _pick_sender_profile(client, user_id: str) -> tuple:
    """初回アプローチ文面に使う送信者名・会社名を選ぶ（氏名, 会社名）

    登録・編集・削除はしない（それは商品リサーチ画面のsender_profiles_ui.pyの
    管理UIが担う）。ここでは既存の送信者プロフィールを読んで使うだけ。
    複数件ある場合だけ、選び直せる小さなプルダウンを出す
    （sender_profiles.pick_auto_selected()と同じ基準で既定を選ぶ）。
    """
    profiles = sp.list_profiles(client, user_id) or []
    if not profiles:
        st.caption(
            "💡 送信者プロフィールが未登録のため、文面の氏名は仮の表記のままです。"
            "商品リサーチ画面の「📝 送信者プロフィール」から登録すると、ここにも反映されます。"
        )
        return "", ""
    if len(profiles) == 1:
        return profiles[0].sender_name_en, profiles[0].company_name_en

    by_id = {p.id: p for p in profiles}
    if st.session_state.get(_SENDER_PROFILE_SELECT_KEY) not in by_id:
        auto = sp.pick_auto_selected(profiles)
        st.session_state[_SENDER_PROFILE_SELECT_KEY] = auto.id if auto else profiles[0].id
    st.selectbox(
        "差出人プロフィール", list(by_id.keys()),
        format_func=lambda pid: by_id[pid].label,
        key=_SENDER_PROFILE_SELECT_KEY,
    )
    chosen = by_id.get(st.session_state.get(_SENDER_PROFILE_SELECT_KEY))
    return (chosen.sender_name_en, chosen.company_name_en) if chosen else ("", "")


def _render_detail(client, user_id: str, case: cases.Case) -> None:
    if st.button("← 案件一覧に戻る"):
        st.session_state[uikeys.SELECTED_CASE_ID] = ""
        st.rerun()

    badge = _STATUS_BADGE.get(case.status, "")
    st.subheader(f"{badge} {case.product_name}")
    st.caption(f"メーカー: {case.maker_name}　／　プラットフォーム: {case.platform or '不明'}")
    if case.source_url:
        st.caption(f"掲載URL: {case.source_url}")

    st.markdown(
        f"**現在の状態: {cases.STATUS_LABELS_JA.get(case.status, case.status)}**　"
        f"（主なチャネル: {_CHANNEL_LABELS_JA.get(case.primary_channel, case.primary_channel)}）"
    )

    contact_lines = [
        f"- {label}: {case.contact_snapshot.get(key)}"
        for key, label in _CONTACT_FIELD_LABELS
        if case.contact_snapshot.get(key)
    ]
    if contact_lines:
        with st.expander("連絡先候補"):
            st.markdown("\n".join(contact_lines))

    memo = st.text_area("メモ", value=case.memo, key=f"case_memo_{case.id}")
    if st.button("メモを更新する", key=f"case_memo_update_{case.id}"):
        ok = cases.update_case(client, user_id, case.id, memo=memo)
        _flash("メモを更新しました。" if ok else "メモを更新できませんでした。")
        st.rerun()

    st.divider()
    st.markdown("#### 次にやること")
    _render_primary_action(client, user_id, case)

    st.divider()
    with st.expander("その他の操作"):
        _render_status_change_actions(client, user_id, case)
        st.divider()
        archive_label = "アーカイブを解除" if case.archived else "アーカイブする"
        if st.button(archive_label, key=f"case_archive_{case.id}"):
            ok = cases.update_case(client, user_id, case.id, archived=not case.archived)
            _flash(("案件を一覧に戻しました。" if case.archived else "案件をアーカイブしました。")
                  if ok else "更新できませんでした。")
            st.rerun()

    st.divider()
    st.markdown("#### 履歴")
    _render_timeline(client, user_id, case.id)


def _log_and_flash(client, case_id: str, kind: str, *, channel: str = "",
                   recipient: str = "", message_text: str = "") -> None:
    result = cases.log_event(client, case_id, kind, channel=channel,
                             recipient=recipient, message_text=message_text)
    _flash("記録しました。" if result.ok else result.message)


def _render_primary_action(client, user_id: str, case: cases.Case) -> None:
    status = case.status

    if status == cases.STATUS_NOT_CONTACTED:
        channel = st.selectbox(
            "連絡チャネル", cases.CHANNEL_OPTIONS,
            index=(cases.CHANNEL_OPTIONS.index(case.primary_channel)
                  if case.primary_channel in cases.CHANNEL_OPTIONS else 0),
            format_func=lambda c: _CHANNEL_LABELS_JA.get(c, c),
            key=f"case_channel_{case.id}")

        sender_name, sender_company = _pick_sender_profile(client, user_id)
        draft = approach_messages.build_approach_message(
            product_name=case.product_name, maker_name=case.maker_name,
            channel=channel, sender_name=sender_name, sender_company=sender_company,
        )
        subject_key = f"case_subject_{case.id}_{channel}"
        body_key = f"case_body_{case.id}_{channel}"
        with st.expander("✉️ 初回メッセージの文面案（チャネル別・編集・コピー可）", expanded=True):
            st.caption(
                "決定論的なテンプレートから自動生成した文面案です（AIは使用していません）。"
                "内容を確認・編集してから、お使いのメールソフトやSNS等からご自身で送信してください。"
                "この画面から送信は行われません。"
            )
            st.caption(draft.usage_note)
            if draft.uses_subject:
                st.text_input("件名", value=draft.subject, key=subject_key)
            st.text_area("本文", value=draft.body, height=260, key=body_key)

        with st.form(f"case_initial_{case.id}"):
            recipient = st.text_input("送信先", value=_recipient_default(case))
            st.caption(
                "⚠️ このボタンは、上の文面を外部（メールソフト・SNS等）で"
                "実際に送信した**あとに**押してください。送信した事実を記録するだけで、"
                "ここから送信は行われません。"
            )
            submitted = st.form_submit_button("✉️ 初回メッセージを送信した", type="primary")
        if submitted:
            cases.update_case(client, user_id, case.id, primary_channel=channel)
            body_text = st.session_state.get(body_key, draft.body)
            if draft.uses_subject:
                subject_text = st.session_state.get(subject_key, draft.subject)
                message_text = f"件名: {subject_text}\n\n{body_text}" if subject_text else body_text
            else:
                message_text = body_text
            _log_and_flash(client, case.id, cases.EVENT_INITIAL,
                          channel=channel, recipient=recipient, message_text=message_text)
            st.rerun()
        return

    if status == cases.STATUS_AWAITING_FIRST_REPLY:
        if case.next_action_label == "2回目フォロー":
            followup_kind, followup_label = cases.EVENT_FOLLOWUP2, "2回目フォローアップを送った"
        else:
            followup_kind, followup_label = cases.EVENT_FOLLOWUP1, "1回目フォローアップを送った"
        col_followup, col_reply = st.columns(2)
        with col_followup:
            if st.button(f"📨 {followup_label}", key=f"case_followup_{case.id}", width="stretch"):
                _log_and_flash(client, case.id, followup_kind)
                st.rerun()
        with col_reply:
            if st.button("📬 返信を受け取った", key=f"case_reply_{case.id}", width="stretch",
                        type="primary"):
                _log_and_flash(client, case.id, cases.EVENT_REPLY)
                st.rerun()
        return

    if status == cases.STATUS_AWAITING_OUR_REPLY:
        with st.form(f"case_our_reply_{case.id}"):
            message_text = st.text_area("送った内容のメモ（任意）", value="")
            submitted = st.form_submit_button("✉️ こちらの返信を送信した", type="primary")
        if submitted:
            _log_and_flash(client, case.id, cases.EVENT_OUR_REPLY, message_text=message_text)
            st.rerun()
        return

    if status == cases.STATUS_AWAITING_NEXT_REPLY:
        if st.button("📬 メーカーからまた返信があった", key=f"case_reply_next_{case.id}",
                    type="primary"):
            _log_and_flash(client, case.id, cases.EVENT_REPLY)
            st.rerun()
        with st.expander("次に確認する日を決めておく（任意）"):
            remind_at = st.date_input("次回確認予定日", value=None,
                                      key=f"case_remind_date_{case.id}")
            if st.button("この日を設定する", key=f"case_remind_set_{case.id}"):
                if isinstance(remind_at, _date):
                    ok = cases.update_case(client, user_id, case.id,
                                          next_action_at=remind_at,
                                          next_action_label="次回確認予定")
                    _flash("次回確認予定日を設定しました。" if ok else "設定できませんでした。")
                    st.rerun()
        return

    if status == cases.STATUS_ADVANCED:
        st.success("商談へ進んだ案件です。以降は商談の進め方に沿って対応してください。")
        return

    st.info(f"現在「{cases.STATUS_LABELS_JA.get(status, status)}」です。"
           "再開するには、下の「その他の操作」からメモを残すか、"
           "メーカーからの反応があった際に改めて記録してください。")


def _render_status_change_actions(client, user_id: str, case: cases.Case) -> None:
    status = case.status
    if status in (cases.STATUS_ADVANCED, cases.STATUS_ON_HOLD, cases.STATUS_LOST):
        st.caption("状態変更はできません（終了・商談済みの案件）。")
        return

    can_advance = status in (cases.STATUS_AWAITING_OUR_REPLY, cases.STATUS_AWAITING_NEXT_REPLY)
    cols = st.columns(3)
    with cols[0]:
        if can_advance and st.button("🤝 商談へ進める", key=f"case_advance_{case.id}"):
            result = cases.log_event(client, case.id, cases.EVENT_STATUS_CHANGE,
                                     new_status=cases.STATUS_ADVANCED)
            _flash("商談へ進めました。" if result.ok else result.message)
            st.rerun()
    with cols[1]:
        if st.button("⏸ 保留にする", key=f"case_hold_{case.id}"):
            result = cases.log_event(client, case.id, cases.EVENT_STATUS_CHANGE,
                                     new_status=cases.STATUS_ON_HOLD)
            _flash("保留にしました。" if result.ok else result.message)
            st.rerun()
    with cols[2]:
        confirm_key = f"case_lost_confirm_{case.id}"
        confirmed = st.checkbox("見送る", key=confirm_key)
        if st.button("🚫 見送る", key=f"case_lost_{case.id}", disabled=not confirmed):
            result = cases.log_event(client, case.id, cases.EVENT_STATUS_CHANGE,
                                     new_status=cases.STATUS_LOST)
            _flash("見送りにしました。" if result.ok else result.message)
            st.rerun()


_EVENT_KIND_LABELS_JA = {
    cases.EVENT_INITIAL: "初回送信", cases.EVENT_FOLLOWUP1: "1回目フォロー",
    cases.EVENT_FOLLOWUP2: "2回目フォロー", cases.EVENT_REPLY: "返信受信",
    cases.EVENT_OUR_REPLY: "こちらの返信", cases.EVENT_CONDITION_CHECK: "条件確認",
    cases.EVENT_CONDITION_ANSWER: "条件回答", cases.EVENT_STATUS_CHANGE: "状態変更",
    cases.EVENT_NOTE: "メモ",
}


def _event_line(ev: "cases.CaseEvent", *, voided: bool = False) -> str:
    occurred = cases.to_jst_date(ev.occurred_at)
    label = _EVENT_KIND_LABELS_JA.get(ev.kind, ev.kind)
    line = f"{occurred.isoformat() if occurred else '－'}　**{label}**"
    if ev.channel:
        line += f"　（{_CHANNEL_LABELS_JA.get(ev.channel, ev.channel)}）"
    if ev.redacted_at:
        line += "　🔒伏せ字化済み"
    if voided:
        voided_date = cases.to_jst_date(ev.voided_at)
        line += f"　（取消し済み: {voided_date.isoformat() if voided_date else '－'}）"
    return line


def _render_correct_form(client, ev: "cases.CaseEvent") -> None:
    st.markdown("**✏️ 訂正**（本文・翻訳文だけを直せます。種類・発生日時は変わりません）")
    with st.form(f"case_correct_{ev.id}"):
        message_text = st.text_area("本文", value=ev.message_text, key=f"case_correct_msg_{ev.id}")
        translated_text = st.text_area("翻訳文", value=ev.translated_text,
                                       key=f"case_correct_tr_{ev.id}")
        submitted = st.form_submit_button("訂正を保存する")
    if submitted:
        result = cases.correct_event(client, ev.id, message_text=message_text,
                                     translated_text=translated_text)
        _flash("訂正しました。元の内容は「訂正・取消し履歴」に残ります。"
              if result.ok else result.message)
        st.rerun()


def _render_redact_action(client, ev: "cases.CaseEvent") -> None:
    st.markdown("**🔒 伏せ字化**")
    st.warning(
        "⚠️ 伏せ字化すると、本文・翻訳文・AI支援結果が元に戻せない固定の文言に"
        "置き換わります。あとから元の内容を復元することはできません。"
    )
    confirm_key = f"case_redact_confirm_{ev.id}"
    confirmed = st.checkbox("元に戻せないことを理解した上で、伏せ字化します", key=confirm_key)
    if st.button("🔒 伏せ字化する", key=f"case_redact_btn_{ev.id}", disabled=not confirmed):
        result = cases.redact_event(client, ev.id)
        _flash("伏せ字化しました。" if result.ok else result.message)
        st.rerun()


def _render_void_action(client, ev: "cases.CaseEvent") -> None:
    st.markdown("**🗑 取消し**（記録そのものは削除されず、「訂正・取消し履歴」に残ります）")
    reason = st.text_input("取消し理由（必須）", key=f"case_void_reason_{ev.id}")
    confirm_key = f"case_void_confirm_{ev.id}"
    confirmed = st.checkbox("この記録を取消します", key=confirm_key)
    can_submit = confirmed and bool(reason.strip())
    if st.button("🗑 取消す", key=f"case_void_btn_{ev.id}", disabled=not can_submit):
        result = cases.void_event(client, ev.id, reason=reason)
        _flash("取消しました。" if result.ok else result.message)
        st.rerun()


def _render_event_actions(client, ev: "cases.CaseEvent") -> None:
    with st.expander("この記録を訂正・伏せ字化・取消しする", expanded=False):
        if not ev.redacted_at:
            _render_correct_form(client, ev)
            st.divider()
            _render_redact_action(client, ev)
            st.divider()
        _render_void_action(client, ev)


def _render_timeline(client, user_id: str, case_id: str) -> None:
    active_events = cases.list_case_events(client, user_id, case_id, include_voided=False)
    if not active_events:
        st.caption("まだ記録がありません。")
    else:
        for ev in active_events:
            st.markdown(_event_line(ev))
            if ev.message_text:
                st.caption(ev.message_text[:200])
            _render_event_actions(client, ev)

    all_events = cases.list_case_events(client, user_id, case_id, include_voided=True)
    voided_events = [ev for ev in all_events if ev.voided_at]
    if voided_events:
        with st.expander(f"🗂 訂正・取消し履歴を表示（{len(voided_events)}件）"):
            for ev in voided_events:
                st.markdown(_event_line(ev, voided=True))
                if ev.voided_reason == "corrected":
                    st.caption("理由: 訂正による置き換え")
                elif ev.voided_reason:
                    st.caption(f"理由: {ev.voided_reason}")
                if ev.message_text:
                    st.caption(f"当時の内容: {ev.message_text[:200]}")
