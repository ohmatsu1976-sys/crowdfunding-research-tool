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
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

# cloudscraper / anthropic は重いので遅延import（Streamlit起動を速くするため）
_cloudscraper = None   # None=未読込 / False=未インストール / モジュール=読込済


def _ks_session():
    """Kickstarter用セッション。cloudscraperは初回使用時に読み込む"""
    global _cloudscraper
    if _cloudscraper is None:
        try:
            import cloudscraper
            _cloudscraper = cloudscraper
        except ImportError:
            _cloudscraper = False
    if _cloudscraper:
        return _cloudscraper.create_scraper()
    return _session()


def _get_anthropic():
    """anthropicモジュールを返す（未インストールなら None）"""
    try:
        import anthropic
        return anthropic
    except ImportError:
        return None

# ───────────────────────────────────────────────────────────────────────────────
# 設定
# ───────────────────────────────────────────────────────────────────────────────

JPY_PER_USD    = 150.0
JPY_PER_TWD    = 4.7          # 台湾ドル → 円（ZECZEC用）
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
    # br(Brotli)を要求するとデコーダ未導入の環境で本文が壊れ、
    # 公式サイト判定もメール抽出も無言で失敗するため gzip/deflate のみにする
    "Accept-Encoding": "gzip, deflate",
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
        # メーカー名が一般語（rest / home など）のときはキーワード照合に使わない。
        # KSのアカウント名がそのままメーカー名になっている場合があり、誤検出の元になる
        maker_word = maker_name.lower().split()[0] if maker_name else ""
        if len(maker_word) < 4 or maker_word in _BRAND_STOPWORDS:
            maker_word = ""
        # 空文字をキーワードに混ぜると全リンクが一致してしまうため必ず除外する
        keywords = [kw for kw in ["website", "official", "learn more", "visit us",
                                  maker_word] if kw]
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
            if any(kw in link_text or kw in href.lower() for kw in keywords):
                found.append(href)
        return found[:3]

    except Exception:
        pass
    return []

# ───────────────────────────────────────────────────────────────────────────────
# 連絡先取得
# ───────────────────────────────────────────────────────────────────────────────

_FREE_MAIL_DOMAINS = (
    "gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "live.com",
    "yahoo.com", "yahoo.co.jp", "icloud.com", "proton.me", "protonmail.com",
    "qq.com", "163.com", "126.com", "foxmail.com", "naver.com",
)


def _email_is_plausible(email: str, site_host: str, brand: str = "") -> bool:
    """そのサイトの持ち主のメールとして妥当か

    テーマの使い回しなどで無関係な会社のアドレスが1件だけ紛れていることがあり、
    それを『メーカーの連絡先』として出すと受講生が誤送信してしまうため除外する。
    """
    domain = email.rsplit("@", 1)[-1].lower()
    host = (site_host or "").lower().lstrip("www.")
    root = ".".join(host.split(".")[-2:]) if host else ""
    if root and (domain == root or domain.endswith("." + root)):
        return True                      # 同一ドメイン
    if domain in _FREE_MAIL_DOMAINS:
        return True                      # 小規模メーカーはフリーメールも普通に使う
    brand_l = (brand or "").lower()
    if len(brand_l) >= 4 and brand_l in email.lower():
        return True                      # ブランド名を含む（例: hello@sitpack-japan.com）
    return False


def get_contact_info(official_url: str, brand: str = "") -> Dict:
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
        base_url  = resp.url or official_url          # リダイレクト後のURLを基準にする
        site_host = urlparse(base_url).netloc

        # メールアドレス抽出
        emails = re.findall(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}", text)
        _BAD_EMAIL_KW = [
            "noreply", "no-reply", "example", "test", "privacy",
            "sentry", "wix", "kickofflabs", "mailchimp", "sendgrid",
            "klaviyo", "hubspot", "zendesk",
        ]
        for email in emails:
            if any(kw in email.lower() for kw in _BAD_EMAIL_KW):
                continue
            if not _email_is_plausible(email, site_host, brand):
                continue                              # 無関係な会社のアドレスを弾く
            info["email"] = email
            break

        # コンタクトフォーム / 問い合わせページ
        contact_kws = ["contact", "wholesale", "distributor", "press", "partner", "inquiry", "about"]
        for a in soup.find_all("a", href=True):
            href = a["href"]
            text_content = a.get_text(strip=True).lower()
            if any(kw in href.lower() or kw in text_content for kw in contact_kws):
                # 相対URLは urljoin で解決する（単純連結はロケール重複で404になる）
                info["contact_form"] = urljoin(base_url, href)
                break

        # contact / about ページを追加でチェック（Shopify 404ページも含む）
        SUB_PAGES = ["/contact", "/pages/contact", "/about", "/pages/about",
                     "/contact-us", "/pages/contact-us", "/pages/wholesale"]
        for sub in SUB_PAGES:
            if info["email"] != "未確認" and info["instagram"] != "未確認":
                break
            try:
                sub_url = urljoin(base_url, sub)
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
                        if any(kw in email.lower()
                               for kw in _BAD_EMAIL_KW + ["shopify", ".png"]):
                            continue
                        if not _email_is_plausible(email, site_host, brand):
                            continue
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

# ───────────────────────────────────────────────────────────────────────────────
# 営業メール（汎用テンプレート）
#   ※特定の会社名・代表者名（Base on Base LLC 等）には固定しない。
#   　クラファン講座の受講生が、自分の名前に差し替えてそのまま使える文面にする。
# ───────────────────────────────────────────────────────────────────────────────

