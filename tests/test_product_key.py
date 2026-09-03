# -*- coding: utf-8 -*-
"""URL正規化・対象ドメイン検証の決定論テスト

実行: python tests/test_product_key.py

Streamlit にも Supabase にも依存しない。外部AI APIもネットワークも使わない。
"""

import sys

from _harness import run  # noqa: E402
import product_key as pk  # noqa: E402


# ── 正常系：3プラットフォームの正規化 ─────────────────────────────────────────

def test_normalizes_kickstarter_url():
    key = pk.normalize_url("https://www.kickstarter.com/projects/foo/bar-baz")
    assert key == "https://kickstarter.com/projects/foo/bar-baz", key


def test_normalizes_indiegogo_url():
    key = pk.normalize_url("https://www.indiegogo.com/projects/foo-bar")
    assert key == "https://indiegogo.com/projects/foo-bar", key


def test_normalizes_zeczec_url():
    key = pk.normalize_url("https://www.zeczec.com/projects/foo-bar")
    assert key == "https://zeczec.com/projects/foo-bar", key


def test_all_three_platforms_are_allowed_hosts():
    for url in ("https://kickstarter.com/projects/a/b",
                "https://indiegogo.com/projects/a",
                "https://zeczec.com/projects/a"):
        assert pk.is_allowed_host(url) is True, url


# ── 正規化の詳細 ──────────────────────────────────────────────────────────────

def test_strips_query_and_fragment():
    key = pk.normalize_url("https://kickstarter.com/projects/a/b?ref=xyz#pledge")
    assert key == "https://kickstarter.com/projects/a/b", key


def test_strips_kickstarter_tab_suffix():
    base = pk.normalize_url("https://kickstarter.com/projects/a/b")
    for tab in ("/description", "/comments", "/faqs", "/risks",
                "/rewards", "/updates", "/community", "/creator"):
        assert pk.normalize_url(f"https://kickstarter.com/projects/a/b{tab}") == base, tab


def test_strips_manual_amount_suffix():
    key = pk.normalize_url("https://indiegogo.com/projects/foo  $57,485")
    assert key == "https://indiegogo.com/projects/foo", key


def test_strips_trailing_slash():
    key = pk.normalize_url("https://kickstarter.com/projects/a/b/")
    assert key == "https://kickstarter.com/projects/a/b", key


def test_host_is_case_insensitive():
    key = pk.normalize_url("https://ZECZEC.com/projects/Foo")
    assert key == "https://zeczec.com/projects/Foo", key


def test_www_prefix_is_removed():
    key = pk.normalize_url("https://www.kickstarter.com/projects/a/b")
    assert key.startswith("https://kickstarter.com/"), key


def test_same_product_different_url_forms_produce_same_key():
    a = pk.normalize_url("https://www.kickstarter.com/projects/a/b?ref=1")
    b = pk.normalize_url("https://kickstarter.com/projects/a/b/description/")
    assert a == b, (a, b)


# ── 拒否系：形式 ──────────────────────────────────────────────────────────────

def test_rejects_empty_and_none():
    assert pk.normalize_url("") == ""
    assert pk.normalize_url(None) == ""
    assert pk.normalize_url("   ") == ""


def test_rejects_http_scheme():
    assert pk.normalize_url("http://kickstarter.com/projects/a/b") == ""


def test_rejects_root_path():
    assert pk.normalize_url("https://kickstarter.com/") == ""
    assert pk.normalize_url("https://kickstarter.com") == ""


def test_rejects_too_short_after_normalization():
    # ホストと極端に短いパスだけでは MIN_LENGTH を満たさない場合がある
    key = pk.normalize_url("https://zeczec.com/a")
    assert key == "" or len(key) >= pk.MIN_LENGTH


def test_rejects_too_long_url():
    long_path = "/projects/" + "a" * 2000
    assert pk.normalize_url(f"https://kickstarter.com{long_path}") == ""


# ── 拒否系：ドメイン偽装（本質） ───────────────────────────────────────────────

def test_rejects_unrelated_domain():
    assert pk.normalize_url("https://amazon.co.jp/dp/foo") == ""
    assert pk.is_allowed_host("https://amazon.co.jp/dp/foo") is False


def test_rejects_subdomain_style_spoof():
    """zeczec.com.example.com のような「後ろに何か続く」偽装を拒否する"""
    url = "https://zeczec.com.example.com/projects/foo"
    assert pk.normalize_url(url) == ""
    assert pk.is_allowed_host(url) is False


def test_rejects_prefix_style_spoof():
    """example-zeczec.com のような「似た名前」偽装を拒否する"""
    url = "https://example-zeczec.com/projects/foo"
    assert pk.normalize_url(url) == ""
    assert pk.is_allowed_host(url) is False


def test_rejects_all_three_spoof_variants():
    """3プラットフォームすべてで同じ偽装パターンを拒否する"""
    for host in ("kickstarter.com", "indiegogo.com", "zeczec.com"):
        for spoof in (f"{host}.evil.com", f"evil-{host}", f"evil{host}",
                      f"not{host}.com"):
            url = f"https://{spoof}/projects/foo"
            assert pk.normalize_url(url) == "", url
            assert pk.is_allowed_host(url) is False, url


def test_rejects_userinfo_host_spoof():
    """https://kickstarter.com@evil.com/... のようなURLは実ホストのevil.comで判定する"""
    url = "https://kickstarter.com@evil.com/projects/foo"
    assert pk.normalize_url(url) == ""
    assert pk.is_allowed_host(url) is False


def test_rejects_path_containing_domain_name():
    """パスにドメイン名の文字列が含まれるだけでは許可しない（部分一致で判定しない証拠）"""
    url = "https://evil.com/kickstarter.com/projects/foo"
    assert pk.normalize_url(url) == ""
    assert pk.is_allowed_host(url) is False


def test_host_check_uses_url_parsing_not_substring():
    """'kickstarter.com' という文字列がURLのどこかに含まれるだけでは判定しない"""
    tricky_urls = [
        "https://evil.com/?u=kickstarter.com",
        "https://evil.com/kickstarter.com",
        "https://evil.com#kickstarter.com",
    ]
    for url in tricky_urls:
        assert "kickstarter.com" in url, url  # 文字列としては含まれている
        assert pk.is_allowed_host(url) is False, url  # それでも許可されない


# ── ALLOWED_HOSTS の一覧そのもの ───────────────────────────────────────────────

def test_allowed_hosts_is_exactly_three_platforms():
    assert pk.ALLOWED_HOSTS == frozenset(
        {"kickstarter.com", "indiegogo.com", "zeczec.com"})


if __name__ == "__main__":
    sys.exit(run(dict(globals()), "URL正規化・対象ドメイン検証テスト"))
