"""
海外クラファン商品リサーチ Webツール
Streamlit Community Cloud にデプロイして受講生に共有する

デプロイ手順:
1. このフォルダを GitHub にプッシュ
2. share.streamlit.io でリポジトリを登録
3. Settings > Secrets に ANTHROPIC_API_KEY を設定
"""

import io
import re
import sys
import time
from pathlib import Path

import streamlit as st  # pandas は結果表示時に遅延import（起動高速化）

# research_crowdfunding.py と同じフォルダにあることを前提
sys.path.insert(0, str(Path(__file__).parent))
from research_crowdfunding import (
    API_WAIT_SEC,
    MAX_RAISED_USD,
    MIN_RAISED_USD,
    JPY_PER_USD,
    analyze_with_claude,
    build_row,
    fetch_project_from_url,
    find_official_site,
    find_creator_site,
    get_contact_info,
)
from summary_table import build_summary_html
import result_schema as rschema
import search_state as sstate

# ── ページ設定 ──────────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="クラファン商品リサーチ",
    page_icon="🔍",
    layout="wide",
    initial_sidebar_state="expanded",
)

# 検索結果はセッション単位で保持する（操作のたびの再実行で消えないようにする）
sstate.init_state(st.session_state)
# 列構成が変わったデプロイの後でも、開きっぱなしのセッションが落ちないようにする
sstate.migrate_state(st.session_state, rschema.normalize_rows, rschema.SCHEMA_VERSION)

# ── スタイル ────────────────────────────────────────────────────────────────────

st.markdown("""
<style>
    .priority-A { color: #1a7f37; font-weight: bold; }
    .priority-B { color: #bf8700; font-weight: bold; }
    .priority-C { color: #cf222e; font-weight: bold; }
    .metric-box { background: #f6f8fa; border-radius: 8px; padding: 12px 20px; }
</style>
""", unsafe_allow_html=True)

# ── サイドバー ──────────────────────────────────────────────────────────────────

with st.sidebar:
    st.markdown("## 候補URLの探し方")
    st.markdown("下のリンクで商品を探し、URLをコピーして貼り付けてください。")

    st.markdown("### Kickstarter")
    st.markdown("""
- [人気商品（Technology）](https://www.kickstarter.com/discover/categories/technology?sort=most_funded)
- [人気商品（Product Design）](https://www.kickstarter.com/discover/categories/product%20design?sort=most_funded)
- [人気商品（Fashion）](https://www.kickstarter.com/discover/categories/fashion?sort=most_funded)
- [終了間近の注目作](https://www.kickstarter.com/discover/categories/technology?sort=end_date)
""")

    st.markdown("### Indiegogo")
    st.markdown("""
- [トレンド商品](https://www.indiegogo.com/explore/all#/?sort=trending&project_type=campaign)
- [最多調達（Tech）](https://www.indiegogo.com/explore/tech#/?sort=most_funded&project_type=campaign)
- [最多調達（Design）](https://www.indiegogo.com/explore/design#/?sort=most_funded&project_type=campaign)
""")

    st.markdown("### ZECZEC（嘖嘖・台湾）")
    st.markdown("""
- [トップページ](https://www.zeczec.com/)

トップページ上部の「**探索**」メニューから
カテゴリ一覧を選択してください。
""")

    st.divider()
    st.divider()
    st.markdown("### 入力フォーマット")
    st.code("https://www.kickstarter.com/projects/xxx/yyy\nhttps://www.indiegogo.com/projects/zzz", language=None)
    st.caption("URLを1行1件で貼り付けるだけでOKです。調達額は自動取得します。")

    st.markdown("### 良い商品の見つけ方")
    st.markdown("""
**調達額の目安**
- ¥50M（$333K）以上が安心
- ¥300M（$2M）以上は競合が多め

**チェックポイント**
- 支援者数が多い（人気の証拠）
- 日本にまだ公式代理店がない
- 物理商品（食品・医薬品は除く）
- メーカーがアジア圏（連絡しやすい）

**避けたほうがよいカテゴリ**
- 食品・サプリメント（薬事法）
- コスメ・スキンケア（薬機法）
- 銃・刃物・危険物
""")

    st.divider()
    st.markdown("### 参考実績（メールに自動引用）")
    st.markdown("""
営業メールには、チームの**参考実績**として
以下のMakuakeプロジェクトが自動で引用されます。

- [Qstoves（Makuake）](https://www.makuake.com/project/qstoves/)
- [Travel Bag（Makuake）](https://www.makuake.com/project/travel-bag/)
- [Vacos Cam（CAMPFIRE）](https://camp-fire.jp/projects/view/313474)
- [Vacos Cam IR（CAMPFIRE）](https://camp-fire.jp/projects/view/587884)

*「私たちのチームの支援実績」として柔らかく引用されます。
特定の会社名・代表者名は入りません。*
""")
    st.divider()
    st.caption("© クラファン物販スクール")

