# -*- coding: utf-8 -*-
"""初回アプローチ文面のチャネル別テンプレート生成のテスト

実行: python tests/test_approach_messages.py
ネットワーク・外部AI APIは一切使わない（決定論的なテンプレート組み立てのみ）。
"""

from _harness import run  # noqa: E402
import approach_messages as am  # noqa: E402


# ── 基本：メール（正式な文面・件名あり） ────────────────────────────────────────

def test_email_channel_has_subject_and_formal_greeting():
    """1. メールは件名ありで、メーカー名が分かれば宛名に使う"""
    msg = am.build_approach_message(
        product_name="Vision Master Smart AI Spotting Scope", maker_name="Acme Optics",
        channel="email", sender_name="Taro Yamada", sender_company="")
    assert msg.uses_subject is True
    assert msg.subject
    assert "Dear Acme Optics Team," in msg.body
    assert "Vision Master Smart AI Spotting Scope" in msg.body
    assert "Taro Yamada" in msg.body


def test_unknown_maker_does_not_leak_into_formal_greeting():
    """2. メーカー名が「不明」の場合、英文の宛名に「不明」を混入させない"""
    msg = am.build_approach_message(
        product_name="Some Gadget", maker_name="不明", channel="email",
        sender_name="Taro Yamada")
    assert "不明" not in msg.body
    assert "Dear" not in msg.body
    assert msg.body.startswith("Hello,")


def test_empty_maker_name_falls_back_to_generic_greeting():
    """3. メーカー名が空文字でも「Dear  Team,」のような欠落表示にしない"""
    msg = am.build_approach_message(product_name="Some Gadget", maker_name="",
                                    channel="email")
    assert "Dear  Team" not in msg.body
    assert msg.body.startswith("Hello,")


# ── 送信者名・会社名の反映 ──────────────────────────────────────────────────────

def test_missing_sender_name_uses_placeholder():
    """4. 送信者プロフィール未選択でも [Your Name] のプレースホルダで文面が壊れない"""
    msg = am.build_approach_message(product_name="Gadget", maker_name="Acme",
                                    channel="email", sender_name="", sender_company="")
    assert "[Your Name]" in msg.body


def test_sender_name_without_company_has_no_dangling_company_line():
    """5. 会社名が無い場合、署名・自己紹介に空の会社名行が残らない"""
    msg = am.build_approach_message(product_name="Gadget", maker_name="Acme",
                                    channel="email", sender_name="Taro Yamada",
                                    sender_company="")
    assert "My name is Taro Yamada, and I am based in Japan." in msg.body
    assert " from " not in msg.body.split("I came across")[0]
    assert msg.body.rstrip().endswith("Taro Yamada")


def test_sender_name_with_company_appears_in_intro_and_signature():
    """6. 会社名がある場合、自己紹介と署名の両方に反映される"""
    msg = am.build_approach_message(product_name="Gadget", maker_name="Acme",
                                    channel="email", sender_name="Taro Yamada",
                                    sender_company="Base on Base LLC")
    assert "from Base on Base LLC" in msg.body
    assert msg.body.rstrip().endswith("Taro Yamada\nBase on Base LLC")


# ── チャネル別の分岐 ────────────────────────────────────────────────────────────

def test_instagram_channel_has_no_subject_and_no_formal_greeting():
    """7. InstagramはDM想定：件名なし・Dear宛名なしの短文"""
    msg = am.build_approach_message(product_name="Gadget", maker_name="Acme",
                                    channel="instagram", sender_name="Taro Yamada")
    assert msg.uses_subject is False
    assert msg.subject == ""
    assert "Dear" not in msg.body
    assert "Gadget" in msg.body


def test_facebook_channel_has_no_subject_and_no_formal_greeting():
    """8. FacebookもInstagramと同じDM想定の短文"""
    msg = am.build_approach_message(product_name="Gadget", maker_name="Acme",
                                    channel="facebook", sender_name="Taro Yamada")
    assert msg.uses_subject is False
    assert msg.subject == ""
    assert "Dear" not in msg.body


def test_linkedin_channel_has_no_subject_but_uses_maker_greeting():
    """9. LinkedInは件名なしだが、メーカー名が分かれば宛名に使う"""
    msg = am.build_approach_message(product_name="Gadget", maker_name="Acme",
                                    channel="linkedin", sender_name="Taro Yamada")
    assert msg.uses_subject is False
    assert msg.subject == ""
    assert "Hi Acme team," in msg.body
    assert "connect" in msg.body.lower()


def test_official_channel_uses_formal_style_with_official_usage_note():
    """10. 公式サイトは正式な文面＋専用の使い方ガイド文"""
    msg = am.build_approach_message(product_name="Gadget", maker_name="Acme",
                                    channel="official")
    assert msg.uses_subject is True
    assert msg.usage_note == am.USAGE_NOTE_OFFICIAL


def test_form_channel_uses_formal_style_with_form_usage_note():
    """11. 問い合わせフォームも正式な文面だが、フォーム専用のガイド文になる"""
    msg = am.build_approach_message(product_name="Gadget", maker_name="Acme",
                                    channel="form")
    assert msg.usage_note == am.USAGE_NOTE_FORM


def test_listing_channel_uses_formal_style_with_listing_usage_note():
    """12. 商品掲載ページはクラファンページ用のガイド文になる"""
    msg = am.build_approach_message(product_name="Gadget", maker_name="Acme",
                                    channel="listing")
    assert msg.usage_note == am.USAGE_NOTE_LISTING


def test_none_channel_falls_back_to_generic_formal_style():
    """13. 連絡先未確定（none）でも汎用的な正式文面で落ちない"""
    msg = am.build_approach_message(product_name="Gadget", maker_name="Acme",
                                    channel="none")
    assert msg.uses_subject is True
    assert msg.usage_note == am.USAGE_NOTE_GENERIC


def test_unknown_channel_value_is_treated_as_none():
    """14. 想定外のチャネル値が渡っても画面を落とさず、noneと同じ扱いにする"""
    msg = am.build_approach_message(product_name="Gadget", maker_name="Acme",
                                    channel="unknown-channel-xyz")
    assert msg.uses_subject is True
    assert msg.usage_note == am.USAGE_NOTE_GENERIC


# ── 商品名の欠落 ────────────────────────────────────────────────────────────────

def test_empty_product_name_does_not_crash_and_has_fallback_text():
    """15. 商品名が空でも例外を出さず、汎用的な言い回しで埋める"""
    msg = am.build_approach_message(product_name="", maker_name="Acme", channel="email")
    assert "your product" in msg.body


# ── 全チャネルの網羅（落ちないこと・本文が必ず埋まること） ───────────────────────

def test_every_channel_option_builds_without_error():
    """16. CHANNEL_OPTIONSの全値で、本文が空にならず例外も出ない"""
    for channel in am.CHANNEL_OPTIONS:
        msg = am.build_approach_message(product_name="Gadget", maker_name="Acme",
                                        channel=channel, sender_name="Taro Yamada")
        assert msg.channel == channel
        assert msg.body
        assert msg.usage_note


if __name__ == "__main__":
    import sys
    sys.exit(run(dict(globals()), "初回アプローチ文面（approach_messages.py）のテスト"))
