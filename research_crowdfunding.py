"""
海外クラファン商品リサーチツール
Kickstarter・Indiegogo から日本販売可能な商品をリサーチし、CSV出力する

Usage:
    python research_crowdfunding.py           # デフォルト(30件)
    python research_crowdfunding.py --limit 50  # 取得件数を増やす
    python research_crowdfunding.py --no-claude # Claude分析をスキップ（高速）
"""

import argparse
import csv
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import requests
from bs4 import BeautifulSoup

try:
    import cloudscraper as _cloudscraper
    def _ks_session():
        return _cloudscraper.create_scraper()
except ImportError:
    _cloudscraper = None
    def _ks_session():
        return _session()

try:
    import anthropic as _anthropic
except ImportError:
    _anthropic = None

# ───────────────────────────────────────────────────────────────────────────────
# 設定
# ───────────────────────────────────────────────────────────────────────────────

JPY_PER_USD    = 150.0
MIN_RAISED_USD = 333_000      # ¥50M 相当
MAX_RAISED_USD = 2_000_000    # ¥300M 相当
API_WAIT_SEC   = 1.5          # スクレイピング間隔（礼儀）
MODEL_ID       = "claude-haiku-4-5-20251001"

# Kickstarter カテゴリID（規制品を除外済み）
KS_CATEGORIES = {
    "Product Design": 44,
    "Technology":     16,
    "Fashion":         9,
    "Design":          7,
    "Photography":    14,
}

# 除外キーワード（タイトル＋説明に含まれる場合スキップ）
EXCLUDE_KEYWORDS = [
    "food", "drink", "supplement", "vitamin", "medical", "drug", "health claim",
    "cannabis", "cbd", "alcohol", "wine", "beer", "spirits", "whiskey",
    "cosmetic", "skincare", "beauty", "makeup", "serum",
    "firearm", "weapon", "gun", "explosive", "knife combat",
    "vape", "tobacco", "cigarette",
    "游戏", "manga", "comic book", "graphic novel",
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9,ja;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Referer": "https://www.kickstarter.com/",
}

# ───────────────────────────────────────────────────────────────────────────────
# API キー読み込み
# ───────────────────────────────────────────────────────────────────────────────

