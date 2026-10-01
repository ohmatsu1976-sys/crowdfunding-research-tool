# -*- coding: utf-8 -*-
"""サミット特典ユーザー向けの制限モード画面

app_metadata.role が "summit" の利用者だけに表示する、簡易版の商品リサーチ
画面。1件のURLを入力すると、商品概要・日本市場との相性・売れそうな理由・
懸念点・メーカー情報・連絡先候補だけを表示する。

判定は auth.get_role() が行う（app_metadata.role由来。本人が書き換え可能な
user_metadataには一切依存しない。詳細は auth._role_from_user 参照）。

表示しないもの（studentの画面構成とは別物として扱う）:
    - 営業メール文生成（件名・本文）
    - 保存機能（マイ候補リストへの保存）
    - 履歴（今回の1件だけを保持し、過去の検索結果は保持しない）
    - 高度分析（優先度A/B/C判定・判定の確度・調達額・支援者数・競合分析）
    - 生徒専用機能（マイ候補リスト・管理者ビュー・画面切替サイドバー）

商品取得・AI分析のロジックは research_crowdfunding.py の既存関数をそのまま
呼び出すだけで、既存の検索フロー（streamlit_app.py の Step1〜3）には一切
手を加えない。
"""

from typing import Any, Dict, Optional, Tuple

import streamlit as st

from research_crowdfunding import (
    analyze_with_claude,
    build_row,
    fetch_project_from_url,
    find_creator_site,
    find_official_site,
    get_contact_info,
)

TITLE = "海外ブランド AIリサーチアシスタント｜サミット特典版"
CAPTION = (
    "気になる海外商品のURLを入力してください。日本市場との相性や、"
    "売れそうな理由・懸念点をAIが簡易分析します。"
)

# 今回の1件だけを保持する（履歴は持たない。再分析すると上書きされる）
RESULT_KEY = "summit_last_result"
ERROR_KEY = "summit_error"

INVALID_URL = "有効な商品URLを入力してください。"
FETCH_FAILED = "商品情報を取得できませんでした。URLをご確認ください。"
_UNCONFIRMED = "未確認"


def _resolve_contact(p: Dict[str, Any]) -> Dict[str, Any]:
    """公式サイト・連絡先候補を探す（既存の探索ロジックの簡易版）"""
    creator_websites = list(p.get("_creator_websites", []) or [])
    if p.get("_official_site") and p["_official_site"] not in creator_websites:
        creator_websites = [p["_official_site"]] + creator_websites
    if not creator_websites:
        found_list = find_official_site(p.get("url", ""), p.get("maker", ""))
        if found_list:
            creator_websites = found_list
    if not creator_websites:
        found = find_creator_site(p.get("_creator_slug", ""), p.get("name", ""),
                                  p.get("maker", ""))
        if found:
            creator_websites = [found]

    contact = {"official_url": "", "email": _UNCONFIRMED, "contact_form": _UNCONFIRMED,
              "facebook": _UNCONFIRMED, "instagram": _UNCONFIRMED, "linkedin": _UNCONFIRMED}
    official_url = ""
    for site_url in creator_websites:
        if not site_url:
            continue
        if "indiegogo.com" in site_url or "kickstarter.com" in site_url:
            if not official_url:
                official_url = site_url
                contact["official_url"] = site_url
            continue
        c = get_contact_info(site_url, brand=p.get("maker", "") or p.get("_creator_slug", ""))
        if c.get("email", _UNCONFIRMED) != _UNCONFIRMED or \
           c.get("contact_form", _UNCONFIRMED) != _UNCONFIRMED:
            contact = c
            official_url = site_url
            break
        if official_url == "":
            contact = c
            official_url = site_url
    if not official_url and creator_websites:
        official_url = creator_websites[0]
        contact["official_url"] = official_url
    return contact


def _normalize_url(url: str) -> str:
    url = (url or "").strip()
    if not url.startswith("http"):
        return ""
    return url.split("?")[0].rstrip("/")


