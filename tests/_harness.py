# -*- coding: utf-8 -*-
"""テスト共通の小さな実行基盤

新しいパッケージは導入しない。pytest があれば pytest でも動くが、
無くても `python tests/test_offline.py` で単体実行できるようにするためのもの。
"""

import io
import sys
from pathlib import Path

# 親ディレクトリ（アプリ本体）を import 可能にする
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _utf8_stdout() -> None:
    """Windowsのコンソールでも日本語が化けないようにする"""
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    except (AttributeError, ValueError):
        pass


def run(namespace: dict, title: str) -> int:
    """namespace 内の test_* 関数を実行し、失敗数を終了コードで返す"""
    _utf8_stdout()
    tests = [(n, f) for n, f in sorted(namespace.items())
             if n.startswith("test_") and callable(f)]
    print(f"\n{title}  ({len(tests)}件)")
    print("-" * 60)

    failures = []
    for name, func in tests:
        label = (func.__doc__ or name).strip().splitlines()[0]
        try:
            func()
        except AssertionError as e:
            failures.append((name, str(e) or "assert failed"))
            print(f"  [FAIL] {label}\n         {e}")
        except Exception as e:                      # noqa: BLE001
            failures.append((name, f"{type(e).__name__}: {e}"))
            print(f"  [ERROR] {label}\n         {type(e).__name__}: {e}")
        else:
            print(f"  [PASS] {label}")

    print("-" * 60)
    print(f"  成功 {len(tests) - len(failures)} / {len(tests)}")
    if failures:
        print("  失敗:", ", ".join(n for n, _ in failures))
    return len(failures)
