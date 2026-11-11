# -*- coding: utf-8 -*-
"""連絡先（メール・問い合わせフォーム）の検証ロジックのテスト

実行: python tests/test_contact_info.py
ネットワーク・外部AI APIは一切使わない（純粋な文字列検証のみ）。

「メール」をクリックすると空白画面になる不具合（mailto: がメールソフト未設定の
端末で機能しない／空の宛先のまま使っていた）の修正に対するテスト。
"""

import re
import sys

from _harness import run  # noqa: E402
import contact_info  # noqa: E402
from summary_table import build_summary_html  # noqa: E402


# ── normalize_email ───────────────────────────────────────────────────────────

def test_valid_email_is_kept():
    """1. 正常なメールアドレスはそのまま返す"""
    assert contact_info.normalize_email("contact@example.com") == "contact@example.com"


def test_empty_string_email_is_rejected():
    """2. 空文字は無効として扱う"""
    assert contact_info.normalize_email("") == ""


def test_none_email_is_rejected():
    """3. None は無効として扱う（推測で補完しない）"""
    assert contact_info.normalize_email(None) == ""


def test_mailto_only_with_no_destination_is_rejected():
    """4. mailto: だけで宛先が無い値は無効として扱う"""
    assert contact_info.normalize_email("mailto:") == ""
    assert contact_info.normalize_email("mailto: ") == ""


def test_malformed_email_is_rejected():
    """5. 不正な形式のメールアドレスは無効として扱う"""
    for bad in ("not-an-email", "foo@", "@example.com", "foo@bar", "foo bar@example.com"):
        assert contact_info.normalize_email(bad) == "", bad


def test_unverified_placeholder_is_rejected():
    """既存仕様の「未確認」「N/A」等のプレースホルダも無効として扱う"""
    for placeholder in ("未確認", "N/A", "none", "None", "null"):
        assert contact_info.normalize_email(placeholder) == "", placeholder


def test_mailto_prefixed_valid_address_is_unwrapped():
    """mailto:接頭辞付きでも、宛先が有効なら通常のメールアドレスとして取り出す"""
    assert contact_info.normalize_email("mailto:contact@example.com") == "contact@example.com"


# ── normalize_contact_url ─────────────────────────────────────────────────────

def test_valid_https_form_url_is_kept():
    """6. 問い合わせフォームURL（https）は有効として扱う"""
    assert contact_info.normalize_contact_url("https://example.com/contact") == \
        "https://example.com/contact"


def test_valid_http_form_url_is_kept():
    assert contact_info.normalize_contact_url("http://example.com/contact") == \
        "http://example.com/contact"


def test_empty_or_placeholder_form_url_is_rejected():
    for bad in ("", "未確認", None, "N/A"):
        assert contact_info.normalize_contact_url(bad) == "", bad


def test_non_http_scheme_form_url_is_rejected():
    """10. http/https以外（javascript:等）や空リンクはクリック可能にしない"""
    for bad in ("javascript:alert(1)", "ftp://example.com", "https://", "http://", "  "):
        assert contact_info.normalize_contact_url(bad) == "", bad


# ── resolve（1行からの解決） ───────────────────────────────────────────────────

def test_resolve_email_only():
    """1. メールアドレスのみ取得できた場合"""
    row = {"メールアドレス": "maker@example.com", "問い合わせフォームURL": "未確認"}
    result = contact_info.resolve(row)
    assert result == {"email": "maker@example.com", "form_url": ""}


def test_resolve_form_only():
    """6. 問い合わせフォームURLだけ取得できた場合"""
    row = {"メールアドレス": "未確認", "問い合わせフォームURL": "https://example.com/contact"}
    result = contact_info.resolve(row)
    assert result == {"email": "", "form_url": "https://example.com/contact"}


def test_resolve_both():
    """7. メールと問い合わせフォームの両方を取得できた場合"""
    row = {"メールアドレス": "maker@example.com",
           "問い合わせフォームURL": "https://example.com/contact"}
    result = contact_info.resolve(row)
    assert result == {"email": "maker@example.com",
                       "form_url": "https://example.com/contact"}


def test_resolve_neither():
    """8. どちらも取得できない場合"""
    row = {"メールアドレス": "未確認", "問い合わせフォームURL": "未確認"}
    result = contact_info.resolve(row)
    assert result == {"email": "", "form_url": ""}


# ── summary_table.py 側のHTML表示（画面に出る実際の文字列） ──────────────────────

def _cell(row: dict) -> str:
    base = {"優先度": "A", "判定の確度": "データ取得済み", "商品名": "商品", "メーカー名": "M",
            "プラットフォーム": "KS", "調達額(円)": 1000, "日本で売れそうな理由": "理由",
            "掲載URL": "https://example.com/p", "種別": "", "アプローチ先リンク": ""}
    base.update(row)
    html = build_summary_html([base])
    return html.split("<tbody>")[1]


def test_display_shows_address_and_mail_link_when_email_available():
    """メールアドレスが取得できた場合、アドレス自体とメール作成リンクの両方を出す"""
    body = _cell({"メールアドレス": "maker@example.com", "問い合わせフォームURL": "未確認"})
    assert "maker@example.com" in body
    assert 'href="mailto:maker@example.com"' in body
    assert "メールを作成" in body


def test_display_shows_form_link_when_form_only():
    """問い合わせフォームだけの場合、「メール」ではなく「問い合わせフォーム」と表示する"""
    body = _cell({"メールアドレス": "未確認",
                 "問い合わせフォームURL": "https://example.com/contact"})
    assert "問い合わせフォーム" in body
    assert 'href="https://example.com/contact"' in body
    assert "メールを作成" not in body, "フォームしか無いのにメール表記が出ている"


def test_display_shows_both_when_both_available():
    """メールと問い合わせフォームの両方がある場合、両方を表示する"""
    body = _cell({"メールアドレス": "maker@example.com",
                 "問い合わせフォームURL": "https://example.com/contact"})
    assert "maker@example.com" in body
    assert 'href="mailto:maker@example.com"' in body
    assert 'href="https://example.com/contact"' in body
    assert "問い合わせフォーム" in body


def test_display_shows_unobtained_when_truly_nothing_available():
    """8. メール・フォーム・フォールバック先も無い場合は「連絡先未取得」になる"""
    body = _cell({"メールアドレス": "未確認", "問い合わせフォームURL": "未確認",
                 "掲載URL": "", "種別": "—", "アプローチ先リンク": ""})
    assert "連絡先未取得" in body
    last_td = re.search(r"<td>(.*?)</td>\s*</tr>", body, re.S).group(1)
    assert "<a " not in last_td, "連絡先未取得なのにクリック可能なリンクが出ている"


def test_display_never_emits_bare_mailto_without_visible_address():
    """mailto:のみで宛先が空、または不正な値の場合はリンクを一切出さない"""
    body = _cell({"メールアドレス": "mailto:", "問い合わせフォームURL": "not-a-url",
                 "掲載URL": "", "種別": "—", "アプローチ先リンク": ""})
    assert "mailto:" not in body
    assert "連絡先未取得" in body


def test_display_invalid_email_does_not_produce_clickable_link():
    """5. 不正なメールアドレスはリンク化されない（連絡先未取得側へ落ちる）"""
    body = _cell({"メールアドレス": "not-an-email", "問い合わせフォームURL": "未確認",
                 "掲載URL": "", "種別": "—", "アプローチ先リンク": ""})
    assert "mailto:not-an-email" not in body
    assert "連絡先未取得" in body


if __name__ == "__main__":
    sys.exit(run(dict(globals()), "連絡先（メール・問い合わせフォーム）検証テスト"))