def _run_single(url: str, ai_client) -> Tuple[Optional[Dict[str, Any]], str]:
    """1件を取得・分析する。失敗時は (None, 安全なメッセージ) を返す"""
    url = _normalize_url(url)
    if not url:
        return None, INVALID_URL
    try:
        p = fetch_project_from_url(url)
    except Exception:
        return None, FETCH_FAILED
    if not p:
        return None, FETCH_FAILED

    contact = _resolve_contact(p)
    analysis = analyze_with_claude(p, ai_client)
    return build_row(p, analysis, contact), ""


def _ai_client():
    """Anthropic クライアントを作る。鍵が無い・読み込めない場合は None を返す

    client=None でも analyze_with_claude() は簡易な既定値を返すため、
    画面を落とさずに（プレースホルダ付きで）結果を表示できる。
    """
    try:
        api_key = st.secrets["ANTHROPIC_API_KEY"]
        import anthropic as _anthropic
        return _anthropic.Anthropic(api_key=api_key)
    except Exception:
        return None


def clear_state(state) -> None:
    """ログアウト時に、今回の分析結果・エラー表示を消す

    保存機能が無いため、消えるのは画面上の一時表示だけ（DBには何も残らない）。
    """
    state.pop(RESULT_KEY, None)
    state.pop(ERROR_KEY, None)


def _show_field(label: str, value: str) -> None:
    st.markdown(f"**{label}**")
    st.write(value or "（取得できませんでした）")


def render(client) -> None:
    """サミット特典ユーザー向けの制限モード画面を描画する

    client（Supabaseクライアント）は呼び出し元との引数の形をそろえる
    ためだけに受け取る（この画面はDBへは一切アクセスしない）。
    """
    st.title(TITLE)
    st.caption(CAPTION)
    st.divider()

    url = st.text_input(
        "商品URLを入力してください",
        placeholder="https://www.kickstarter.com/projects/example/product-name",
    )
    run = st.button("商品情報を取得", type="primary", disabled=not url.strip())

    if run:
        with st.spinner("商品情報を取得・分析しています..."):
            row, error = _run_single(url, _ai_client())
        st.session_state[ERROR_KEY] = error
        st.session_state[RESULT_KEY] = row
        st.rerun()

    error = st.session_state.get(ERROR_KEY, "")
    if error:
        st.error(error)

    row = st.session_state.get(RESULT_KEY)
    if not row:
        return

    st.divider()
    st.subheader(f"📦 {row.get('商品名', '') or '（商品名不明）'}")
    if row.get("掲載URL"):
        st.caption(row["掲載URL"])

    _show_field("商品概要", row.get("商品の特徴", ""))
    _show_field("日本市場との相性", row.get("日本で売れそうな理由", ""))
    _show_field("売れそうな理由", row.get("日本販売時の訴求ポイント", ""))
    _show_field("懸念点", row.get("注意点・懸念点", ""))

    st.markdown("**メーカー情報**")
    st.write(
        f"メーカー名: {row.get('メーカー名') or '不明'}　／　"
        f"プラットフォーム: {row.get('プラットフォーム') or '不明'}　／　"
        f"ジャンル: {row.get('商品ジャンル') or '不明'}"
    )

    st.markdown("**連絡先候補**")
    contact_lines = []
    if row.get("公式サイトURL"):
        contact_lines.append(f"公式サイト: {row['公式サイトURL']}")
    for label, key in (("メール", "メールアドレス"), ("問い合わせフォーム", "問い合わせフォームURL"),
                       ("Facebook", "Facebook"), ("Instagram", "Instagram"),
                       ("LinkedIn", "LinkedIn")):
        value = row.get(key, _UNCONFIRMED)
        if value and value != _UNCONFIRMED:
            contact_lines.append(f"{label}: {value}")
    if contact_lines:
        for line in contact_lines:
            st.write(line)
    else:
        st.write("（連絡先候補は見つかりませんでした）")
