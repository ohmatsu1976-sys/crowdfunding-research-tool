# -*- coding: utf-8 -*-
"""モジュールの読み込み順序に依存しないことの回帰テスト（本番起動不能の再発防止）

実行: python tests/test_import_order.py

案件管理（cases_ui.py）とマイ候補リスト・画面切替（candidates_ui.py）は、
画面間で共有するsession_stateキーを、依存の無い ui_state_keys.py から
読む（どちらも互いをimportしない）。以前は cases_ui.py が
candidates_ui.py の属性をモジュール読み込み時に参照しており、本番で
初期化順序によって AttributeError になり、アプリが起動不能になる不具合が
あった（詳細は ui_state_keys.py のdocstring参照）。

ここでは実際に「クリーンなPythonプロセス」でimportし直すことで確認する。
同一プロセス内でのimportは sys.modules にキャッシュされ、2回目以降の
importは（既に壊れていても）常に成功して見えるため、再発の検出には
使えない（この不具合自体も、テスト実行時のimportキャッシュのせいで
見つからなかった）。
"""

import ast
import subprocess
import sys
from pathlib import Path

from _harness import run  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def _imported_module_names(py_file: Path) -> set:
    """そのファイルのトップレベルで実際にimportしているモジュール名の集合を返す

    （docstring・コメント中に「import」という単語が出てくるだけの行を
    誤検出しないよう、文字列の前方一致ではなく ast で構文解析する）。
    """
    tree = ast.parse(py_file.read_text(encoding="utf-8"), str(py_file))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def _run_clean(code: str) -> subprocess.CompletedProcess:
    """リポジトリをカレントディレクトリにした、まったく新しいPythonプロセスで実行する"""
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(ROOT), capture_output=True, text=True, timeout=60,
    )


def test_import_cases_ui_alone_in_clean_process():
    result = _run_clean("import cases_ui")
    assert result.returncode == 0, result.stderr


def test_import_candidates_ui_alone_in_clean_process():
    result = _run_clean("import candidates_ui")
    assert result.returncode == 0, result.stderr


def test_import_cases_ui_then_candidates_ui_in_clean_process():
    """cases_ui → candidates_ui の順でも読み込みが成功する"""
    result = _run_clean("import cases_ui; import candidates_ui")
    assert result.returncode == 0, result.stderr


def test_import_candidates_ui_then_cases_ui_in_clean_process():
    """candidates_ui → cases_ui の順でも読み込みが成功する（本番で実際に失敗した順序）"""
    result = _run_clean("import candidates_ui; import cases_ui")
    assert result.returncode == 0, result.stderr


def test_import_streamlit_app_in_clean_process_raises_no_attribute_error():
    """アプリ本体（streamlit_app.py）をクリーンプロセスでimportしてもAttributeErrorが出ない

    st.set_page_config等はStreamlitの実行コンテキスト外では警告を出すだけで
    例外にはならない（AppTestベースの既存テストで動作そのものは確認済み）。
    ここで検出したいのは、importの連鎖だけで起きるAttributeErrorの再発。
    """
    result = _run_clean("import streamlit_app")
    assert "AttributeError" not in result.stderr, result.stderr
    assert result.returncode == 0, result.stderr


def test_cases_ui_does_not_import_candidates_ui():
    """cases_ui.py が candidates_ui.py を一切importしないことの静的検査"""
    imported = _imported_module_names(ROOT / "cases_ui.py")
    assert "candidates_ui" not in imported, imported


def test_candidates_ui_does_not_import_cases_ui():
    """candidates_ui.py も cases_ui.py を一切importしないことの静的検査（逆方向の再発防止）"""
    imported = _imported_module_names(ROOT / "candidates_ui.py")
    assert "cases_ui" not in imported, imported


def test_ui_state_keys_has_no_imports_at_all():
    """ui_state_keys.py はどのモジュールも一切importしない（依存ゼロであることの検査）"""
    imported = _imported_module_names(ROOT / "ui_state_keys.py")
    assert imported == set(), imported


if __name__ == "__main__":
    sys.exit(run(dict(globals()), "import順序の回帰テスト（起動不能の再発防止）"))
