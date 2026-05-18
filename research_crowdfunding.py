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

def find_official_site(project_url: str, maker_name: str) -> str:
    """クラファンページからメーカー公式サイト URL を探す"""
    if not project_url.startswith("http"):
        return ""
    try:
        sess = _session()
        resp = sess.get(project_url, timeout=12)
        if resp.status_code != 200:
            return ""

        soup = BeautifulSoup(resp.text, "html.parser")
        maker_word = maker_name.lower().split()[0] if maker_name else ""

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
                return href

        time.sleep(API_WAIT_SEC)
    except Exception:
        pass
    return ""

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
        for email in emails:
            if not any(kw in email.lower() for kw in ["noreply", "no-reply", "example", "test",
                                                        "privacy", "support@sentry", "wix"]):
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
                        if not any(kw in email.lower() for kw in [
                            "noreply", "no-reply", "example", "test",
                            "privacy", "sentry", "wix", "shopify", ".png",
                        ]):
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


def analyze_with_claude(project: Dict, client, sender_name: str = "", sender_company: str = "") -> Dict:
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

    prompt = _ANALYSIS_PROMPT.format(
        name=project.get("name", ""),
        maker=project.get("maker", ""),
        platform=project.get("platform", ""),
        raised_usd=project.get("raised_usd", 0),
        raised_jpy=project.get("raised_jpy", 0),
        backers=project.get("backers", 0),
        genre=project.get("genre", ""),
        url=project.get("url", ""),
        description=str(project.get("description", ""))[:400],
        company_profile=_build_company_profile(sender_name, sender_company),
    )

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
        print(f"  [Claude] エラー: {e}")
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

def _clean_ks_url(url: str) -> str:
    """クエリパラメータを除去してプロジェクト基本URLを返す"""
    return url.split("?")[0].rstrip("/")