def _load_api_key() -> str:
    candidates = [
        Path(__file__).parent / ".env",
        Path(__file__).parent.parent / "ebay_walkman_listing" / ".env",
        Path(__file__).parent.parent / "ebay_handycam_listing" / ".env",
        Path.home() / ".claude" / ".env",
    ]
    for c in candidates:
        if c.exists():
            for line in c.read_text(encoding="utf-8").splitlines():
                if line.startswith("ANTHROPIC_API_KEY="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    return os.environ.get("ANTHROPIC_API_KEY", "")

# ───────────────────────────────────────────────────────────────────────────────
# ユーティリティ
# ───────────────────────────────────────────────────────────────────────────────

def _has_exclude_keyword(text: str) -> bool:
    t = text.lower()
    return any(kw in t for kw in EXCLUDE_KEYWORDS)


def _in_range(usd: float) -> bool:
    return MIN_RAISED_USD <= usd <= MAX_RAISED_USD


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update(HEADERS)
    return s

# ───────────────────────────────────────────────────────────────────────────────
# Kickstarter スクレイピング
# ───────────────────────────────────────────────────────────────────────────────

def search_kickstarter(limit: int = 30) -> List[Dict]:
    """Kickstarterの成功プロジェクトをカテゴリ別に取得"""
    results: List[Dict] = []
    sess = _session()

    for cat_name, cat_id in KS_CATEGORIES.items():
        if len(results) >= limit:
            break
        try:
            url = (
                "https://www.kickstarter.com/projects/search.json"
                f"?term=&category_id={cat_id}&sort=most_funded"
                "&state=successful&page=1"
            )
            resp = sess.get(url, timeout=15)
            if resp.status_code != 200:
                print(f"  [KS] {cat_name}: HTTP {resp.status_code}")
                time.sleep(API_WAIT_SEC)
                continue

            data = resp.json()
            projects = data.get("projects", [])
            print(f"  [KS] {cat_name}: {len(projects)}件取得")

            for p in projects:
                pledged = float(p.get("pledged", 0) or 0)
                if not _in_range(pledged):
                    continue
                title = p.get("name", "")
                blurb = p.get("blurb", "")
                if _has_exclude_keyword(title + " " + blurb):
                    continue

                creator = p.get("creator", {}) or {}
                urls    = p.get("urls", {}) or {}
                web     = urls.get("web", {}) or {}

                results.append({
                    "platform":    "Kickstarter",
                    "name":        title,
                    "maker":       creator.get("name", ""),
                    "url":         "https://www.kickstarter.com" + web.get("project", ""),
                    "raised_usd":  pledged,
                    "raised_jpy":  int(pledged * JPY_PER_USD),
                    "backers":     int(p.get("backers_count", 0) or 0),
                    "genre":       cat_name,
                    "description": blurb,
                    "goal_usd":    float(p.get("goal", 0) or 0),
                    "country":     p.get("country", ""),
                })

            time.sleep(API_WAIT_SEC)

        except Exception as e:
            print(f"  [KS] {cat_name}: エラー: {e}")
            time.sleep(API_WAIT_SEC)

    return results

# ───────────────────────────────────────────────────────────────────────────────
# Indiegogo スクレイピング
# ───────────────────────────────────────────────────────────────────────────────

def search_indiegogo(limit: int = 20) -> List[Dict]:
    """Indiegogoのトレンドプロジェクトを取得"""
    results: List[Dict] = []
    sess = _session()

    # Indiegogo のパブリック API エンドポイント（認証不要）
    endpoints = [
        "https://www.indiegogo.com/private_api/funding_search?q=&per_page=50&page=1&order=trending&percent_funded_min=100",
        "https://www.indiegogo.com/private_api/funding_search?q=&per_page=50&page=1&order=most_funded&percent_funded_min=100",
    ]

    for url in endpoints:
        if len(results) >= limit:
            break
        try:
            resp = sess.get(url, timeout=15)
            if resp.status_code != 200:
                continue

            data = resp.json()
            # レスポンス構造が変わる場合があるため複数パターンに対応
            campaigns = (
                data.get("response", {}).get("hits", {}).get("hits", [])
                or data.get("hits", {}).get("hits", [])
                or data.get("campaigns", [])
            )

            for c in campaigns:
                src = c.get("_source", c)  # _source があればその中、なければ直接
                raised = float(src.get("collected_funds", 0) or 0)
                if not _in_range(raised):
                    continue

                title = src.get("title", "")
                blurb = src.get("tagline", "")
                if _has_exclude_keyword(title + " " + blurb):
                    continue

                slug = src.get("url_slug", src.get("slug", ""))
                results.append({
                    "platform":    "Indiegogo",
                    "name":        title,
                    "maker":       src.get("owner_name", src.get("owner", {}).get("name", "")),
                    "url":         f"https://www.indiegogo.com/projects/{slug}" if slug else "",
                    "raised_usd":  raised,
                    "raised_jpy":  int(raised * JPY_PER_USD),
                    "backers":     int(src.get("contributions_count", 0) or 0),
                    "genre":       src.get("category", src.get("category_name", "")),
                    "description": blurb,
                    "goal_usd":    float(src.get("goal_amount", 0) or 0),
                    "country":     src.get("country_code", ""),
                })

            time.sleep(API_WAIT_SEC)

        except Exception as e:
            print(f"  [IGG] エラー: {e}")

    return results[:limit]

# ───────────────────────────────────────────────────────────────────────────────
# 公式サイト URL を探す
# ───────────────────────────────────────────────────────────────────────────────

def find_official_site(project_url: str, maker_name: str) -> list:
    """クラファンページからメーカー公式サイト URL リストを返す（複数対応）"""
    if not project_url.startswith("http"):
        return []

    # KickstarterはCloudflare対策のためcloudscraperを使用
    is_ks = "kickstarter.com" in project_url
    scraper = _ks_session() if is_ks else _session()

    try:
        resp = scraper.get(project_url, timeout=15, allow_redirects=True)
        if resp.status_code != 200:
            return []

        soup = BeautifulSoup(resp.text, "html.parser")

        # KS: data-initial の creator.websites が最も信頼性が高い
        if is_ks:
            for tag in soup.find_all(attrs={"data-initial": True}):
                try:
                    data = json.loads(tag["data-initial"])
                    proj = data.get("project")
                    if not isinstance(proj, dict):
                        continue
                    creator_data = proj.get("creator", {}) or {}
                    websites = creator_data.get("websites", [])
                    if isinstance(websites, list) and websites:
                        return [w.get("url", "") for w in websites if w.get("url")]
                except Exception:
                    continue

        # Aタグから外部リンクを探す（IGG・KSのフォールバック）
        maker_word = maker_name.lower().split()[0] if maker_name else ""
        found = []
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if not href.startswith("http"):
                continue
            if any(skip in href for skip in ["kickstarter.com", "indiegogo.com",
                                              "facebook.com", "instagram.com",
                                              "twitter.com", "youtube.com", "linkedin.com"]):
                continue
            link_text = a.get_text(strip=True).lower()
            if any(kw in link_text or kw in href.lower()
                   for kw in ["website", "official", "learn more", "visit us", maker_word]):
                found.append(href)
        return found[:3]

    except Exception:
        pass
    return []

# ───────────────────────────────────────────────────────────────────────────────
# 連絡先取得
# ───────────────────────────────────────────────────────────────────────────────

def get_contact_info(official_url: str) -> Dict:
    """公式サイトから連絡先情報を取得"""
    info = {
        "official_url":   official_url,
        "email":          "未確認",
        "contact_form":   "未確認",
        "facebook":       "未確認",
        "instagram":      "未確認",
        "linkedin":       "未確認",
    }
    if not official_url or not official_url.startswith("http"):
        return info

    try:
        sess = _session()
        resp = sess.get(official_url, timeout=10)
        if resp.status_code not in (200, 404):  # Shopifyは404でもフッターを返すため含める
            return info

        soup = BeautifulSoup(resp.text, "html.parser")
        text = resp.text

        # メールアドレス抽出
        emails = re.findall(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}", text)
        _BAD_EMAIL_KW = [
            "noreply", "no-reply", "example", "test", "privacy",
            "sentry", "wix", "kickofflabs", "mailchimp", "sendgrid",
            "klaviyo", "hubspot", "zendesk",
        ]
        for email in emails:
            if not any(kw in email.lower() for kw in _BAD_EMAIL_KW):
                info["email"] = email
                break

        # コンタクトフォーム / 問い合わせページ
        contact_kws = ["contact", "wholesale", "distributor", "press", "partner", "inquiry", "about"]
        for a in soup.find_all("a", href=True):
            href = a["href"]
            text_content = a.get_text(strip=True).lower()
            if any(kw in href.lower() or kw in text_content for kw in contact_kws):
                full = href if href.startswith("http") else official_url.rstrip("/") + "/" + href.lstrip("/")
                info["contact_form"] = full
                break

        # contact / about ページを追加でチェック（Shopify 404ページも含む）
        SUB_PAGES = ["/contact", "/pages/contact", "/about", "/pages/about",
                     "/contact-us", "/pages/contact-us", "/pages/wholesale"]
        for sub in SUB_PAGES:
            if info["email"] != "未確認" and info["instagram"] != "未確認":
                break
            try:
                sub_url = official_url.rstrip("/") + sub
                sub_resp = sess.get(sub_url, timeout=8)
                if sub_resp.status_code not in (200, 404):
                    continue
                sub_soup = BeautifulSoup(sub_resp.text, "html.parser")

                # メール探索
                if info["email"] == "未確認":
                    sub_emails = re.findall(
                        r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}",
                        sub_resp.text
                    )
                    for email in sub_emails:
                        if not any(kw in email.lower() for kw in _BAD_EMAIL_KW + ["shopify", ".png"]):
                            info["email"] = email
                            break

                # SNS探索（トップで見つからなかった場合）
                for a in sub_soup.find_all("a", href=True):
                    href = a["href"]
                    if "facebook.com" in href and info["facebook"] == "未確認":
                        info["facebook"] = href
                    elif "instagram.com" in href and info["instagram"] == "未確認":
                        info["instagram"] = href
                    elif "linkedin.com/company" in href and info["linkedin"] == "未確認":
                        info["linkedin"] = href
            except Exception:
                continue

        # SNS アカウント
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if "facebook.com" in href and info["facebook"] == "未確認":
                info["facebook"] = href
            elif "instagram.com" in href and info["instagram"] == "未確認":
                info["instagram"] = href
            elif "linkedin.com/company" in href and info["linkedin"] == "未確認":
                info["linkedin"] = href

        time.sleep(API_WAIT_SEC)

    except Exception:
        pass

    return info