# ── ヘッダー ────────────────────────────────────────────────────────────────────

st.title("🔍 海外クラファン商品リサーチツール")
st.caption(
    "Kickstarter・Indiegogo・ZECZEC のプロジェクト URL を入力すると、"
    "日本販売可能性・競合・営業メールを AI が自動分析します"
)

# ── 送信者情報入力 ──────────────────────────────────────────────────────────────
with st.expander("📝 営業メールの送信者名を入力してください", expanded=True):
    col_name, col_company = st.columns(2)
    with col_name:
        sender_name = st.text_input(
            "氏名（英語表記）",
            placeholder="例：Taro Yamada",
            help="営業メールの署名（Best regards 以下）に使用されます"
        )
    with col_company:
        sender_company = st.text_input(
            "会社名・屋号（任意）",
            placeholder="例：〇〇 Trading（空欄でもOK）",
            help="入力すると、自己紹介（My name is ... from ○○）と署名に反映されます。空欄なら省略されます"
        )
    if not sender_name:
        st.caption("⚠️ 未入力の場合、署名は [Your Name] のまま出力されます（あとから差し替え可）")

min_jpy = int(MIN_RAISED_USD * JPY_PER_USD / 1_000_000)
max_jpy = int(MAX_RAISED_USD * JPY_PER_USD / 1_000_000)
st.info(
    f"対象調達額の目安: **¥{min_jpy}M 〜 ¥{max_jpy}M** ／ "
    "調達額が取得できない場合も分析は実行されます",
    icon="ℹ️",
)

st.divider()

# ── Step 1: URL 入力 ────────────────────────────────────────────────────────────

st.subheader("Step 1　URLを貼り付けてください")
st.markdown(
    "KickstarterまたはIndiegogoまたはZECZECのプロジェクトURLを **1行に1つ** 貼り付けてください。"
    "最大 **30件** まで処理できます。"
)

PLACEHOLDER = (
    "https://www.kickstarter.com/projects/example/product-name\n"
    "https://www.indiegogo.com/projects/product-name\n"
    "https://zeczec.com/campaigns/1234567890/about\n"
    "（URLを1行1件で貼り付けてください）"
)

urls_text = st.text_area(
    label="URL一覧（1行1件）",
    height=220,
    placeholder=PLACEHOLDER,
    help="#で始まる行はコメントとして無視されます",
)

# URL + 金額のパース
def parse_url_lines(text: str):
    entries = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if not parts[0].startswith("http"):
            continue
        url = parts[0].split("?")[0].rstrip("/")
        # KickstarterのタブURLサフィックスを除去（/creator /description など）
        for _tab in ("/creator", "/description", "/updates", "/comments",
                     "/community", "/faqs", "/risks", "/rewards"):
            if url.endswith(_tab):
                url = url[:-len(_tab)]
                break
        # 金額を探す: $1,234,567 / ¥185,000,000 / 1234567
        amount_usd = 0.0
        for part in parts[1:]:
            cleaned = re.sub(r"[¥$,\s]", "", part)
            if cleaned.isdigit():
                val = float(cleaned)
                # ¥マーク付きはJPY→USD換算、それ以外はUSD
                if "¥" in part or "円" in part:
                    val = val / JPY_PER_USD
                amount_usd = val
                break
        entries.append({"url": url, "amount_usd": amount_usd})
    return entries

url_entries = parse_url_lines(urls_text)
urls = [e["url"] for e in url_entries]

if url_entries:
    if len(urls) > 30:
        st.warning("30件を超えるURLは最初の30件のみ処理されます")
    st.success(f"✅ {len(urls)} 件のURLを検出しました")

st.divider()

# ── Step 2: 分析実行 ────────────────────────────────────────────────────────────

st.subheader("Step 2　リサーチ開始")

col_btn, col_note = st.columns([1, 3])
with col_btn:
    run = st.button(
        "▶ リサーチ開始",
        type="primary",
        disabled=(len(urls) == 0),
        width="stretch",
    )