# 参考実績（受講生本人ではなく「私たちのチーム／支援実績」として柔らかく引用する）
_REFERENCE_PROJECTS = [
    ("Qstoves on Makuake",            "https://www.makuake.com/project/qstoves/"),
    ("Travel Bag project on Makuake", "https://www.makuake.com/project/travel-bag/"),
    ("Vacos Cam on CAMPFIRE",         "https://camp-fire.jp/projects/view/313474"),
    ("Vacos Cam IR on CAMPFIRE",      "https://camp-fire.jp/projects/view/587884"),
]

# 署名・宛名のプレースホルダ（入力がない場合はそのまま差し替え式で残す）
_NAME_PLACEHOLDER  = "[Your Name]"
_BRAND_PLACEHOLDER = "[Brand / Team Name]"


def _titleish(text: str) -> str:
    """人名・ブランド名を自然な表記に整える。

    - すべて小文字 / すべて大文字の語は Title Case にする
      （例: "hirohito ohmatsu" → "Hirohito Ohmatsu" / "m5stack" → "M5Stack"）
    - すでに大小混在の語（"GoPro" / "M5Stack" 等）はそのまま尊重する
    """
    words = []
    for w in (text or "").split():
        if w.islower() or w.isupper():
            words.append(w.title())   # "m5stack" → "M5Stack", "JOHN" → "John"
        else:
            words.append(w)           # "GoPro" 等は変更しない
    return " ".join(words)


# 過度な断定・誇張を避けるための保険（プロンプト指示の取りこぼしを後段で柔らげる）
_SOFTEN_PATTERNS = [
    (re.compile(r"\bensures\b",   re.I), "could support"),
    (re.compile(r"\bensure\b",    re.I), "could support"),
    (re.compile(r"\bguarantees\b", re.I), "could support"),
    (re.compile(r"\bguarantee\b",  re.I), "could support"),
    (re.compile(r"\bis guaranteed to\b", re.I), "could"),
    (re.compile(r"\bwill definitely\b",  re.I), "could"),
    (re.compile(r"\bwill certainly\b",   re.I), "could"),
]


def _soften_claims(text: str) -> str:
    """断定的な表現を、誠実で控えめな表現に置き換える。"""
    out = text or ""
    for pat, repl in _SOFTEN_PATTERNS:
        out = pat.sub(repl, out)
    return out


def _resolve_name(sender_name: str = "") -> str:
    """送信者名。未入力（空・全角プレースホルダ）の場合は [Your Name] を残す。"""
    name = (sender_name or "").strip()
    if not name or name in ("【氏名】", _NAME_PLACEHOLDER):
        return _NAME_PLACEHOLDER
    return _titleish(name)


def build_approach_email(project: Dict, reason_en: str = "",
                         sender_name: str = "", sender_company: str = "") -> Dict:
    """商品情報から汎用営業メール（件名・本文）を組み立てて返す。

    送信者名・会社名/屋号は入力があれば本文の自己紹介と署名に反映する。
    未入力の名前は差し替え式（[Your Name]）。会社名は未入力なら省略する。
    参考実績は「私たちのチームの支援実績」として柔らかく引用する。"""
    product   = (project.get("name") or "").strip() or "[Product Name]"
    raw_brand = (project.get("maker") or "").strip()
    brand     = _titleish(raw_brand) if raw_brand else _BRAND_PLACEHOLDER
    name      = _resolve_name(sender_name)

    # 会社名・屋号（入力時のみ反映。未入力・プレースホルダは空扱い）
    company = (sender_company or "").strip()
    if company in ("【会社名・屋号】", "[Company / Business Name]"):
        company = ""

    # 自己紹介：会社名があれば「from 会社名」を添える
    if company:
        intro = f"My name is {name} from {company}, and I am based in Japan."
    else:
        intro = f"My name is {name}, and I am based in Japan."

    # 署名：会社名があれば名前の下に添える
    signature = f"{name}\n{company}" if company else name

    reason  = (reason_en or "").strip()
    if not reason:
        reason = ("its concept and design could resonate well with Japanese "
                  "consumers who value new and unique products")
    # 断定の強い表現を柔らげ、「because ...」に続く形に整える
    reason = _soften_claims(reason).rstrip(". 　")

    subject = f"Japan Market Opportunity: {product} - Crowdfunding Partnership Proposal"

    reference_lines = "\n".join(
        f"* {label}\n  {url}" for label, url in _REFERENCE_PROJECTS
    )

    body = f"""\
Dear {brand} Team,

{intro}

I came across your product, {product}, and I was very impressed by its concept, design, and potential value for Japanese consumers.

I am currently exploring opportunities to introduce unique and innovative overseas products to the Japanese market through crowdfunding platforms such as Makuake and CAMPFIRE.

In Japan, crowdfunding is a powerful way to test market demand, build early brand awareness, and connect with consumers before entering general retail channels.

As reference, our team has been involved in successful crowdfunding projects in Japan, including:

{reference_lines}

I believe {product} could have strong potential in Japan because {reason}.

I would be interested in discussing the possibility of working together, such as:

* Launching {product} on Makuake or another Japanese crowdfunding platform
* Exploring a Japan distribution partnership
* Testing demand in Japan before expanding into general sales channels

If you are open to discussing the Japanese market, I would be happy to schedule a short online meeting.

I would also like to learn more about your current product stage, production timeline, and partnership policy.

Thank you very much for your time.

Best regards,
{signature}"""

    return {"approach_subject": subject, "approach_body": body}