# ───────────────────────────────────────────────────────────────────────────────
# Claude API 分析
# ───────────────────────────────────────────────────────────────────────────────

_TRACK_RECORD = """\
【スクール運営会社の実績（信頼性の根拠として活用）】
- VACOS CAM（AIスマート防犯カメラ）: CAMPFIRE調達 ¥23,403,000 / 612人支援 / 目標の7,801%達成
- VACOS CAM IR（AI人体検知カメラ）: CAMPFIRE調達 ¥11,149,912 / 343人支援 / 目標の5,574%達成
- 合計調達実績: ¥34,000,000以上
- 実績プラットフォーム: CAMPFIRE・Makuake（日本最大級のクラウドファンディング）
- 専門: 海外優良製品の日本市場独占展開・Makuakeプロデュース"""


def _build_company_profile(sender_name: str = "", sender_company: str = "") -> str:
    name    = sender_name    if sender_name    else "【氏名】"
    company = sender_company if sender_company else "【会社名・屋号】"
    return f"""\
【送信者情報】
氏名: {name}
会社名/屋号: {company}

{_TRACK_RECORD}

※営業メール本文では、送信者を「{name} from {company}」として紹介してください。
　スクール運営会社の実績（VACOS CAM ¥23M調達など）は
　「私が所属するクラファンスクールの運営実績」として自然に引用し、
　信頼性の根拠として使用してください。"""

_ANALYSIS_PROMPT = """\
以下の海外クラファン商品について、日本市場向け分析をJSONのみで返してください。
※情報が少ない場合も、商品名・URLから推測して分析してください。

【商品情報】
商品名: {name}
メーカー: {maker}
プラットフォーム: {platform}
調達額: ${raised_usd:,.0f} (約{raised_jpy:,}円)
支援者数: {backers:,}人
ジャンル: {genre}
URL: {url}
説明: {description}

{company_profile}

【出力JSON】
{{
  "japanese_market_reason": "日本で売れそうな理由（2〜3文）",
  "appeal_points": "日本販売時の訴求ポイント（箇条書き2〜3点）",
  "japanese_competitors": "競合する日本商品（なければ「特になし」）",
  "priority": "A / B / C のいずれか1文字のみ",
  "priority_reason": "優先度の理由（1〜2文）",
  "concerns": "注意点・懸念点（1〜2点）",
  "approach_subject": "英語営業メール件名（1行）",
  "approach_body": "以下の構成で英語ビジネスメール本文を書いてください（6〜8文）:\\n1. 自己紹介（Base on Base LLC・日本のクラファン専門会社）\\n2. 実績（VACOS CAMで¥23M調達・7,801%達成など具体的数字を含める）\\n3. 貴社製品への関心と日本市場のポテンシャル\\n4. 提案（日本独占販売権またはMakuakeでのローンチ支援）\\n5. 次のステップ（ビデオ通話の提案）"
}}

優先度基準：
A＝コンセプト強・日本未展開・規制リスク低・Makuake向き
B＝良品だが日本展開済みの可能性あり・競合多め
C＝面白いが規制・価格・物流・競合に懸念あり"""