with col_note:
    st.markdown(
        "1件あたり約 **30〜60秒** かかります。"
        "ブラウザを閉じないでください。"
    )

if run:
    # APIキー取得
    try:
        api_key = st.secrets["ANTHROPIC_API_KEY"]
    except (KeyError, FileNotFoundError):
        # 失敗を記録して再描画する。以前の正常な結果は消さずに表示を続ける
        sstate.record_failure(
            st.session_state,
            "APIキーが設定されていません。管理者に問い合わせるか、"
            ".streamlit/secrets.toml に ANTHROPIC_API_KEY を設定してください。",
        )
        st.rerun()

    try:
        import anthropic as _anthropic
        client = _anthropic.Anthropic(api_key=api_key)
    except ImportError:
        sstate.record_failure(
            st.session_state,
            "anthropic パッケージがインストールされていません: pip install anthropic",
        )
        st.rerun()

    # Claude API 接続テスト（過負荷時は最大3回リトライ）
    with st.spinner("Claude API 接続確認中..."):
        _last_err = None
        for _attempt in range(3):
            try:
                client.messages.create(
                    model="claude-haiku-4-5-20251001",
                    max_tokens=10,
                    messages=[{"role": "user", "content": "hi"}],
                )
                _last_err = None
                break
            except Exception as _e:
                _last_err = _e
                _is_overload = "529" in str(_e) or "overloaded" in str(_e).lower()
                if _is_overload and _attempt < 2:
                    time.sleep(10)
                else:
                    break
        if _last_err:
            _is_overload = "529" in str(_last_err) or "overloaded" in str(_last_err).lower()
            if _is_overload:
                _msg = ("Claude API が一時的に混雑しています。"
                        "少し待ってから再度「▶ リサーチ開始」を押してください。")
            else:
                _msg = (f"Claude API 接続エラー: {_last_err}／"
                        "Streamlit Cloud の Secrets に ANTHROPIC_API_KEY が"
                        "正しく設定されているか確認してください。")
            sstate.record_failure(st.session_state, _msg)
            st.rerun()

    # 送信者情報をsession_stateに保持してClaude分析に渡す
    _sender_name    = sender_name    if sender_name    else "【氏名】"
    _sender_company = sender_company if sender_company else "【会社名・屋号】"

    targets = url_entries[:30]
    results = []
    errors = []

    progress_bar = st.progress(0, text="分析中...")
    status_text  = st.empty()
    log_area     = st.empty()
    log_lines    = []

    for i, entry in enumerate(targets):
        url = entry["url"]
        manual_amount_usd = entry["amount_usd"]
        short = url[:70] + ("..." if len(url) > 70 else "")

        # プロジェクト情報取得
        log_lines.append(f"[{i+1:02d}] 取得中: {short}")
        log_area.code("\n".join(log_lines[-8:]))

        p = fetch_project_from_url(url)
        # 手動入力の調達額を優先して上書き
        if p and manual_amount_usd > 0:
            p["raised_usd"] = manual_amount_usd
            p["raised_jpy"] = int(manual_amount_usd * JPY_PER_USD)
        if not p:
            errors.append(url)
            log_lines.append(f"      ⚠️ 取得失敗（スキップ）")
            log_area.code("\n".join(log_lines[-8:]))
            progress_bar.progress((i + 1) / len(targets), text="分析中...")
            continue

        if p.get("_from_slug"):
            if "indiegogo" in url:
                platform_label = "IGG"
            elif "zeczec" in url:
                platform_label = "ZECZEC"
            else:
                platform_label = "KS"
            log_lines.append(f"      ⚠️ {platform_label}自動取得不可（URLから商品名推定）: {p.get('name','')[:40]}")
        else:
            _src = p.get("_source", "")
            _src_note = f"（代替経路: {_src}）" if _src else ""
            log_lines.append(f"      ✅ 商品名: {p.get('name','')[:40]}{_src_note}")

        # 公式サイト・連絡先
        # 優先1: KSプロジェクトページの creator.websites（複数URLを全試行）
        creator_websites = p.get("_creator_websites", [])
        if p.get("_official_site") and p["_official_site"] not in creator_websites:
            creator_websites = [p["_official_site"]] + creator_websites

        # 優先2: プロジェクトページのリンクから（cloudscraperでKS対応）
        if not creator_websites:
            found_list = find_official_site(p["url"], p["maker"])
            if found_list:
                creator_websites = found_list
                log_lines.append(f"      🌐 公式サイト取得: {found_list[0][:50]}")
                log_area.code("\n".join(log_lines[-8:]))

        # 優先3: DuckDuckGo検索（creator_slug・メーカー名・商品名で検索）
        _slug = p.get("_creator_slug", "")
        _pname = p.get("name", "")
        _maker = p.get("maker", "")
        log_lines.append(f"      🔎 slug={_slug[:30] or '(なし)'} websites={len(creator_websites)}")
        log_area.code("\n".join(log_lines[-8:]))
        if not creator_websites and (_slug or _pname or _maker):
            log_lines.append(f"      🔍 DDG検索: slug={_slug[:20]!r} maker={_maker[:20]!r}")
            log_area.code("\n".join(log_lines[-8:]))
            found = find_creator_site(_slug, _pname, _maker)
            log_lines.append(f"      🔍 DDG結果: {found[:50] if found else 'なし'}")
            log_area.code("\n".join(log_lines[-8:]))
            if found:
                creator_websites = [found]

        # 優先4 (IGGのみ): 検証済みプロフィールURLをフォールバックとして使用
        if not creator_websites and p.get("platform") == "Indiegogo":
            valid_prof = p.get("_valid_profile_url", "")
            if valid_prof:
                creator_websites = [valid_prof]
                log_lines.append(f"      📋 IGGプロフィール(確認済): {valid_prof[:50]}")
                log_area.code("\n".join(log_lines[-8:]))
            elif _slug:
                # /en/creators/ 形式（2025年の正式URL）をフォールバックとして設定
                fallback_prof = f"https://www.indiegogo.com/en/creators/{_slug}"
                creator_websites = [fallback_prof]
                log_lines.append(f"      📋 IGGクリエイターURL(未確認): {fallback_prof[:60]}")
                log_area.code("\n".join(log_lines[-8:]))

        # IGGプロフィールページから取得したSNSリンクを事前にセット
        igg_profile = p.get("_igg_profile", {})
        contact = {
            "official_url": "", "email": "未確認", "contact_form": "未確認",
            "facebook":  igg_profile.get("facebook",  "未確認"),
            "instagram": igg_profile.get("instagram", "未確認"),
            "linkedin":  igg_profile.get("linkedin",  "未確認"),
        }
        official_url = ""
        for site_url in creator_websites:
            if not site_url:
                continue
            # IGG/KSドメインはスクレイピング不要 → URLだけ保持してスキップ
            if "indiegogo.com" in site_url or "kickstarter.com" in site_url:
                if not official_url:
                    official_url = site_url
                    contact["official_url"] = site_url
                continue
            c = get_contact_info(site_url, brand=_maker or _slug)
            # メール or フォームが見つかった時点で確定
            if c.get("email", "未確認") != "未確認" or c.get("contact_form", "未確認") != "未確認":
                contact = c
                official_url = site_url
                break
            # SNSしか見つからない場合も候補として保持
            if official_url == "":
                contact = c
                official_url = site_url
        if not official_url and creator_websites:
            official_url = creator_websites[0]
            contact["official_url"] = official_url

        if official_url:
            log_lines.append(f"      🌐 公式サイト: {official_url[:50]}")
            if contact.get("email", "未確認") != "未確認":
                log_lines.append(f"      📧 メール: {contact['email']}")

        # Claude 分析
        log_lines.append("      Claude 分析中...")
        log_area.code("\n".join(log_lines[-8:]))
        claude_errors = []
        analysis = analyze_with_claude(p, client, _sender_name, _sender_company, errors=claude_errors)
        if claude_errors:
            log_lines.append(f"      ❌ Claude エラー: {claude_errors[0][:100]}")
        priority = analysis.get("priority", "?")
        log_lines.append(f"      → 優先度: {priority}")
        log_area.code("\n".join(log_lines[-8:]))

        results.append(build_row(p, analysis, contact))
        progress_bar.progress((i + 1) / len(targets), text="分析中...")
        time.sleep(API_WAIT_SEC)

    progress_bar.progress(1.0, text="完了！")
    status_text.empty()
    log_area.empty()

    # 検索が正常終了したときだけ結果を差し替える（途中経過や失敗は保存しない）
    if not sstate.save_success(st.session_state, results, urls,
                               failed_urls=errors, log_lines=log_lines,
                               schema_version=rschema.SCHEMA_VERSION):
        sstate.record_failure(
            st.session_state,
            "分析できた商品が0件でした。URLを確認してください。",
        )