def fetch_kickstarter_project(url: str) -> Optional[Dict]:
    """Kickstarter プロジェクトページからデータを取得"""
    base_url = _clean_ks_url(url)
    creator_slug = _extract_creator_from_ks_url(base_url)
    product_slug = base_url.rstrip("/").split("/")[-1]

    # ── 1. 検索API + cloudscraper（pledged取得の最優先手段）──────────
    search_term = " ".join(w for w in product_slug.replace("-", " ").split() if len(w) > 1)
    search_url = (
        "https://www.kickstarter.com/projects/search.json"
        f"?term={requests.utils.quote(search_term)}&sort=most_funded&page=1"
    )
    scraper = _ks_session()
    try:
        resp = scraper.get(search_url, timeout=15)
        if resp.status_code == 200 and resp.text.strip():
            for proj in resp.json().get("projects", []):
                proj_web_url = (proj.get("urls", {}) or {}).get("web", {}).get("project", "")
                # creator_slug か product_slug の先頭20文字でマッチ
                if creator_slug and creator_slug in proj_web_url:
                    pass  # creator_slug が一致
                elif product_slug[:20] not in proj_web_url:
                    continue
                pledged = float(proj.get("pledged", 0) or 0)
                creator = (proj.get("creator", {}) or {})
                cat     = proj.get("category", {})
                genre   = cat.get("name", "Technology") if isinstance(cat, dict) else "Technology"
                # proj_web_url はフルURL（https://...）のためそのまま使用
                final_url = proj_web_url if proj_web_url.startswith("http") else base_url
                return {
                    "platform":      "Kickstarter",
                    "name":          proj.get("name", ""),
                    "maker":         creator.get("name", creator_slug),
                    "url":           final_url,
                    "raised_usd":    pledged,
                    "raised_jpy":    int(pledged * JPY_PER_USD),
                    "backers":       int(proj.get("backers_count", 0) or 0),
                    "genre":         genre,
                    "description":   proj.get("blurb", ""),
                    "goal_usd":      float(proj.get("goal", 0) or 0),
                    "country":       proj.get("country", ""),
                    "_creator_slug": creator_slug,
                }
    except Exception as e:
        print(f"  [KS] 検索API エラー: {e}")

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
                proj = data.get("project", data)
                name = proj.get("name", "")
                if not name:
                    continue
                pledged_raw = proj.get("pledged", {})
                pledged = float(pledged_raw.get("amount", 0) if isinstance(pledged_raw, dict) else (pledged_raw or 0))
                return {
                    "platform": "Kickstarter", "name": name, "maker": creator_slug,
                    "url": final_url, "raised_usd": pledged,
                    "raised_jpy": int(pledged * JPY_PER_USD),
                    "backers": int(proj.get("backersCount", 0) or 0),
                    "genre": "Technology", "description": proj.get("description", ""),
                    "goal_usd": 0, "country": "", "_creator_slug": creator_slug,
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


def fetch_indiegogo_project(url: str) -> Optional[Dict]:
    """Indiegogo プロジェクトページからデータを取得"""
    sess = _session()

    # ── 1. private_api エンドポイント（最優先）───────────────────────
    slug_match = re.search(r"indiegogo\.com/projects/([^/#?]+)", url)
    if slug_match:
        slug = slug_match.group(1)
        api_url = f"https://www.indiegogo.com/private_api/campaigns/{slug}"
        try:
            resp = sess.get(api_url, timeout=12)
            if resp.status_code == 200:
                data = resp.json()
                camp = data.get("response", data)
                name = camp.get("title", "")
                if name:
                    raised = float(camp.get("collected_funds", camp.get("amount_raised", 0)) or 0)
                    backers = int(camp.get("contributions_count", 0) or 0)
                    owner = camp.get("owner", {}) or {}
                    return {
                        "platform":    "Indiegogo",
                        "name":        name,
                        "maker":       owner.get("name", ""),
                        "url":         url,
                        "raised_usd":  raised,
                        "raised_jpy":  int(raised * JPY_PER_USD),
                        "backers":     backers,
                        "genre":       camp.get("category_name", ""),
                        "description": camp.get("tagline", ""),
                        "goal_usd":    float(camp.get("goal_amount", 0) or 0),
                        "country":     camp.get("country_code", ""),
                    }
        except Exception as e:
            print(f"  [IGG] private_api エラー: {e}")

    # ── 2. HTMLスクレイピング（フォールバック）───────────────────────
    try:
        resp = sess.get(url, timeout=15)
        if resp.status_code != 200:
            return None
        soup = BeautifulSoup(resp.text, "html.parser")

        title_tag = soup.find("meta", property="og:title")
        desc_tag  = soup.find("meta", property="og:description")
        name  = title_tag["content"] if title_tag else ""
        blurb = desc_tag["content"] if desc_tag else ""

        raised = 0.0
        backers = 0
        for script in soup.find_all("script"):
            text = script.string or ""
            if "collected_funds" in text or "amount_raised" in text:
                m = re.search(r'"(?:collected_funds|amount_raised)"\s*:\s*([\d.]+)', text)
                if m:
                    raised = float(m.group(1))
                m2 = re.search(r'"contributions_count"\s*:\s*(\d+)', text)
                if m2:
                    backers = int(m2.group(1))
                break

        return {
            "platform": "Indiegogo", "name": name, "maker": "",
            "url": url, "raised_usd": raised,
            "raised_jpy": int(raised * JPY_PER_USD),
            "backers": backers, "genre": "",
            "description": blurb, "goal_usd": 0, "country": "",
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
    if not creator_slug:
        return ""
    sess = _session()

    # ── 1. ドメイン直接試打 ──────────────────────────────────────────
    extensions = [".com", ".io", ".co", ".shop", ".tech", ".ai", ".store", ".net"]
    for ext in extensions:
        for prefix in [creator_slug, f"www.{creator_slug}"]:
            candidate = f"https://{prefix}{ext}"
            try:
                resp = sess.get(candidate, timeout=6, allow_redirects=True)
                if resp.status_code == 200 and len(resp.text) > 500:
                    return resp.url
            except Exception:
                continue

    # ── 2. DuckDuckGo で検索 ────────────────────────────────────────
    query = product_name if product_name else creator_slug
    query = f"{query} official site"
    try:
        ddg_url = f"https://api.duckduckgo.com/?q={requests.utils.quote(query)}&format=json&no_redirect=1"
        resp = sess.get(ddg_url, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            # AbstractURL または Results[0].FirstURL
            official = data.get("AbstractURL", "")
            if not official and data.get("Results"):
                official = data["Results"][0].get("FirstURL", "")
            if official and not any(skip in official for skip in [
                "kickstarter", "indiegogo", "amazon", "wikipedia",
                "facebook", "instagram", "twitter", "youtube",
            ]):
                return official
    except Exception:
        pass

    return ""


def _extract_from_slug(url: str) -> Dict:
    """URLスラグから商品名を推定し、最低限の情報を返す（スクレイピング失敗時のフォールバック）"""
    base = url.split("?")[0].rstrip("/")
    slug = base.split("/")[-1]
    creator = _extract_creator_from_ks_url(url)
    # ハイフン区切りを単語に変換して商品名を推定
    name = " ".join(w.capitalize() for w in slug.replace("-", " ").split())
    platform = "Kickstarter" if "kickstarter.com" in url else "Indiegogo"
    return {
        "platform": platform, "name": name, "maker": creator,
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