_ANALYSIS_PROMPT = """\
以下の海外クラファン商品について、日本市場向け分析をJSONのみで返してください。
※情報が少ない場合も、商品名・URLから推測して分析してください。

【商品情報】
商品名: {name}
メーカー: {maker}
プラットフォーム: {platform}
調達額: {funding}
支援者数: {backers}
ジャンル: {genre}
URL: {url}
説明: {description}

【出力JSON】
{{
  "japanese_market_reason": "日本で売れそうな理由（2〜3文）",
  "appeal_points": "日本販売時の訴求ポイント（箇条書き2〜3点）",
  "japanese_competitors": "競合する日本商品（なければ「特になし」）",
  "priority": "A / B / C のいずれか1文字のみ",
  "priority_reason": "優先度の理由（1〜2文）",
  "concerns": "注意点・懸念点（1〜2点）",
  "approach_reason_en": "英語1文。この商品が日本市場で可能性を持つ商品固有の理由。営業メールの 'could have strong potential in Japan because ___' の空欄に自然に入る形で、'because' や末尾のピリオドは付けず小文字始まりの節で書く。【重要】海外メーカーへの営業メールなので、誠実で控えめな表現にし、過度な断定・誇張は避ける。'ensures' / 'guarantees' / 'will definitely' / 'dominates' のような確定表現は使わず、'could help create early awareness' / 'may resonate well with Japanese consumers' / 'could be a good fit for the Japanese market' のように 'could' / 'may' を使った柔らかい表現にする。例: 'its compact design may resonate with Japanese users who value space-efficient gadgets, and there appears to be little comparable product available locally yet'"
}}

優先度基準：
A＝コンセプト強・日本未展開・規制リスク低・Makuake向き
B＝良品だが日本展開済みの可能性あり・競合多め
C＝面白いが規制・価格・物流・競合に懸念あり

【重要・情報が「不明」の場合の扱い】
「不明」「取得できず」と書かれた項目は、単に自動取得できなかっただけで、
実績が無いという意味ではありません。それを理由に優先度を下げないでください。
優先度は商品特性（コンセプト・日本での需要・規制リスク・競合状況）で判断し、
情報が不足している事実は concerns に「調達額が未取得のため要確認」等として記載してください。"""


def analyze_with_claude(project: Dict, client, sender_name: str = "", sender_company: str = "",
                        errors: list = None) -> Dict:
    defaults = {
        "japanese_market_reason": "（Claude未接続のため省略）",
        "appeal_points":          "（Claude未接続のため省略）",
        "japanese_competitors":   "未確認",
        "priority":               "B",
        "priority_reason":        "（Claude未接続のため省略）",
        "concerns":               "未確認",
        "approach_reason_en":     "",
    }
    # Claude 未接続でも、汎用テンプレートで営業メールは生成する
    if client is None:
        return {**defaults, **build_approach_email(project, "", sender_name, sender_company)}

    # 自動取得できなかった項目を 0 のまま渡すと「1円も集まっていない商品」と
    # 誤解され、優先度が不当に下がるため「不明」と明示する
    raised_usd = float(project.get("raised_usd", 0) or 0)
    raised_jpy = int(project.get("raised_jpy", 0) or 0)
    backers    = int(project.get("backers", 0) or 0)
    from_slug  = bool(project.get("_from_slug"))

    funding_txt = (f"${raised_usd:,.0f} (約{raised_jpy:,}円)" if raised_usd > 0
                   else "取得できず（不明。0円という意味ではありません）")
    backers_txt = f"{backers:,}人" if backers > 0 else "取得できず（不明）"

    maker_txt = project.get("maker", "") or "不明"
    name_txt  = project.get("name", "")
    if from_slug:
        maker_txt += "（URLからの推定。正式なメーカー名と異なる可能性あり）"
        name_txt  += "（URLからの推定表記。正式な商品名と異なる可能性あり）"

    desc = str(project.get("description", ""))[:400]
    if from_slug or not desc.strip():
        desc = "取得できず（不明）"

    try:
        prompt = _ANALYSIS_PROMPT.format(
            name=name_txt,
            maker=maker_txt,
            platform=project.get("platform", ""),
            funding=funding_txt,
            backers=backers_txt,
            genre=project.get("genre", ""),
            url=project.get("url", ""),
            description=desc.replace("{", "｛").replace("}", "｝"),
        )
    except Exception as e:
        msg = f"プロンプト生成エラー: {e}"
        print(f"  [Claude] {msg}")
        if errors is not None:
            errors.append(msg)
        return {**defaults, **build_approach_email(project, "", sender_name, sender_company)}

    last_err = None
    for attempt in range(3):
        try:
            response = client.messages.create(
                model=MODEL_ID,
                max_tokens=1024,
                # temperature は渡さない。新しい Anthropic SDK では
                # messages.create から削除されており、渡すと送信前に TypeError になる
                messages=[{"role": "user", "content": prompt}],
            )
            raw = response.content[0].text.strip()
            m = re.search(r"```json\s*([\s\S]+?)\s*```", raw)
            json_str = m.group(1) if m else raw[raw.find("{"):raw.rfind("}")+1]
            analysis = {**defaults, **json.loads(json_str)}
            # 商品固有の理由を使って営業メールを組み立てる（会社名は固定しない）
            analysis.update(
                build_approach_email(project, analysis.get("approach_reason_en", ""), sender_name, sender_company)
            )
            return analysis
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
    # 分析に失敗しても営業メールだけは出せるようにする
    return {**defaults, **build_approach_email(project, "", sender_name, sender_company)}

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
    "優先度", "判定の確度", "優先度の理由", "注意点・懸念点",
    "営業メール件名(英語)", "営業メール本文(英語)",
]


def is_low_confidence(p: Dict) -> bool:
    """判定材料（調達額・説明文など）が揃っていないか

    材料が無いとABC判定は同じ商品でも揺れるため、参考値であることを明示する。
    """
    return (bool(p.get("_from_slug")) or bool(p.get("_partial"))
            or not float(p.get("raised_usd", 0) or 0))