# ── Step 3: 結果表示 ────────────────────────────────────────────────────────────
# 検索結果は session_state から読む。ボタン操作などで再実行されても消えない

_notice = sstate.get_notice(st.session_state)
if _notice:
    st.info(_notice, icon="ℹ️")
    sstate.clear_notice(st.session_state)

_error_msg = sstate.get_error(st.session_state)
if _error_msg:
    st.error(f"⚠️ {_error_msg}")

results   = sstate.get_results(st.session_state)
errors    = st.session_state.get(sstate.FAILED_URLS, [])
log_lines = st.session_state.get(sstate.LOG, [])

# 処理ログを折りたたみで残す（デバッグ用）
if log_lines:
    with st.expander("処理ログ（デバッグ）", expanded=False):
        st.code("\n".join(log_lines))

if errors:
    st.warning(f"取得できなかったURL: {len(errors)}件\n" + "\n".join(errors))

# スラグ推定モードの件数確認
slug_mode_count = sum(1 for r in results if any(
    r.get("調達額(円)", 0) == 0 and r.get("調達額(USD)", 0) == 0
    for _ in [1]
))
if slug_mode_count > 0:
    st.info(
        f"💡 **{slug_mode_count}件** はページから情報を取得できず、**URLから推定**しています。\n\n"
        "この場合、**調達額は0**、**メーカー名はURLからの推定値**です。"
        "メーカー名は正式な社名と異なることがあるため、必ずご自身で確認してください。\n\n"
        "**Indiegogo**: 2025年以降、APIが廃止されデータ取得不可のため調達額は手動入力してください。\n"
        "**Kickstarter**: クラウドサーバーIPがブロックされる場合があります。\n\n"
        "URLの後ろに半角スペース＋金額を追加すると反映されます。\n"
        "例: `https://www.indiegogo.com/projects/xxx  $57,485`",
        icon="💡",
    )

