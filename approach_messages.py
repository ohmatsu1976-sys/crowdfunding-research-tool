# -*- coding: utf-8 -*-
"""初回アプローチ文面の決定論的テンプレート生成（チャネル別・第1段階「返信獲得」）

AI API（Anthropic等）は一切呼び出さない。既存の案件情報（商品名・メーカー名）・
連絡チャネル（cases.resolve_primary_channel()が選ぶ1つ）・送信者プロフィール
（sender_profiles.py）から、文面（件名・本文）を組み立てるだけの純粋関数。

実際の送信は一切行わない（送信手段を何も呼び出さない）。利用者が画面に表示された
文面を確認・編集し、お使いのメールソフト・SNS等から自分で送信する。
案件詳細側の「初回メッセージを送信した」ボタンは、その送信事実をあとから
記録するだけであり、送信そのものはこのモジュールの責務ではない。

Streamlit・Supabase・Anthropicのいずれにも依存しない。プレーンな文字列だけを
受け取る純粋関数なので、偽データだけで外部通信なしにテストできる
（cases.py / contact_info.py / sender_profiles.py と同じ方針）。
"""

from __future__ import annotations

from dataclasses import dataclass

# ── 連絡チャネル（cases.CHANNEL_OPTIONS / contact_info.resolve_priority()の
#    kindと同じ値を、依存を増やさずにここでも定義する。cases.py が同じ理由で
#    contact_info.pyのkindを再定義しているのと同じ考え方）────────────────────
CHANNEL_OPTIONS = (
    "email", "facebook", "instagram", "form", "linkedin", "official", "listing", "none",
)

_DM_CHANNELS = ("instagram", "facebook")
_LINKEDIN_CHANNEL = "linkedin"

_NAME_PLACEHOLDER = "[Your Name]"
_UNKNOWN_MAKER_MARKERS = {"", "不明", "unknown", "n/a", "na", "null", "none"}

USAGE_NOTE_EMAIL = "メールの件名・本文としてそのまま使えます。送信はお使いのメールソフトから行ってください。"
USAGE_NOTE_OFFICIAL = "公式サイトの問い合わせ窓口に貼り付けてご利用ください（件名の欄が無ければ省略してください）。"
USAGE_NOTE_FORM = "問い合わせフォームの本文欄に貼り付けてご利用ください（件名の欄が無ければ省略してください）。"
USAGE_NOTE_LISTING = "クラウドファンディングページの「クリエイターへメッセージ」機能等に貼り付けてご利用ください。"
USAGE_NOTE_GENERIC = "連絡先チャネルが未確定のため、汎用的なメール文面を用意しました。実際に使うチャネルに合わせて調整してください。"
USAGE_NOTE_DM = "Instagram／FacebookのDM（メッセージ）にそのまま貼り付けてご利用ください。件名はありません。"
USAGE_NOTE_LINKEDIN = "LinkedInのメッセージ欄にそのまま貼り付けてご利用ください。件名はありません。"

_USAGE_NOTES_BY_CHANNEL = {
    "email": USAGE_NOTE_EMAIL,
    "official": USAGE_NOTE_OFFICIAL,
    "form": USAGE_NOTE_FORM,
    "listing": USAGE_NOTE_LISTING,
    "none": USAGE_NOTE_GENERIC,
}


@dataclass
class ApproachMessage:
    channel: str
    uses_subject: bool
    subject: str
    body: str
    usage_note: str


def _clean(value) -> str:
    return str(value or "").strip()


def _product_label(product_name) -> str:
    return _clean(product_name) or "your product"


def _known_maker(maker_name) -> str:
    """メーカー名が実質的に不明（空・「不明」等）なら空文字を返す

    「不明」のまま英文の宛名（Dear 不明 Team,）に混入させないための処理
    （research_crowdfunding.py の「不明」が営業メールに混入しない、という
    既存の方針と同じ考え方）。
    """
    name = _clean(maker_name)
    return "" if name.lower() in _UNKNOWN_MAKER_MARKERS else name


def _sender_name_or_placeholder(sender_name) -> str:
    return _clean(sender_name) or _NAME_PLACEHOLDER