def build_row(p: Dict, analysis: Dict, contact: Dict) -> Dict:
    return {
        "商品名":              p.get("name", ""),
        # 内部の maker は空のままにする（英文営業メールでは [Brand / Team Name] に
        # 置き換わるため）。表示・CSVでだけ「不明」と明示する
        "メーカー名":          p.get("maker", "") or "不明",
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
        "判定の確度":          "参考値（データ不足）" if is_low_confidence(p) else "データ取得済み",
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

    # ── 3. 別経路のフォールバック ────────────────────────────────
    # Kickstarter本体はデータセンターIP（Streamlit Cloud等）からブロックされる
    # ことがあるため、軽量エンドポイントと外部ミラーを順に試す
    return _fetch_ks_via_fallback(base_url, creator_slug, product_slug)


def _ks_stats_json(base_url: str) -> Dict:
    """KSの軽量エンドポイント stats.json から調達額・支援者数を取得"""
    try:
        resp = _session().get(f"{base_url}/stats.json?v=1", timeout=10)
        if resp.status_code != 200:
            return {}
        proj = resp.json().get("project", {})
        pledged = float(proj.get("pledged", 0) or 0)
        if pledged <= 0:
            return {}
        return {
            "raised_usd": pledged,
            "raised_jpy": int(pledged * JPY_PER_USD),
            "backers":    int(proj.get("backers_count", 0) or 0),
        }
    except Exception:
        return {}


_KICKTRAQ_FUNDED = re.compile(r"Funded:\s*\$([\d,]+)\s*of\s*\$([\d,]+)")
_KICKTRAQ_BACKERS = re.compile(r"Backers:\s*([\d,]+)")


def _kicktraq_project(creator_slug: str, product_slug: str) -> Dict:
    """Kicktraq（KSの外部トラッキングサイト）からプロジェクト情報を取得

    Kickstarter本体と別ホストのため、本体がブロックされる環境でも取得できる。
    """
    if not creator_slug or not product_slug:
        return {}
    url = f"https://www.kicktraq.com/projects/{creator_slug}/{product_slug}/"
    try:
        resp = _session().get(url, timeout=12, allow_redirects=True)
        if resp.status_code != 200 or not _looks_like_html(resp.text):
            return {}
        soup = BeautifulSoup(resp.text, "html.parser")
        text = soup.get_text("\n", strip=True)

        data: Dict = {}
        m = _KICKTRAQ_FUNDED.search(text)
        if m:
            pledged = float(m.group(1).replace(",", ""))
            data["raised_usd"] = pledged
            data["raised_jpy"] = int(pledged * JPY_PER_USD)
            data["goal_usd"]   = float(m.group(2).replace(",", ""))
        m = _KICKTRAQ_BACKERS.search(text)
        if m:
            data["backers"] = int(m.group(1).replace(",", ""))

        # 「<商品名> by <メーカー名> :: Kicktraq」形式のタイトル
        title = soup.title.get_text(strip=True) if soup.title else ""
        title = title.replace(":: Kicktraq", "").strip()
        if " by " in title:
            name, _, maker = title.rpartition(" by ")
            data["name"]  = name.strip()
            data["maker"] = maker.strip()
        elif title:
            data["name"] = title

        desc = soup.find("meta", attrs={"name": "description"})
        if desc and desc.get("content"):
            data["description"] = desc["content"].strip()

        return data if data.get("raised_usd") else {}
    except Exception:
        return {}


def _fetch_ks_via_fallback(base_url: str, creator_slug: str, product_slug: str) -> Optional[Dict]:
    """KS本体が取得できないときの代替経路"""
    project: Dict = {
        "platform": "Kickstarter", "name": "", "maker": "",
        "url": base_url, "raised_usd": 0, "raised_jpy": 0,
        "backers": 0, "genre": "Technology", "description": "",
        "goal_usd": 0, "country": "", "_creator_slug": creator_slug,
    }

    kicktraq = _kicktraq_project(creator_slug, product_slug)
    if kicktraq:
        project.update(kicktraq)
        project["_source"] = "kicktraq"
        print(f"  [KS] Kicktraq から取得: {project.get('name','')[:40]}")
        return project

    stats = _ks_stats_json(base_url)
    if stats:
        project.update(stats)
        project["_source"] = "stats.json"
        project["_partial"] = True      # 商品名・メーカー名はスラグからの推定
        # 商品名・メーカー名は取得できないため、既存のURL推定ロジックを再利用する。
        # これによりアカウント名が一般語（例: rest）の場合も誤ったメーカー名にならない
        slug_info = _extract_from_slug(base_url)
        project["name"]  = slug_info.get("name", "") or project["name"]
        project["maker"] = slug_info.get("maker", "")
        print(f"  [KS] stats.json から調達額を取得: ${stats['raised_usd']:,.0f}")
        return project

    return None


def _categorize_link(href: str, links: dict, skip: tuple) -> None:
    """URLをSNS種別に分類してlinksに追加するヘルパー"""
    if not href or not href.startswith("http"):
        return
    if any(s in href for s in skip):
        return
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
        links.setdefault("website", href)


def _igg_profile_links(profile_url: str, maker: str = "", sess=None,
                       _accessible_urls: list = None) -> dict:
    """Indiegogoクリエイタープロフィールページから公式サイト・SNSリンクを取得
    Next.js SPAのため<a>タグだけでなく__NEXT_DATA__とscriptタグ内JSONも解析する
    """
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
        # リダイレクトでホームページ等に飛んだ場合はスキップ
        if "/individuals/" not in r.url and "/creators/" not in r.url:
            return {}
        # アクセス可能URLを記録（リンクが見つからなくても有効なURLとして保持）
        if _accessible_urls is not None:
            _accessible_urls.append(r.url)
        soup = BeautifulSoup(r.text, "html.parser")
        links = {}

        # 1. __NEXT_DATA__ JSON（Next.js SSRデータ）
        next_tag = soup.find("script", id="__NEXT_DATA__")
        if next_tag and next_tag.string:
            try:
                ndata = json.loads(next_tag.string)
                pp = ndata.get("props", {}).get("pageProps", {})
                # Indiegogoのindividualページの可能性のあるキー
                individual = (
                    pp.get("individual") or pp.get("profile") or
                    pp.get("userData") or pp.get("user") or
                    pp.get("data", {}).get("individual") or
                    pp.get("data", {}).get("profile") or {}
                )
                # linksフィールド（配列 or dict）
                ext_links = (individual.get("links") or
                             individual.get("external_links") or
                             individual.get("socialLinks") or [])
                if isinstance(ext_links, list):
                    for lnk in ext_links:
                        url_val = lnk if isinstance(lnk, str) else (
                            lnk.get("url") or lnk.get("href") or "")
                        _categorize_link(url_val, links, _SKIP)
                elif isinstance(ext_links, dict):
                    for url_val in ext_links.values():
                        if isinstance(url_val, str):
                            _categorize_link(url_val, links, _SKIP)
                # websiteフィールド（文字列）
                for key in ("website", "website_url", "websiteUrl", "homepage", "url"):
                    w = individual.get(key, "")
                    if w and isinstance(w, str) and w.startswith("http"):
                        links.setdefault("website", w)
                        break
                if links:
                    return links
            except Exception:
                pass

        # 2. スクリプトタグ内JSONからURL値を正規表現で抽出（ブルートフォース）
        for script in soup.find_all("script"):
            text = script.string or ""
            if "http" not in text:
                continue
            # "website":"https://..." パターン
            for m in re.finditer(
                r'"(?:website|websiteUrl|website_url|homepage|external_url)"'
                r'\s*:\s*"(https?://[^"]{4,})"', text
            ):
                _categorize_link(m.group(1), links, _SKIP)
            # "url":"https://..." （indiegogo以外のドメイン）
            for m in re.finditer(r'"url"\s*:\s*"(https?://(?!(?:www\.)?indiegogo\.com)[^"]{4,})"', text):
                _categorize_link(m.group(1), links, _SKIP)
            if links.get("website"):
                return links

        # 3. <a>タグフォールバック（静的HTML中にリンクがある場合）
        for a in soup.find_all("a", href=True):
            _categorize_link(a["href"], links, _SKIP)
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
        # リダイレクト後のURLから creator_slug を再取得（新形式URLへのリダイレクト対応）
        if resp.url and resp.url != url:
            _, redirected_creator = _extract_igg_slugs(resp.url)
            if redirected_creator:
                creator_slug = redirected_creator
        if resp.status_code != 200:
            return None
        html = resp.text
        soup = BeautifulSoup(html, "html.parser")

        # 2a. Next.js __NEXT_DATA__ （最近のIGG構造）
        # キャンペーンデータの取得 + JSON全体からのクリエイターURL抽出を同時実行
        next_tag = soup.find("script", id="__NEXT_DATA__")
        _next_data_ext_links: dict = {}  # __NEXT_DATA__内の外部リンク（後段で使用）
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
            # __NEXT_DATA__全体をregexスキャンしてクリエイター外部URLを探す
            _nd_skip = ("indiegogo.com", "facebook.com/sharer", "twitter.com/intent",
                        "linkedin.com/share", "cdn.", "static.", "cloudfront.")
            for pat in (
                r'"(?:website|websiteUrl|website_url|homepage|external_url)"\s*:\s*"(https?://[^"]{4,})"',
            ):
                for m in re.finditer(pat, next_tag.string):
                    u = m.group(1)
                    if not any(s in u for s in _nd_skip):
                        _categorize_link(u, _next_data_ext_links,
                                         ("indiegogo.com", "facebook.com/sharer"))

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

        # 2d. クリエイタープロフィールリンクを探す（/en/creators/ または /individuals/）
        profile_url = ""
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if "/creators/" in href or "/individuals/" in href:
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
                # 2025年の正しい形式: /en/creators/ を最優先
                profile_candidates.append(f"https://www.indiegogo.com/en/creators/{creator_slug}")
                profile_candidates.append(f"https://www.indiegogo.com/en/individuals/{creator_slug}")
                profile_candidates.append(f"https://www.indiegogo.com/individuals/{creator_slug}")
            if profile_url:
                profile_candidates.append(profile_url)
            if maker_slug and maker_slug not in creator_slug:
                profile_candidates.append(f"https://www.indiegogo.com/en/creators/{maker_slug}")
                profile_candidates.append(f"https://www.indiegogo.com/en/individuals/{maker_slug}")

            accessible_profiles: list = []
            profile_links = {}
            for pc in profile_candidates:
                profile_links = _igg_profile_links(pc, maker, sess,
                                                   _accessible_urls=accessible_profiles)
                if profile_links:
                    break

            # 有効なスラグ（URLから抽出したものを優先）
            effective_slug = creator_slug or maker_slug
            # 優先順位: プロフィールリンク > __NEXT_DATA__スキャン
            merged_links = dict(_next_data_ext_links)
            merged_links.update(profile_links)  # profile_linksを優先
            creator_websites = []
            if merged_links.get("website"):
                creator_websites.append(merged_links["website"])
            # アクセス可能だったプロフィールURL（リンクは取れなくても有効なURLとして保持）
            valid_profile_url = accessible_profiles[0] if accessible_profiles else ""
            return {
                "platform": "Indiegogo", "name": name, "maker": maker,
                "url": url, "raised_usd": raised,
                "raised_jpy": int(raised * JPY_PER_USD),
                "backers": backers, "genre": "",
                "description": blurb, "goal_usd": 0, "country": "",
                "_creator_slug": effective_slug,
                "_creator_websites": creator_websites,
                "_official_site": creator_websites[0] if creator_websites else "",
                "_igg_profile": merged_links,
                "_valid_profile_url": valid_profile_url,
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


# 公式サイト判定に使えない一般語（ブランド名候補から除外）
_BRAND_STOPWORDS = {
    "the", "a", "an", "my", "your", "our", "this", "that", "new", "smart",
    "ai", "for", "of", "and", "with", "rest", "home", "life", "one", "team",
    "studio", "design", "shop", "store", "world", "best", "pro", "app",
    "project", "product", "official", "site", "web", "go", "get", "make",
}


def usable_maker(candidate: str) -> str:
    """メーカー名として確定表示してよい文字列だけを返す（不可なら空文字）

    Kickstarterのアカウント名は「rest」のような一般語のことがあり、
    そのままメーカー名にすると誤った社名を表示してしまう。判定は
    _BRAND_STOPWORDS（既存の一般語リスト）に一本化する。
    """
    value = (candidate or "").strip()
    if not value or value.isdigit():
        return ""
    if value.lower() in _BRAND_STOPWORDS:
        return ""
    return value

# 商品名から特徴語を作るときに落とす語（宣伝文句・プラットフォーム名など）
_GENERIC_TOKENS = {
    "the", "and", "for", "with", "your", "our", "this", "that", "from",
    "world", "worlds", "most", "best", "first", "ever", "only", "more",
    "new", "next", "plus", "pro", "max", "mini", "ultra", "edition",
    "series", "version", "official", "site", "website", "kickstarter",
    "indiegogo", "zeczec", "project", "campaign", "perfect", "ultimate",
    "introducing", "meet",
}


def _brand_tokens(*sources: str) -> List[str]:
    """ブランド名の候補（ドメイン直打ち用）を優先順に返す"""
    cands: List[str] = []
    for raw in sources:
        if not raw:
            continue
        words = re.findall(r"[a-z0-9]+", str(raw).lower())
        if not words:
            continue
        # 先頭語（例: "Sitpack Zen ..." → sitpack / "mono-mono" → mono）
        if len(words[0]) >= 3 and words[0] not in _BRAND_STOPWORDS:
            cands.append(words[0])
        # ハイフン結合形（例: "mono-mono" → monomono）
        if len(words) > 1:
            joined = "".join(words)
            if 3 <= len(joined) <= 20:
                cands.append(joined)
    seen = set()
    return [c for c in cands if not (c in seen or seen.add(c))]


def _product_tokens(product_name: str, brand: str = "") -> List[str]:
    """『そのページが本当にこの商品のサイトか』を確かめるための特徴語"""
    brand_l = (brand or "").lower()
    toks: List[str] = []
    for t in re.findall(r"[a-z0-9]+", (product_name or "").lower()):
        if len(t) < 4 or t in _GENERIC_TOKENS or t == brand_l or t in toks:
            continue
        toks.append(t)
    return toks


def _looks_like_html(text: str) -> bool:
    """本文が正しくデコードされたHTMLか（圧縮のまま等の壊れた応答を弾く）"""
    head = text[:4000].lower()
    return any(m in head for m in ("<html", "<!doctype html", "<head", "<body"))


def _page_matches_product(text: str, tokens: List[str]) -> bool:
    """ページ本文が商品の特徴語を含むか（推測サイトの誤採用を防ぐ決定的チェック）"""
    if not tokens or not _looks_like_html(text):
        return False          # 判定材料が無いときは「確認できた」と見なさない
    low = text[:200_000].lower()
    hits = sum(1 for t in tokens if t in low)
    return hits >= (2 if len(tokens) >= 3 else 1)


def _brand_in_product_name(brand: str, product_name: str) -> bool:
    """ブランド名候補が商品名そのものに含まれるか

    含まれる場合、そのドメイン（例: Sitpack Zen → sitpack.com）は
    クリエイターのアカウント名から推測したドメインより格段に確度が高い。
    """
    if len(brand) < 5:
        return False
    return brand in re.findall(r"[a-z0-9]+", (product_name or "").lower())


def find_creator_site(creator_slug: str, product_name: str = "",
                      maker_name: str = "") -> str:
    """クリエイタースラグ・商品名・メーカー名から公式サイトを探す

    重要: 推測で辿り着いたURLは、そのページが本当にこの商品のものか検証してから返す。
    確認できない場合は空文字を返す（誤ったサイトを公式サイトとして出さないため）。
    """
    if not creator_slug and not product_name and not maker_name:
        return ""
    sess = _session()

    # 検証用の特徴語（商品名ベース。ブランド名そのものは除外＝ドメイン名と循環するため）
    _verify_tokens = _product_tokens(product_name, creator_slug)

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

    # ── 1. ドメイン直接試打 ──────────────────────────────────────────
    # creator_slug（KSのアカウント名）はブランド名とは限らない（例: /projects/rest/sitpack-zen…）
    # ため、商品名・メーカー名からもブランド候補を作り、内容を検証してから採用する
    brand_cands = _brand_tokens(creator_slug, maker_name, product_name)[:3]
    extensions = [".com", ".io", ".co", ".shop", ".tech", ".ai", ".store", ".net"]

    def _try_domain(brand: str, ext: str) -> str:
        for prefix in (brand, f"www.{brand}"):
            try:
                resp = sess.get(f"https://{prefix}{ext}", timeout=6, allow_redirects=True)
            except Exception:
                continue
            if resp.status_code != 200 or len(resp.text) <= 500 or _is_parked(resp):
                continue
            if not _looks_like_html(resp.text):
                continue
            # ブランド名が商品名に含まれる場合はドメイン一致自体が強い根拠
            # （例: "Sitpack Zen ..." ↔ sitpack.com）。
            # そうでない推測（KSアカウント名など）は本文の特徴語で裏取りする
            if (_brand_in_product_name(brand, product_name)
                    or _page_matches_product(resp.text,
                                             _product_tokens(product_name, brand))):
                return resp.url
        return ""

    # まず全候補を .com で試し、その後は上位候補のみ他の拡張子を試す（時間短縮）
    for brand in brand_cands:
        hit = _try_domain(brand, ".com")
        if hit:
            return hit
    for brand in brand_cands[:2]:
        for ext in extensions[1:]:
            hit = _try_domain(brand, ext)
            if hit:
                return hit

    # ── 2. DuckDuckGo 検索（html + lite 両エンドポイント）────────────
    _SKIP_DOMAINS = (
        "kickstarter.com", "indiegogo.com", "amazon.", "wikipedia.",
        "facebook.com", "instagram.com", "twitter.com", "x.com",
        "youtube.com", "linkedin.com", "tiktok.com",
    )
    # 商品名での検索を最優先（creator_slugは無関係な一般語のことがあるため）
    queries = []
    if product_name:
        queries.append(f"{product_name} official site")
    if maker_name and maker_name.lower() != (creator_slug or "").lower():
        queries.append(f"{maker_name} official site")
    if creator_slug:
        queries.append(f"{creator_slug} official site")

    def _verified(href: str) -> bool:
        """検索結果のURLを実際に開き、この商品のサイトか確認する"""
        try:
            resp = sess.get(href, timeout=8, allow_redirects=True)
        except Exception:
            return False
        if resp.status_code != 200 or _is_parked(resp):
            return False
        return _page_matches_product(resp.text, _verify_tokens)

    for query in queries:
        encoded = requests.utils.quote(query)
        candidates: List[str] = []

        # Approach A: html.duckduckgo.com（uddgエンコードされたURL）
        try:
            resp = sess.get(f"https://html.duckduckgo.com/html/?q={encoded}", timeout=12)
            if resp.status_code == 200:
                soup = BeautifulSoup(resp.text, "html.parser")
                for a in soup.select(".result__a"):
                    href = a.get("href", "")
                    uddg = re.search(r"uddg=([^&]+)", href)
                    if uddg:
                        href = requests.utils.unquote(uddg.group(1))
                    if href.startswith("http") and not any(s in href for s in _SKIP_DOMAINS):
                        candidates.append(href)
        except Exception:
            pass

        # Approach B: lite.duckduckgo.com（シンプルHTML、AWSでも動く可能性あり）
        try:
            resp = sess.get(f"https://lite.duckduckgo.com/lite/?q={encoded}", timeout=12)
            if resp.status_code == 200:
                soup = BeautifulSoup(resp.text, "html.parser")
                for a in soup.find_all("a", href=True):
                    href = a["href"]
                    if href.startswith("http") and not any(s in href for s in _SKIP_DOMAINS):
                        candidates.append(href)
        except Exception:
            pass

        # Approach C: Bing HTML（DDGがブロックされてもBingは通る場合あり）
        try:
            resp = sess.get(
                f"https://www.bing.com/search?q={encoded}&setlang=en-US",
                timeout=12,
            )
            if resp.status_code == 200:
                soup = BeautifulSoup(resp.text, "html.parser")
                for a in soup.select("li.b_algo h2 a, #b_results h2 a"):
                    href = a.get("href", "")
                    if href.startswith("http") and not any(s in href for s in _SKIP_DOMAINS):
                        candidates.append(href)
        except Exception:
            pass

        # 上位候補のみ検証（無検証で先頭を返すと無関係サイトを掴む）
        seen = set()
        for href in candidates:
            if href in seen:
                continue
            seen.add(href)
            if len(seen) > 5:
                break
            if _verified(href):
                return href

    return ""


def _extract_from_slug(url: str) -> Dict:
    """URLスラグから商品名を推定し、最低限の情報を返す（スクレイピング失敗時のフォールバック）"""
    base = url.split("?")[0].rstrip("/")
    slug = base.split("/")[-1]
    creator = _extract_creator_from_ks_url(url)
    # 一般語・数値IDはメーカー名として採用しない（判定は usable_maker に一本化）
    maker = usable_maker(creator)
    # ハイフン区切りを単語に変換して商品名を推定
    name = " ".join(w.capitalize() for w in slug.replace("-", " ").split())

    def _brand_from_slug() -> str:
        """商品スラグの先頭語をブランド名候補として取り出す（採用可否も同じ基準で判定）"""
        if "-" not in slug:
            return ""
        first_word = slug.split("-")[0]
        if len(first_word) < 3:
            return ""
        return usable_maker(first_word).capitalize()

    if "kickstarter.com" in url:
        platform = "Kickstarter"
        # KSのアカウント名はブランド名とは限らない（例: /projects/rest/sitpack-zen…）。
        # 採用できない場合は商品スラグの先頭語をブランド名候補にする
        if not maker:
            maker = _brand_from_slug()
    elif "zeczec.com" in url:
        platform = "ZECZEC"
        # ZECZECはスラグの先頭単語がブランド名のことが多い（例: kieslect-ai-watch → kieslect）
        if not maker:
            maker = _brand_from_slug()
            creator = maker.lower() or creator
    else:
        platform = "Indiegogo"
        # Indiegogoの旧形式URL（creator不在）でも先頭単語をブランド名として推定
        if not maker:
            maker = _brand_from_slug()
            creator = maker.lower() or creator
    return {
        "platform": platform, "name": name, "maker": maker,
        "url": base, "raised_usd": 0, "raised_jpy": 0,
        "backers": 0, "genre": "Technology",
        "description": f"（URL: {base}）", "goal_usd": 0, "country": "",
        "_from_slug": True,
        "_creator_slug": creator,
    }


def _parse_zeczec_campaign(data: dict, url: str) -> Optional[Dict]:
    """ZECZEC キャンペーン情報を抽出（台湾ドル NT$ → JPY/USD換算）"""
    campaign = data.get("campaign", {})
    if not campaign:
        return None

    name = campaign.get("title", "")
    if not name:
        return None

    # ZECZECは台湾ドル(TWD)建て
    raised_twd = float(campaign.get("collected_amount", 0) or 0)
    raised_jpy = int(raised_twd * JPY_PER_TWD)
    raised_usd = raised_jpy / JPY_PER_USD if raised_jpy > 0 else 0.0

    target_twd = float(campaign.get("target_amount", 0) or 0)
    goal_usd = (target_twd * JPY_PER_TWD) / JPY_PER_USD if target_twd > 0 else 0.0

    creator = campaign.get("creator", {})
    maker = creator.get("name", "") if isinstance(creator, dict) else ""

    return {
        "platform":    "ZECZEC",
        "name":        name,
        "maker":       maker,
        "url":         url,
        "raised_usd":  raised_usd,
        "raised_jpy":  raised_jpy,
        "backers":     int(campaign.get("supporter_count", 0) or 0),
        "genre":       campaign.get("category_name", ""),
        "description": campaign.get("summary", ""),
        "goal_usd":    goal_usd,
        "country":     "TW",
    }


def fetch_zeczec_project(url: str) -> Optional[Dict]:
    """ZECZEC プロジェクトページからデータを取得"""
    try:
        sess = _session()
        base_url = url.rstrip("/").split("?")[0]
        resp = sess.get(base_url, timeout=15, allow_redirects=True)
        print(f"  [ZECZEC] status={resp.status_code} final_url={resp.url[:60]}")
        if resp.status_code != 200:
            return None

        soup = BeautifulSoup(resp.text, "html.parser")

        # __ ページ内JSON データを探す __
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(script.string)
                if isinstance(data, dict) and "campaign" in data:
                    result = _parse_zeczec_campaign(data, base_url)
                    if result:
                        return result
            except Exception:
                continue

        # __ 代替: React state データを探す __
        for script in soup.find_all("script"):
            if script.string and "window.__INITIAL_STATE__" in script.string:
                try:
                    match = re.search(r'window\.__INITIAL_STATE__\s*=\s*({.*?});', script.string)
                    if match:
                        data = json.loads(match.group(1))
                        # ネストされた構造に対応
                        campaign_data = data.get("campaign", {}) or data.get("data", {})
                        if campaign_data:
                            result = _parse_zeczec_campaign({"campaign": campaign_data}, base_url)
                            if result:
                                return result
                except Exception:
                    continue

        # __ HTMLからメタ情報を抽出（NT$ 台湾ドル）__
        title = ""
        raised_twd = 0.0

        og_title = soup.find("meta", property="og:title")
        if og_title and og_title.get("content"):
            title = og_title["content"]

        # 金額情報をHTMLから抽出（NT$1,234,567 / NT$ 1,234,567 / $1,234,567 等）
        for tag in soup.find_all(["span", "div", "p", "h1", "h2", "h3"]):
            text = tag.get_text(strip=True)
            # 「NT$1,234,567」または「$1,234,567」パターン
            m = re.search(r"NT\$\s*([\d,]+)", text)
            if not m:
                m = re.search(r"\$\s*([\d,]+)", text)
            if m:
                val = float(m.group(1).replace(",", ""))
                if val >= 1000:  # ノイズ除外
                    raised_twd = val
                    break

        if title and raised_twd > 0:
            raised_jpy = int(raised_twd * JPY_PER_TWD)
            return {
                "platform":    "ZECZEC",
                "name":        title,
                "maker":       "",
                "url":         base_url,
                "raised_usd":  raised_jpy / JPY_PER_USD,
                "raised_jpy":  raised_jpy,
                "backers":     0,
                "genre":       "",
                "description": "",
                "goal_usd":    0.0,
                "country":     "TW",
                "_from_slug":  True,
            }

        return None

    except Exception as e:
        print(f"  [ZECZEC] エラー: {e}")
        return None


def fetch_project_from_url(url: str) -> Optional[Dict]:
    """URL から自動判定してプロジェクト情報を取得。失敗時はURLスラグで代替"""
    result = None
    try:
        if "kickstarter.com" in url:
            result = fetch_kickstarter_project(url)
        elif "indiegogo.com" in url:
            result = fetch_indiegogo_project(url)
        elif "zeczec.com" in url:
            result = fetch_zeczec_project(url)
        else:
            return None
    except Exception as e:
        print(f"  [fetch_project_from_url] 例外: {type(e).__name__}: {e}")
        result = None

    if result:
        return result

    # スクレイピング失敗 → URLスラグから最低限の情報を生成してClaude分析は実行する
    try:
        return _extract_from_slug(url)
    except Exception as e:
        print(f"  [_extract_from_slug] 例外: {type(e).__name__}: {e}")
        return None


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
    _anthropic = _get_anthropic() if not args.no_claude else None
    if _anthropic is not None:
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
        contact = get_contact_info(official_url, brand=p.get("maker", "")) if official_url else {
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