if results:
    import pandas as pd  # 表示・CSV出力に使用（ここで初めて読み込む）

    st.divider()
    st.subheader(f"Step 3　分析結果　（{len(results)} 件）")

    # この結果がどの検索によるものかを明示する
    _executed_at = sstate.get_executed_at(st.session_state)
    _executed_txt = _executed_at.strftime("%Y-%m-%d %H:%M") if _executed_at else "不明"
    if sstate.is_showing_previous(st.session_state):
        st.warning(
            f"⚠️ 下に表示しているのは **前回（{_executed_txt}）の検索結果** です。"
            "直近の検索は失敗したため、結果は置き換わっていません。"
            "CSVもこの前回の結果を出力します。",
            icon="⚠️",
        )
    _col_info, _col_clear = st.columns([4, 1])
    with _col_info:
        st.caption(f"検索日時: {_executed_txt} ／ 検索条件: "
                   f"{sstate.describe_query(st.session_state)}")
    with _col_clear:
        if st.button("🗑 検索結果をクリア", width="stretch",
                     help="表示中の検索結果・検索条件・エラー表示を消します（入力欄のURLは残ります）"):
            sstate.clear_results(st.session_state)
            st.rerun()

    # DataFrame へ変換する境界で行のスキーマをそろえる（表示側で握り潰さない）
    _rows, _missing_cols = rschema.normalize_rows(results)
    if _missing_cols:
        st.warning(
            "表示中の検索結果に不足していた項目を、既定値で補いました: "
            + "、".join(_missing_cols)
            + "。正確な内容は再検索してご確認ください。",
            icon="⚠️",
        )
    df = pd.DataFrame(_rows, columns=rschema.required_columns())

    # 優先度サマリー
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.metric("合計", f"{len(df)}件")
    with c2:
        cnt_a = int((df["優先度"] == "A").sum())
        st.metric("優先度 A 🟢", f"{cnt_a}件", help="コンセプト強・日本未展開・Makuake向き")
    with c3:
        cnt_b = int((df["優先度"] == "B").sum())
        st.metric("優先度 B 🟡", f"{cnt_b}件", help="良品だが日本展開済みの可能性あり")
    with c4:
        cnt_c = int((df["優先度"] == "C").sum())
        st.metric("優先度 C 🔴", f"{cnt_c}件", help="規制・価格・競合に懸念あり")

    st.markdown("---")

    # サマリーテーブル（主要カラムのみ）
    def best_contact_type(row):
        if row["メールアドレス"] not in ("未確認", ""):
            return "📧 メール"
        if row["問い合わせフォームURL"] not in ("未確認", ""):
            return "📝 フォーム"
        if row["LinkedIn"] not in ("未確認", ""):
            return "💼 LinkedIn"
        if row["Facebook"] not in ("未確認", ""):
            return "📘 Facebook"
        if row["Instagram"] not in ("未確認", ""):
            return "📷 Instagram"
        if row["公式サイトURL"] not in ("", "未確認"):
            return "🌐 公式サイト"
        if row["掲載URL"] not in ("", "未確認"):
            plat = row.get("プラットフォーム", "")
            if plat == "Indiegogo":
                return "📋 IGGページ"
            elif plat == "ZECZEC":
                return "📋 ZECZECページ"
            else:
                return "📋 KSページ"
        return "—"

    def best_contact_url(row):
        if row["メールアドレス"] not in ("未確認", ""):
            return "mailto:" + row["メールアドレス"]
        if row["問い合わせフォームURL"] not in ("未確認", ""):
            return row["問い合わせフォームURL"]
        if row["LinkedIn"] not in ("未確認", ""):
            return row["LinkedIn"]
        if row["Facebook"] not in ("未確認", ""):
            return row["Facebook"]
        if row["Instagram"] not in ("未確認", ""):
            return row["Instagram"]
        if row["公式サイトURL"] not in ("", "未確認"):
            return row["公式サイトURL"]
        # 最終フォールバック: プロジェクトページ（必ず存在する）
        return row.get("掲載URL", "")

    # 正規化済みなので通常はすべて揃っている。想定外の欠落でページごと落とさないよう、
    # 列の選択は KeyError を投げない reindex で行う（原因は上の警告で明示する）
    summary_df = df.reindex(columns=rschema.DISPLAY_COLUMNS).copy()
    summary_df["種別"] = df.apply(best_contact_type, axis=1)
    summary_df["アプローチ先リンク"] = df.apply(best_contact_url, axis=1)

    st.markdown("**サマリー（主要項目）**")
    st.caption(
        "優先度 A → B → C の順に並んでいます。"
        "並び替え・コピーをしたい場合は下の「全カラムを表示」の表をご利用ください。"
    )
    st.caption(
        "⚠ 参考値 … 調達額や説明文を自動取得できなかった商品です。"
        "判定材料が少ないため、同じ商品でも判定が変わることがあります。"
        "URLの後ろに半角スペース＋調達額を貼り付けて再実行すると精度が上がります。"
    )

    # st.dataframe は長文を折り返せないため、理由を全文表示できるHTMLテーブルで描画する
    st.markdown(
        build_summary_html(summary_df.to_dict("records")),
        unsafe_allow_html=True,
    )

    # 全カラム展開表示
    with st.expander("全カラムを表示"):
        st.dataframe(df, width="stretch", hide_index=True)

    # 営業メール一覧（全商品。優先度A→B→Cの順で表示）
    _badge = {"A": "🟢", "B": "🟡", "C": "🔴"}
    df_mail = df.sort_values("優先度")
    with st.expander(f"✉️ 営業メール文（全{len(df_mail)}件）", expanded=True):
        st.caption(
            "署名が [Your Name] の場合は、ご自身のお名前に差し替えてご利用ください。"
            "宛名が [Brand / Team Name] の場合も同様に差し替えてください。"
        )
        for _, row in df_mail.iterrows():
            mark = _badge.get(row["優先度"], "")
            st.markdown(f"**{mark} {row['商品名']}** — {row['メーカー名']}")
            st.markdown(f"To: `{row['メールアドレス']}`")
            st.markdown(f"**Subject:** {row['営業メール件名(英語)']}")
            st.code(row["営業メール本文(英語)"], language=None)
            st.divider()

    # CSV ダウンロード
    csv_bytes = df.to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig")
    # 現在時刻ではなく、表示中の結果を取得した日時をファイル名にする
    ts = sstate.timestamp_label(st.session_state)
    st.download_button(
        label="📥 CSVダウンロード（Google スプレッドシート対応）",
        data=csv_bytes,
        file_name=f"crowdfunding_research_{ts}.csv",
        mime="text/csv",
        type="primary",
    )
# ── フッター ────────────────────────────────────────────────────────────────────

st.divider()
st.markdown(
    "<small>© クラファン物販スクール　本ツールはスクール受講生専用です　｜　ver 2026-06-04</small>",
    unsafe_allow_html=True,
)