def _sender_intro(sender_name, sender_company) -> str:
    name = _clean(sender_name)
    company = _clean(sender_company)
    if not name:
        return "I am based in Japan and work with overseas crowdfunding brands."
    if company:
        return f"My name is {name} from {company}, and I am based in Japan."
    return f"My name is {name}, and I am based in Japan."


def _signature(sender_name, sender_company) -> str:
    name = _sender_name_or_placeholder(sender_name)
    company = _clean(sender_company)
    return f"{name}\n{company}" if company else name


# ── 書式（長文の正式な文面：メール・公式サイト・問い合わせフォーム・掲載ページ・未設定） ──

def _formal_subject(product: str) -> str:
    return f"Introducing {product} to the Japanese Market"


def _formal_body(product: str, maker_known: str, sender_name, sender_company) -> str:
    greeting = f"Dear {maker_known} Team," if maker_known else "Hello,"
    intro = _sender_intro(sender_name, sender_company)
    signature = _signature(sender_name, sender_company)
    return (
        f"{greeting}\n\n"
        f"{intro}\n\n"
        f"I came across {product} and was very impressed by its concept and design.\n\n"
        "I am currently looking for unique overseas products to introduce to the "
        "Japanese market through crowdfunding platforms such as Makuake and CAMPFIRE.\n\n"
        f"I believe {product} could resonate well with Japanese consumers, and I would "
        "love to discuss the possibility of a Japan launch together.\n\n"
        "Would you be open to a short conversation about this?\n\n"
        "Thank you very much for your time.\n\n"
        f"Best regards,\n{signature}"
    )


# ── 書式（短文のDM：Instagram・Facebook） ────────────────────────────────────────

def _dm_body(product: str, sender_name, sender_company) -> str:
    name = _sender_name_or_placeholder(sender_name)
    company = _clean(sender_company)
    who = f"{name} from {company}" if company else name
    return (
        f"Hi! I'm {who}, based in Japan.\n\n"
        f"I really love {product} and think it could do great in the Japanese market.\n\n"
        "I'm exploring bringing unique overseas products to Japan through crowdfunding "
        "platforms such as Makuake and CAMPFIRE. Would you be open to a quick chat about "
        "a possible Japan launch?\n\n"
        f"Thank you!\n{name}"
    )


# ── 書式（LinkedIn：短文だが宛名は入れる） ───────────────────────────────────────

def _linkedin_body(product: str, maker_known: str, sender_name, sender_company) -> str:
    greeting = f"Hi {maker_known} team," if maker_known else "Hi,"
    intro = _sender_intro(sender_name, sender_company)
    name = _sender_name_or_placeholder(sender_name)
    return (
        f"{greeting}\n\n"
        f"{intro}\n\n"
        f"I came across {product} and think it could do well in the Japanese market "
        "through crowdfunding platforms such as Makuake and CAMPFIRE.\n\n"
        "I'd love to connect and explore a possible partnership for a Japan launch. "
        "Would you be open to a short chat?\n\n"
        f"Best,\n{name}"
    )


def build_approach_message(
    product_name: str = "",
    maker_name: str = "",
    channel: str = "none",
    sender_name: str = "",
    sender_company: str = "",
) -> ApproachMessage:
    """案件1件分の、連絡チャネルに合わせた初回アプローチ文面を組み立てる

    channel が CHANNEL_OPTIONS の値でない場合は "none"（汎用メール文面）として
    扱う（未知の値で画面を落とさない）。
    """
    product = _product_label(product_name)
    maker_known = _known_maker(maker_name)
    channel = channel if channel in CHANNEL_OPTIONS else "none"

    if channel == _LINKEDIN_CHANNEL:
        body = _linkedin_body(product, maker_known, sender_name, sender_company)
        return ApproachMessage(channel=channel, uses_subject=False, subject="",
                               body=body, usage_note=USAGE_NOTE_LINKEDIN)

    if channel in _DM_CHANNELS:
        body = _dm_body(product, sender_name, sender_company)
        return ApproachMessage(channel=channel, uses_subject=False, subject="",
                               body=body, usage_note=USAGE_NOTE_DM)

    subject = _formal_subject(product)
    body = _formal_body(product, maker_known, sender_name, sender_company)
    note = _USAGE_NOTES_BY_CHANNEL.get(channel, USAGE_NOTE_GENERIC)
    return ApproachMessage(channel=channel, uses_subject=True, subject=subject,
                           body=body, usage_note=note)
