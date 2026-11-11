# -*- coding: utf-8 -*-
"""連絡先（メールアドレス・問い合わせフォームURL）の検証ロジック

「アプローチ先」表示・CSV出力の両方がここを正として使う。
メールソフトが未設定の端末では mailto: リンクをクリックしても空白画面になるため、
メールアドレスは常に文字列としても表示し、mailto: は補助的な手段にとどめる
（summary_table.py 側の責務）。ここでは「値が本当に使える連絡先か」だけを判定し、
推測で補完しない。空文字・None・「未確認」・mailto:のみ（宛先無し）・不正な形式は
すべて「連絡先なし」として扱う。
"""

import re
from typing import Dict

_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")
_PLACEHOLDERS = {"", "未確認", "n/a", "na", "none", "null", "not available", "不明", "nan"}


def _clean(value) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if text.lower() in _PLACEHOLDERS:
        return ""
    return text


def normalize_email(value) -> str:
    """有効なメールアドレスなら正規化した文字列を返す。無効なら空文字を返す"""
    text = _clean(value)
    if not text:
        return ""
    if text.lower().startswith("mailto:"):
        text = text[len("mailto:"):].strip()
    if not text or "@" not in text:
        return ""
    if not _EMAIL_RE.match(text):
        return ""
    return text


def normalize_contact_url(value) -> str:
    """有効な http/https の問い合わせフォームURLなら文字列を返す。無効なら空文字を返す"""
    text = _clean(value)
    if not text:
        return ""
    for scheme in ("https://", "http://"):
        if text.startswith(scheme) and len(text) > len(scheme):
            return text
    return ""


def resolve(row: Dict) -> Dict[str, str]:
    """1行からメール・問い合わせフォームを解決する（検証済みの値のみ。無ければ空文字）"""
    return {
        "email": normalize_email(row.get("メールアドレス", "")),
        "form_url": normalize_contact_url(row.get("問い合わせフォームURL", "")),
    }


def _listing_label(row: Dict) -> str:
    platform = str(row.get("プラットフォーム", "") or "")
    if platform == "Indiegogo":
        return "📋 IGGページ"
    if platform == "ZECZEC":
        return "📋 ZECZECページ"
    return "📋 KSページ"


def resolve_priority(row: Dict) -> Dict[str, str]:
    """「アプローチ先」として使う連絡先を優先順位どおりに1つ選ぶ

    優先順位: 1.メール 2.Facebook 3.Instagram 4.問い合わせフォーム 5.LinkedIn
    6.公式サイト 7.商品掲載ページ 8.連絡先未取得。
    空文字・不正なURL・不正なメールアドレスは候補に含めない
    （normalize_email / normalize_contact_url の検証をそのまま使う）。
    """
    email = normalize_email(row.get("メールアドレス", ""))
    if email:
        return {"kind": "email", "email": email, "url": "", "label": ""}

    facebook = normalize_contact_url(row.get("Facebook", ""))
    if facebook:
        return {"kind": "facebook", "email": "", "url": facebook, "label": "📘 Facebook"}

    instagram = normalize_contact_url(row.get("Instagram", ""))
    if instagram:
        return {"kind": "instagram", "email": "", "url": instagram, "label": "📷 Instagram"}

    form_url = normalize_contact_url(row.get("問い合わせフォームURL", ""))
    if form_url:
        return {"kind": "form", "email": "", "url": form_url, "label": "🌐 問い合わせフォーム"}

    linkedin = normalize_contact_url(row.get("LinkedIn", ""))
    if linkedin:
        return {"kind": "linkedin", "email": "", "url": linkedin, "label": "💼 LinkedIn"}

    official = normalize_contact_url(row.get("公式サイトURL", ""))
    if official:
        return {"kind": "official", "email": "", "url": official, "label": "🌐 公式サイト"}

    listing = normalize_contact_url(row.get("掲載URL", ""))
    if listing:
        return {"kind": "listing", "email": "", "url": listing, "label": _listing_label(row)}

    return {"kind": "none", "email": "", "url": "", "label": ""}
