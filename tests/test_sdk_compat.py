# -*- coding: utf-8 -*-
"""実際にインストールされている Anthropic SDK との引数互換テスト

実行: python tests/test_sdk_compat.py

偽クライアントは何でも受け取ってしまうため、SDK側の破壊的変更を検出できない。
ここでは実SDKの messages.create のシグネチャに対して、アプリが実際に渡している
引数が bind できるかを検証する。

このテストは外部AI APIへHTTPリクエストを送らない。
create() は一度も呼ばず、inspect.signature による束縛検査だけを行う。
（Anthropic クライアントの生成自体は通信を伴わない。APIキーもダミー）
"""

import inspect
import sys

from _harness import run  # noqa: E402
import research_crowdfunding as r  # noqa: E402

# アプリが現時点で messages.create に渡している引数（増減したら気づけるようにする）
EXPECTED_KWARGS = {"model", "max_tokens", "messages"}


class _FakeMessages:
    """アプリが渡した引数だけを捕まえて、送信前に中断する"""

    def __init__(self, box):
        self.box = box

    def create(self, **kwargs):
        self.box.update(kwargs)
        raise RuntimeError("captured")


class _FakeClient:
    def __init__(self, box):
        self.messages = _FakeMessages(box)


def _actual_kwargs() -> dict:
    """本番と同じ経路でアプリが組み立てた create の引数を取り出す"""
    box: dict = {}
    r.analyze_with_claude(
        {"name": "X", "maker": "M", "platform": "Kickstarter",
         "raised_usd": 1, "raised_jpy": 1, "backers": 1,
         "genre": "T", "url": "u", "description": "d"},
        _FakeClient(box),
    )
    assert box, "analyze_with_claude が messages.create を呼んでいない"
    return box


def _real_signature() -> inspect.Signature:
    """実インストール済み SDK の messages.create のシグネチャ（通信しない）"""
    import anthropic
    client = anthropic.Anthropic(api_key="dummy-key-not-used")
    return inspect.signature(client.messages.create)


# ── 実SDKとの整合 ─────────────────────────────────────────────────────────────

def test_app_kwargs_bind_to_real_sdk():
    """アプリが渡す引数が実SDKの messages.create で受け付けられる"""
    sig = _real_signature()
    try:
        sig.bind(**_actual_kwargs())
    except TypeError as e:
        raise AssertionError(f"実SDKが引数を受け付けない: {e}") from None


def test_unknown_kwarg_is_rejected():
    """未知の引数を足すと bind に失敗する（この検査が機能していることの確認）"""
    sig = _real_signature()
    kwargs = dict(_actual_kwargs())
    kwargs["definitely_not_a_real_parameter"] = 1
    try:
        sig.bind(**kwargs)
    except TypeError:
        return
    raise AssertionError("未知の引数が通ってしまい、互換検査が機能していない")


def test_temperature_is_not_passed():
    """temperature を渡さない（新しいSDKでは削除された引数）"""
    assert "temperature" not in _actual_kwargs()


def test_required_kwargs_are_kept():
    """model・max_tokens・messages が渡され続けている"""
    kwargs = _actual_kwargs()
    for key in ("model", "max_tokens", "messages"):
        assert key in kwargs, f"{key} が渡されていない"
    assert kwargs["model"] == r.MODEL_ID, str(kwargs["model"])
    assert isinstance(kwargs["max_tokens"], int) and kwargs["max_tokens"] > 0
    assert kwargs["messages"] and kwargs["messages"][0]["role"] == "user"
    assert kwargs["messages"][0]["content"].strip(), "プロンプトが空"


def test_kwarg_set_is_unchanged():
    """渡す引数の顔ぶれが変わったら気づけるようにする

    system など新たに必要な引数を足したときは EXPECTED_KWARGS も更新し、
    実SDKで bind できることを上のテストで確認すること。
    """
    assert set(_actual_kwargs()) == EXPECTED_KWARGS, \
        str(set(_actual_kwargs()) ^ EXPECTED_KWARGS)


def test_model_id_is_accepted_as_string():
    """MODEL_ID が文字列として定義されている（SDKはモデル名を文字列で受ける）"""
    assert isinstance(r.MODEL_ID, str) and r.MODEL_ID.strip()


if __name__ == "__main__":
    sys.exit(run(dict(globals()), "実SDK引数互換テスト（HTTP通信なし）"))