def analyze_with_claude(project: Dict, client, sender_name: str = "", sender_company: str = "",
                        errors: list = None) -> Dict:
    defaults = {
        "japanese_market_reason": "（Claude未接続のため省略）",
        "appeal_points":          "（Claude未接続のため省略）",
        "japanese_competitors":   "未確認",
        "priority":               "B",
        "priority_reason":        "（Claude未接続のため省略）",
        "concerns":               "未確認",
        "approach_subject":       "",
        "approach_body":          "",
    }
    if client is None:
        return defaults

    try:
        prompt = _ANALYSIS_PROMPT.format(
            name=project.get("name", ""),
            maker=project.get("maker", ""),
            platform=project.get("platform", ""),
            raised_usd=float(project.get("raised_usd", 0) or 0),
            raised_jpy=int(project.get("raised_jpy", 0) or 0),
            backers=int(project.get("backers", 0) or 0),
            genre=project.get("genre", ""),
            url=project.get("url", ""),
            description=str(project.get("description", ""))[:400].replace("{", "｛").replace("}", "｝"),
            company_profile=_build_company_profile(sender_name, sender_company),
        )
    except Exception as e:
        msg = f"プロンプト生成エラー: {e}"
        print(f"  [Claude] {msg}")
        if errors is not None:
            errors.append(msg)
        return defaults

    last_err = None
    for attempt in range(3):
        try:
            response = client.messages.create(
                model=MODEL_ID,
                max_tokens=1024,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = response.content[0].text.strip()
            m = re.search(r"```json\s*([\s\S]+?)\s*```", raw)
            json_str = m.group(1) if m else raw[raw.find("{"):raw.rfind("}")+1]
            return {**defaults, **json.loads(json_str)}
        except Exception as e:
            last_err = e
            is_overload = "529" in str(e) or "overloaded" in str(e).lower()
            if is_overload and attempt < 2:
                time.sleep(15)
                continue
            break
    msg = f"{type(last_err).__name__}: {last_err}"
    print(f"  [Claude] エラー: {msg}")
    if errors is not None:
        errors.append(msg)
    return defaults

# ───────────────────────────────────────────────────────────────────────────────
# CSV 出力定義
# ───────────────────────────────────────────────────────────────────────────────

CSV_FIELDS = [
    "商品名", "メーカー名", "掲載URL", "プラットフォーム",
    "調達額(円)", "調達額(USD)", "支援者数",
    "商品ジャンル", "商品の特徴",
    "日本で売れそうな理由", "日本販売時の訴求ポイント", "競合する日本商品",
    "公式サイトURL", "メールアドレス", "問い合わせフォームURL",
    "Facebook", "Instagram", "LinkedIn",
    "優先度", "優先度の理由", "注意点・懸念点",
    "営業メール件名(英語)", "営業メール本文(英語)",
]


def build_row(p: Dict, analysis: Dict, contact: Dict) -> Dict:
    return {
        "商品名":              p.get("name", ""),
        "メーカー名":          p.get("maker", ""),
        "掲載URL":             p.get("url", ""),
        "プラットフォーム":    p.get("platform", ""),
        "調達額(円)":          p.get("raised_jpy", 0),
        "調達額(USD)":         round(p.get("raised_usd", 0), 0),
        "支援者数":            p.get("backers", 0),
        "商品ジャンル":        p.get("genre", ""),
        "商品の特徴":          str(p.get("description", ""))[:300],
        "日本で売れそうな理由":       analysis.get("japanese_market_reason", ""),
        "日本販売時の訴求ポイント":   analysis.get("appeal_points", ""),
        "競合する日本商品":           analysis.get("japanese_competitors", ""),
        "公式サイトURL":       contact.get("official_url", ""),
        "メールアドレス":      contact.get("email", "未確認"),
        "問い合わせフォームURL": contact.get("contact_form", "未確認"),
        "Facebook":            contact.get("facebook", "未確認"),
        "Instagram":           contact.get("instagram", "未確認"),
        "LinkedIn":            contact.get("linkedin", "未確認"),
        "優先度":              analysis.get("priority", ""),
        "優先度の理由":        analysis.get("priority_reason", ""),
        "注意点・懸念点":      analysis.get("concerns", ""),
        "営業メール件名(英語)": analysis.get("approach_subject", ""),
        "営業メール本文(英語)": analysis.get("approach_body", ""),
    }

# ───────────────────────────────────────────────────────────────────────────────
# URL から直接プロジェクト情報を取得
# ───────────────────────────────────────────────────────────────────────────────

_KS_TAB_SUFFIXES = (
    "/creator", "/description", "/updates", "/comments",
    "/community", "/faqs", "/risks", "/rewards",
)

def _clean_ks_url(url: str) -> str:
    """クエリパラメータ・タブパスを除去してプロジェクト基本URLを返す"""
    base = url.split("?")[0].rstrip("/")
    for tab in _KS_TAB_SUFFIXES:
        if base.endswith(tab):
            base = base[:-len(tab)]
            break
    return base


def fetch_kickstarter_project(url: str) -> Optional[Dict]:
    """Kickstarter プロジェクトページからデータを取得"""
    base_url = _clean_ks_url(url)
    creator_slug = _extract_creator_from_ks_url(base_url)
    product_slug = base_url.rstrip("/").split("/")[-1]

    # ── 1. 検索API + cloudscraper（pledged取得の最優先手段）──────────
    scraper = _ks_session()
    words = [w for w in product_slug.replace("-", " ").split() if len(w) > 2]
    # 短すぎず長すぎない検索語を複数試す（2語→3語の順）
    search_candidates = []
    if len(words) >= 2:
        search_candidates.append(" ".join(words[:2]))
    if len(words) >= 3:
        search_candidates.append(" ".join(words[:3]))
    if creator_slug:
        search_candidates.append(creator_slug)

    def _ks_search_match(term: str):
        """検索APIで term を検索し creator_slug または product_slug がマッチする最初のプロジェクトを返す"""
        try:
            url = (
                "https://www.kickstarter.com/projects/search.json"
                f"?term={requests.utils.quote(term)}&sort=most_funded&page=1"
            )
            r = scraper.get(url, timeout=15)
            if r.status_code != 200 or not r.text.strip():
                return None
            for proj in r.json().get("projects", []):
                proj_url = (proj.get("urls", {}) or {}).get("web", {}).get("project", "")
                # creator_slug（文字列・数値ID両方）でマッチ
                if creator_slug and creator_slug in proj_url:
                    return proj
                # product_slug の先頭15文字でマッチ
                if product_slug[:15] and product_slug[:15] in proj_url:
                    return proj
                # creator のID（数値）がURLに含まれているかチェック
                creator_id = str((proj.get("creator", {}) or {}).get("id", ""))
                if creator_id and creator_id in proj_url and product_slug[:10] in proj_url:
                    return proj
        except Exception as e:
            print(f"  [KS] 検索API エラー ({term!r}): {e}")
        return None

    def _ks_creator_websites(page_url: str) -> list:
        """プロジェクトページの data-initial から creator.websites を全件取得して返す"""
        try:
            r = scraper.get(page_url, timeout=15, allow_redirects=True)
            if r.status_code != 200:
                return []
            soup = BeautifulSoup(r.text, "html.parser")
            for tag in soup.find_all(attrs={"data-initial": True}):
                try:
                    data = json.loads(tag["data-initial"])
                    proj_data = data.get("project", data)
                    creator_data = proj_data.get("creator", {}) or {}
                    websites = creator_data.get("websites", [])
                    if isinstance(websites, list):
                        return [w.get("url", "") for w in websites if w.get("url")]
                except Exception:
                    continue
        except Exception:
            pass
        return []

    try:
        matched = None
        for term in search_candidates:
            matched = _ks_search_match(term)
            if matched:
                break
        if matched:
            proj = matched
            proj_web_url = (proj.get("urls", {}) or {}).get("web", {}).get("project", "")
            pledged = float(proj.get("pledged", 0) or 0)
            creator = (proj.get("creator", {}) or {})
            cat     = proj.get("category", {})
            genre   = cat.get("name", "Technology") if isinstance(cat, dict) else "Technology"
            final_url = proj_web_url if proj_web_url.startswith("http") else base_url
            # プロジェクトページから creator.websites を全件取得
            creator_websites = _ks_creator_websites(final_url)
            # 数値IDはmaker名として不適切なので除外
            maker_name = creator.get("name", "") or creator_slug
            if str(maker_name).isdigit():
                maker_name = creator_slug
            return {
                "platform":           "Kickstarter",
                "name":               proj.get("name", ""),
                "maker":              maker_name,
                "url":                final_url,
                "raised_usd":         pledged,
                "raised_jpy":         int(pledged * JPY_PER_USD),
                "backers":            int(proj.get("backers_count", 0) or 0),
                "genre":              genre,
                "description":        proj.get("blurb", ""),
                "goal_usd":           float(proj.get("goal", 0) or 0),
                "country":            proj.get("country", ""),
                "_creator_slug":      creator_slug,
                "_creator_websites":  creator_websites,  # 複数URLリスト
                "_official_site":     creator_websites[0] if creator_websites else "",
            }
    except Exception as e:
        print(f"  [KS] 検索マッチ エラー: {e}")

    # ── 2. cloudscraperでHTMLスクレイピング（フォールバック）────────
    scraper = _ks_session()
    try:
        resp = scraper.get(base_url, timeout=20, allow_redirects=True)
        if resp.status_code != 200:
            print(f"  [KS] HTTP {resp.status_code}: {base_url}")
            return None

        final_url = resp.url
        soup = BeautifulSoup(resp.text, "html.parser")

        for tag in soup.find_all(attrs={"data-initial": True}):
            try:
                data = json.loads(tag["data-initial"])
                # "project" キーが dict として存在するものだけ使用（creator profileなど他要素を除外）
                proj = data.get("project")
                if not isinstance(proj, dict) or not proj.get("name"):
                    continue
                name = proj["name"]
                pledged_raw = proj.get("pledged", {})
                if isinstance(pledged_raw, dict):
                    pledged = float(pledged_raw.get("amount", 0) or 0)
                else:
                    pledged = float(pledged_raw or 0)
                creator_data = proj.get("creator", {}) or {}
                maker = creator_data.get("name", "") or creator_slug
                # 数値IDはmaker名として不適切なので除外
                if str(maker).isdigit():
                    maker = creator_slug
                websites = creator_data.get("websites", [])
                official_site = websites[0].get("url", "") if isinstance(websites, list) and websites else ""
                creator_websites = [w.get("url", "") for w in websites if isinstance(w, dict) and w.get("url")] if isinstance(websites, list) else []
                return {
                    "platform":          "Kickstarter",
                    "name":              name,
                    "maker":             maker,
                    "url":               final_url,
                    "raised_usd":        pledged,
                    "raised_jpy":        int(pledged * JPY_PER_USD),
                    "backers":           int(proj.get("backersCount", 0) or 0),
                    "genre":             "Technology",
                    "description":       proj.get("description", ""),
                    "goal_usd":          0,
                    "country":           "",
                    "_creator_slug":     creator_slug,
                    "_official_site":    official_site,
                    "_creator_websites": creator_websites,
                }
            except Exception:
                continue

        title_tag = soup.find("meta", property="og:title")
        name = title_tag["content"] if title_tag else (soup.title.string if soup.title else "")
        blurb = (soup.find("meta", property="og:description") or {}).get("content", "")
        if name:
            return {
                "platform": "Kickstarter", "name": name, "maker": creator_slug,
                "url": final_url, "raised_usd": 0, "raised_jpy": 0,
                "backers": 0, "genre": "Technology",
                "description": blurb, "goal_usd": 0, "country": "",
                "_creator_slug": creator_slug,
            }

    except Exception as e:
        print(f"  [KS] ページ取得エラー: {e}")

    return None


def _igg_profile_links(profile_url: str, maker: str = "", sess=None) -> dict:
    """Indiegogoクリエイタープロフィールページから公式サイト・SNSリンクを取得"""
    if not profile_url:
        return {}
    if sess is None:
        sess = _session()
    _SKIP = ("indiegogo.com", "facebook.com/sharer", "twitter.com/intent",
             "linkedin.com/share", "plus.google.com")
    try:
        r = sess.get(profile_url, timeout=12, allow_redirects=True)
        if r.status_code != 200:
            return {}
        soup = BeautifulSoup(r.text, "html.parser")
        links = {}
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if not href.startswith("http"):
                continue
            if any(s in href for s in _SKIP):
                continue
            if "instagram.com" in href:
                links.setdefault("instagram", href)
            elif "youtube.com" in href:
                links.setdefault("youtube", href)
            elif "facebook.com" in href:
                links.setdefault("facebook", href)
            elif "twitter.com" in href or "x.com" in href:
                links.setdefault("twitter", href)
            elif "linkedin.com" in href:
                links.setdefault("linkedin", href)
            else:
                # 公式サイト（indiegogo以外の外部リンク）
                links.setdefault("website", href)
        return links
    except Exception:
        return {}


def _parse_igg_campaign(camp: dict, url: str) -> Optional[Dict]:
    """Indiegogo キャンペーンdictからプロジェクト情報を抽出"""
    name = camp.get("title", "") or camp.get("name", "")
    if not name:
        return None
    raised = float(
        camp.get("collected_funds") or camp.get("amount_raised") or
        camp.get("funds_raised_amount") or camp.get("raised_amount") or 0
    )
    backers = int(
        camp.get("contributions_count") or camp.get("backers_count") or
        camp.get("contribution_count") or 0
    )
    owner = camp.get("owner") or camp.get("team") or {}
    if isinstance(owner, dict):
        maker = owner.get("name") or owner.get("display_name") or ""
    else:
        maker = ""
    return {
        "platform":    "Indiegogo",
        "name":        name,
        "maker":       maker,
        "url":         url,
        "raised_usd":  raised,
        "raised_jpy":  int(raised * JPY_PER_USD),
        "backers":     backers,
        "genre":       camp.get("category_name") or camp.get("category") or "",
        "description": camp.get("tagline") or camp.get("short_description") or "",
        "goal_usd":    float(camp.get("goal_amount") or camp.get("goal") or 0),
        "country":     camp.get("country_code") or camp.get("country") or "",
    }


def _extract_igg_slugs(url: str):
    """IndiegogoのURLからプロジェクトスラグとクリエイタースラグを抽出する
    旧形式: /projects/{project-slug}
    新形式: /en/projects/{creator-slug}/{project-slug}
    """
    # 新形式: /projects/{creator}/{project} or /en/projects/{creator}/{project}
    m = re.search(r"indiegogo\.com(?:/en)?/projects/([^/#?]+)/([^/#?]+)", url)
    if m:
        return m.group(2), m.group(1)  # project_slug, creator_slug
    # 旧形式: /projects/{project-slug}
    m = re.search(r"indiegogo\.com(?:/en)?/projects/([^/#?]+)", url)
    if m:
        return m.group(1), ""
    return "", ""


def fetch_indiegogo_project(url: str) -> Optional[Dict]:
    """Indiegogo プロジェクトページからデータを取得"""
    sess = _session()
    project_slug, creator_slug = _extract_igg_slugs(url)
    slug = project_slug  # private_api用（プロジェクトスラグ）

    # ── 1. private_api エンドポイント（複数パターン）────────────────
    if slug:
        api_candidates = [
            f"https://www.indiegogo.com/private_api/campaigns/{slug}",
            f"https://www.indiegogo.com/private_api/campaigns/{slug}/basics",
        ]
        for api_url in api_candidates:
            try:
                resp = sess.get(api_url, timeout=12)
                if resp.status_code != 200:
                    continue
                data = resp.json()
                # レスポンス構造: {"response": {...}} または直接 dict
                camp = data.get("response") or data
                if isinstance(camp, dict):
                    result = _parse_igg_campaign(camp, url)
                    if result:
                        return result
            except Exception as e:
                print(f"  [IGG] private_api ({api_url[-30:]}): {e}")

    # ── 2. HTMLスクレイピング ────────────────────────────────────────
    try:
        resp = sess.get(url, timeout=15)
        if resp.status_code != 200:
            return None
        html = resp.text
        soup = BeautifulSoup(html, "html.parser")

        # 2a. Next.js __NEXT_DATA__ （最近のIGG構造）
        next_tag = soup.find("script", id="__NEXT_DATA__")
        if next_tag and next_tag.string:
            try:
                ndata = json.loads(next_tag.string)
                # props.pageProps.campaign / props.pageProps.data.campaign
                pp = ndata.get("props", {}).get("pageProps", {})
                camp = (pp.get("campaign") or pp.get("data", {}).get("campaign")
                        or pp.get("project") or {})
                if camp:
                    result = _parse_igg_campaign(camp, url)
                    if result:
                        return result
            except Exception:
                pass

        # 2b. JSON-LD structured data
        for ld in soup.find_all("script", type="application/ld+json"):
            try:
                obj = json.loads(ld.string or "")
                if obj.get("@type") in ("Product", "CreativeWork", "Event"):
                    name = obj.get("name", "")
                    desc = obj.get("description", "")
                    if name:
                        return {
                            "platform": "Indiegogo", "name": name, "maker": "",
                            "url": url, "raised_usd": 0, "raised_jpy": 0,
                            "backers": 0, "genre": "", "description": desc,
                            "goal_usd": 0, "country": "",
                        }
            except Exception:
                pass

        # 2c. スクリプトタグ内JSON（旧来パターン）
        raised = 0.0
        backers = 0
        maker = ""
        for script in soup.find_all("script"):
            text = script.string or ""
            if not ("collected_funds" in text or "amount_raised" in text
                    or "funds_raised" in text):
                continue
            try:
                # JSON blob の可能性
                m_json = re.search(r'(\{.*"title"\s*:\s*"[^"]+".*\})', text, re.S)
                if m_json:
                    obj = json.loads(m_json.group(1))
                    result = _parse_igg_campaign(obj, url)
                    if result:
                        return result
            except Exception:
                pass
            # フィールドを個別抽出
            for pat in [r'"(?:collected_funds|amount_raised|funds_raised_amount)"\s*:\s*([\d.]+)',
                        r"collected_funds['\"]?\s*:\s*([\d.]+)"]:
                m = re.search(pat, text)
                if m:
                    raised = float(m.group(1))
                    break
            m2 = re.search(r'"contributions_count"\s*:\s*(\d+)', text)
            if m2:
                backers = int(m2.group(1))
            m3 = re.search(r'"owner"\s*:\s*\{[^}]*"name"\s*:\s*"([^"]+)"', text)
            if m3:
                maker = m3.group(1)
            break

        # 2d. クリエイタープロフィールリンクを探す（/individuals/xxx）
        profile_url = ""
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if "/individuals/" in href:
                profile_url = href if href.startswith("http") else "https://www.indiegogo.com" + href
                break

        # 2e. og:title / og:description で最低限の情報を返す
        title_tag = soup.find("meta", property="og:title")
        desc_tag  = soup.find("meta", property="og:description")
        raw_title = (title_tag.get("content", "") if title_tag else "") or ""
        blurb     = (desc_tag.get("content", "") if desc_tag else "") or ""

        if raw_title and raw_title.lower() != "indiegogo":
            # "Product Name by Maker - Indiegogo" → name="Product Name", maker="Maker"
            clean = re.sub(r"\s*-\s*Indiegogo\s*$", "", raw_title, flags=re.I).strip()
            by_match = re.search(r"^(.+?)\s+by\s+([^-|]+)$", clean, re.I)
            if by_match:
                name  = by_match.group(1).strip()
                maker = maker or by_match.group(2).strip()
            else:
                name = clean
            # クリエイタープロフィールURLの候補リスト（URLスラグを優先）
            maker_slug = re.sub(r"[^a-z0-9]", "", maker.lower()) if maker else ""
            profile_candidates = []
            if creator_slug:
                profile_candidates.append(f"https://www.indiegogo.com/individuals/{creator_slug}")
            if profile_url:
                profile_candidates.append(profile_url)
            if maker_slug and maker_slug not in creator_slug:
                profile_candidates.append(f"https://www.indiegogo.com/individuals/{maker_slug}")

            profile_links = {}
            for pc in profile_candidates:
                profile_links = _igg_profile_links(pc, maker, sess)
                if profile_links:
                    break

            # 有効なスラグ（URLから抽出したものを優先）
            effective_slug = creator_slug or maker_slug
            creator_websites = []
            if profile_links.get("website"):
                creator_websites.append(profile_links["website"])
            return {
                "platform": "Indiegogo", "name": name, "maker": maker,
                "url": url, "raised_usd": raised,
                "raised_jpy": int(raised * JPY_PER_USD),
                "backers": backers, "genre": "",
                "description": blurb, "goal_usd": 0, "country": "",
                "_creator_slug": effective_slug,
                "_creator_websites": creator_websites,
                "_official_site": creator_websites[0] if creator_websites else "",
                "_igg_profile": profile_links,
            }

    except Exception as e:
        print(f"  [IGG] ページ取得エラー: {e}")

    return None


def _extract_creator_from_ks_url(url: str) -> str:
    """Kickstarter URL から creator スラグを抽出: /projects/{creator}/{project}"""
    base = url.split("?")[0].rstrip("/")
    parts = base.split("/")
    # ['https:', '', 'www.kickstarter.com', 'projects', creator, project]
    if len(parts) >= 6 and parts[3] == "projects":
        return parts[4]
    return ""


def find_creator_site(creator_slug: str, product_name: str = "") -> str:
    """クリエイタースラグ・商品名から公式サイトを探す"""
    if not creator_slug and not product_name:
        return ""
    sess = _session()

    # ドメイン駐車・売り出しページの判定
    _PARKING_HOSTS = (
        "hugedomains.com", "sedo.com", "dan.com", "afternic.com",
        "godaddy.com", "parkingcrew.net", "domainmarket.com",
        "bodis.com", "undeveloped.com", "uniregistry.com",
    )
    _PARKING_PHRASES = ("is for sale", "domain for sale", "buy this domain",
                        "this domain is for sale", "purchase this domain")

    def _is_parked(resp) -> bool:
        if any(h in resp.url for h in _PARKING_HOSTS):
            return True
        snippet = resp.text[:3000].lower()
        return any(p in snippet for p in _PARKING_PHRASES)

    # ── 1. ドメイン直接試打（creator_slugがある場合のみ）────────────
    if creator_slug:
        extensions = [".com", ".io", ".co", ".shop", ".tech", ".ai", ".store", ".net"]
        for ext in extensions:
            for prefix in [creator_slug, f"www.{creator_slug}"]:
                candidate = f"https://{prefix}{ext}"
                try:
                    resp = sess.get(candidate, timeout=6, allow_redirects=True)
                    if resp.status_code == 200 and len(resp.text) > 500 and not _is_parked(resp):
                        return resp.url
                except Exception:
                    continue

    # ── 2. DuckDuckGo HTMLスクレイピングで検索 ──────────────────────
    _SKIP_DOMAINS = (
        "kickstarter.com", "indiegogo.com", "amazon.", "wikipedia.",
        "facebook.com", "instagram.com", "twitter.com", "x.com",
        "youtube.com", "linkedin.com", "tiktok.com",
    )
    # メーカー名（スラグ）で先に検索、次に商品名で検索
    queries = []
    if creator_slug:
        queries.append(f"{creator_slug} official site")
    if product_name:
        queries.append(f"{product_name} official site")

    for query in queries:
        try:
            ddg_url = f"https://html.duckduckgo.com/html/?q={requests.utils.quote(query)}"
            resp = sess.get(ddg_url, timeout=12)
            if resp.status_code != 200:
                continue
            soup = BeautifulSoup(resp.text, "html.parser")
            for a in soup.select(".result__a"):
                href = a.get("href", "")
                uddg = re.search(r"uddg=([^&]+)", href)
                if uddg:
                    href = requests.utils.unquote(uddg.group(1))
                if href.startswith("http") and not any(s in href for s in _SKIP_DOMAINS):
                    return href
        except Exception:
            continue

    return ""


def _extract_from_slug(url: str) -> Dict:
    """URLスラグから商品名を推定し、最低限の情報を返す（スクレイピング失敗時のフォールバック）"""
    base = url.split("?")[0].rstrip("/")
    slug = base.split("/")[-1]
    creator = _extract_creator_from_ks_url(url)
    # 数値IDはメーカー名として不適切なので空にする
    maker = "" if str(creator).isdigit() else creator
    # ハイフン区切りを単語に変換して商品名を推定
    name = " ".join(w.capitalize() for w in slug.replace("-", " ").split())
    platform = "Kickstarter" if "kickstarter.com" in url else "Indiegogo"
    return {
        "platform": platform, "name": name, "maker": maker,
        "url": base, "raised_usd": 0, "raised_jpy": 0,
        "backers": 0, "genre": "Technology",
        "description": f"（URL: {base}）", "goal_usd": 0, "country": "",
        "_from_slug": True,
        "_creator_slug": creator,
    }


def fetch_project_from_url(url: str) -> Optional[Dict]:
    """URL から自動判定してプロジェクト情報を取得。失敗時はURLスラグで代替"""
    if "kickstarter.com" in url:
        result = fetch_kickstarter_project(url)
    elif "indiegogo.com" in url:
        result = fetch_indiegogo_project(url)
    else:
        return None
    # スクレイピング失敗 → URLスラグから最低限の情報を生成してClaude分析は実行する
    return result if result else _extract_from_slug(url)


def load_urls_from_file(path: Path) -> List[str]:
    """テキストファイルから URL 一覧を読み込む（1行1URL）"""
    urls = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and line.startswith("http") and not line.startswith("#"):
            urls.append(line)
    return urls


# ───────────────────────────────────────────────────────────────────────────────
# メイン
# ───────────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="海外クラファン商品リサーチツール")
    parser.add_argument("--urls",      type=str, default=None,
                        help="URLリストファイル (1行1URL)。指定するとURLモードで動作")
    parser.add_argument("--limit",     type=int, default=30, help="出力件数 (デフォルト: 30)")
    parser.add_argument("--no-claude", action="store_true",  help="Claude分析をスキップ")
    args = parser.parse_args()

    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print("=" * 60)
    print("  海外クラファン商品リサーチツール")
    print(f"  対象調達額: JPY {int(MIN_RAISED_USD * JPY_PER_USD / 1_000_000)}M"
          f" 〜 {int(MAX_RAISED_USD * JPY_PER_USD / 1_000_000)}M")
    print(f"  出力件数: {args.limit}件")
    print("=" * 60)

    # Claude クライアント
    client = None
    if not args.no_claude and _anthropic is not None:
        api_key = _load_api_key()
        if api_key:
            client = _anthropic.Anthropic(api_key=api_key)
            print("Claude API: 接続済み\n")
        else:
            print("Claude API: APIキー未検出（分析スキップ）\n")
    else:
        print("Claude API: スキップ\n")

    # ── プロジェクト取得 ────────────────────────────
    if args.urls:
        # URLモード: テキストファイルから URL を読み込む
        url_file = Path(args.urls)
        if not url_file.exists():
            print(f"[ERROR] URLファイルが見つかりません: {url_file}")
            sys.exit(1)
        urls = load_urls_from_file(url_file)
        print(f"[1/4] URLモード: {len(urls)}件のURLを処理\n")
        all_projects = []
        for u in urls[:args.limit]:
            print(f"  取得中: {u[:70]}")
            p = fetch_project_from_url(u)
            if p:
                all_projects.append(p)
            time.sleep(API_WAIT_SEC)
    else:
        # 自動スクレイピングモード
        print("[1/4] Kickstarter 調査中...")
        ks = search_kickstarter(limit=args.limit)
        print(f"  → 条件合致: {len(ks)}件\n")

        print("[1/4] Indiegogo 調査中...")
        igg = search_indiegogo(limit=max(10, args.limit // 2))
        print(f"  → 条件合致: {len(igg)}件\n")

        all_projects = (ks + igg)[:args.limit]

    print(f"合計 {len(all_projects)}件 を詳細分析します\n")

    # ── 詳細取得・分析 ────────────────────────────────
    rows: List[Dict] = []

    for i, p in enumerate(all_projects, 1):
        name_disp = p["name"][:50]
        print(f"[{i:02d}/{len(all_projects)}] {name_disp}")

        # 公式サイト
        official_url = find_official_site(p["url"], p["maker"])
        if official_url:
            print(f"  公式サイト: {official_url[:60]}")

        # 連絡先
        contact = get_contact_info(official_url) if official_url else {
            "official_url": "", "email": "未確認", "contact_form": "未確認",
            "facebook": "未確認", "instagram": "未確認", "linkedin": "未確認",
        }

        # Claude 分析
        analysis = analyze_with_claude(p, client)
        print(f"  優先度: {analysis.get('priority', '?')}")

        rows.append(build_row(p, analysis, contact))
        time.sleep(API_WAIT_SEC)

    # ── CSV 出力 ──────────────────────────────────────
    out_dir  = Path(__file__).parent / "output"
    out_dir.mkdir(exist_ok=True)
    ts       = datetime.now().strftime("%Y%m%d_%H%M")
    out_path = out_dir / f"crowdfunding_research_{ts}.csv"

    with open(out_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    print(f"\n✅ 完了: {out_path}")
    print(f"   {len(rows)}件 → Google スプレッドシートに貼り付けてソート可能")

    # 優先度Aサマリー
    a_rows = [r for r in rows if r.get("優先度") == "A"]
    if a_rows:
        print(f"\n━━━ 優先度A: {len(a_rows)}件 ━━━")
        for r in a_rows:
            jpy = r["調達額(円)"]
            print(f"  * {r['商品名'][:40]}  ({r['プラットフォーム']})  "
                  f"JPY {jpy:,}  {r['メーカー名']}")


if __name__ == "__main__":
    main()
