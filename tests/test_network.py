# -*- coding: utf-8 -*-
"""外部サイトに実アクセスする回帰テスト

実行: python tests/test_network.py

注意:
- 外部サイトの一時的な状態（掲載終了・仕様変更・IPブロック）に依存するため、
  失敗＝必ずしもコードの不具合ではない。切り分けのため test_offline.py と分けている。
- 外部AI APIは呼ばない。本番の分析処理も実行しない。
- 実行するのは読み取り（GET）のみ。
"""

import sys

from _harness import run  # noqa: E402
import research_crowdfunding as r  # noqa: E402

KS = "https://www.kickstarter.com/projects/{}/{}"
SITPACK = ("rest", "sitpack-zen-the-worlds-most-compact-chair-perfect")
NEX = ("niid", "nex-slim-wallet-1cm-magnetic-rfid-and-tap")


def test_kickstarter_direct_fetch():
    """KS本体から商品名・メーカー名・調達額を取得できる"""
    p = r.fetch_project_from_url(KS.format(*SITPACK))
    assert p, "取得できなかった"
    assert p.get("raised_usd", 0) > 0, f"調達額が0: {p.get('raised_usd')}"
    assert p.get("maker"), "メーカー名が空"
    assert p.get("_source") is None, f"本体成功時に代替経路を使っている: {p.get('_source')}"


def test_kicktraq_fallback_returns_full_data():
    """Kicktraq経路で商品名・メーカー名・調達額・支援者数が揃う"""
    p = r._fetch_ks_via_fallback(KS.format(*SITPACK), *SITPACK)
    assert p, "代替経路が None を返した"
    assert p.get("_source") == "kicktraq", f"経路が想定と異なる: {p.get('_source')}"
    assert p.get("maker"), "メーカー名が空"
    assert p.get("raised_usd", 0) > 0 and p.get("backers", 0) > 0
    assert r.is_low_confidence(p) is False, "Kicktraq経路が参考値扱いになっている"


def test_stats_json_fallback_returns_amounts():
    """Kicktraq未収録でも stats.json で調達額・支援者数を取得できる"""
    p = r._fetch_ks_via_fallback(KS.format(*NEX), *NEX)
    assert p, "代替経路が None を返した"
    assert p.get("raised_usd", 0) > 0, "調達額が取得できていない"
    if p.get("_source") == "stats.json":
        assert r.is_low_confidence(p) is True, "stats.json経路が参考値扱いになっていない"
        assert r.usable_maker(p.get("maker", "")) == p.get("maker", ""), \
            f"一般語がメーカー名になっている: {p.get('maker')!r}"


def test_official_site_not_wrong_domain():
    """アカウント名(rest)から無関係な rest.com を公式サイトにしない"""
    site = r.find_creator_site("rest", "Sitpack Zen The Worlds Most Compact Chair", "Sitpack")
    assert "rest.com" not in (site or ""), f"誤ったサイトを返した: {site}"


def test_missing_project_returns_none():
    """存在しない案件では代替経路が None を返す"""
    p = r._fetch_ks_via_fallback(KS.format("zzz", "does-not-exist-xyz-000"),
                                 "zzz", "does-not-exist-xyz-000")
    assert p is None, f"None ではなく {p} を返した"


if __name__ == "__main__":
    sys.exit(run(dict(globals()), "ネットワーク回帰テスト（外部サイトの状態に依存）"))
