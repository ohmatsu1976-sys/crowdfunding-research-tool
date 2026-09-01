# -*- coding: utf-8 -*-
"""検索結果の保持（st.session_state の読み書き）

Streamlit はボタン操作のたびにスクリプトを先頭から再実行するため、
検索結果をローカル変数に置いたままだと操作の直後に消えてしまう。
ここでは結果をセッション単位で保持する。

この module は Streamlit に依存せず、辞書ライクなオブジェクトを受け取る。
そのため外部APIもブラウザも使わずにテストできる。
"""

from datetime import datetime
from typing import Any, List, MutableMapping, Optional

# session_state のキー（他機能と衝突しないよう search_ で始める）
RESULTS = "search_results"          # 検索結果の行（list[dict]）
QUERY = "search_query"              # その結果を生んだ検索条件（URLの一覧）
EXECUTED_AT = "search_executed_at"  # 検索実行日時（datetime）
STATUS = "search_status"            # 直近の検索の成否: None / "ok" / "error"
ERROR = "search_error"              # 直近のエラーメッセージ
FAILED_URLS = "search_failed_urls"  # 取得できなかったURL
LOG = "search_log"                  # 処理ログ

KEYS = (RESULTS, QUERY, EXECUTED_AT, STATUS, ERROR, FAILED_URLS, LOG)

STATUS_OK = "ok"
STATUS_ERROR = "error"


def _defaults() -> dict:
    return {RESULTS: [], QUERY: [], EXECUTED_AT: None,
            STATUS: None, ERROR: "", FAILED_URLS: [], LOG: []}


def init_state(state: MutableMapping[str, Any]) -> None:
    """未設定のキーだけ初期化する（既存の結果は壊さない）"""
    for key, value in _defaults().items():
        if key not in state:
            state[key] = value


def save_success(state: MutableMapping[str, Any],
                 rows: List[dict],
                 query: List[str],
                 failed_urls: Optional[List[str]] = None,
                 log_lines: Optional[List[str]] = None,
                 executed_at: Optional[datetime] = None) -> bool:
    """検索が正常終了したときだけ結果を置き換える

    行が1件も無い場合は成功とみなさず、以前の結果を残したまま False を返す。
    途中経過を保存しないよう、呼び出しは検索ループ完了後に限る。
    """
    if not rows:
        return False
    state[RESULTS] = list(rows)          # 呼び出し側のリストと共有しない
    state[QUERY] = list(query or [])
    state[EXECUTED_AT] = executed_at or datetime.now()
    state[STATUS] = STATUS_OK
    state[ERROR] = ""
    state[FAILED_URLS] = list(failed_urls or [])
    state[LOG] = list(log_lines or [])
    return True


def record_failure(state: MutableMapping[str, Any], message: str) -> None:
    """検索が失敗したことだけを記録する（以前の正常な結果は消さない）"""
    init_state(state)
    state[STATUS] = STATUS_ERROR
    state[ERROR] = str(message or "検索に失敗しました")


def clear_results(state: MutableMapping[str, Any]) -> None:
    """検索結果・検索条件・日時・エラー表示を初期化する"""
    for key, value in _defaults().items():
        state[key] = value


def has_results(state: MutableMapping[str, Any]) -> bool:
    """表示できる検索結果を持っているか"""
    return bool(state.get(RESULTS))


def get_results(state: MutableMapping[str, Any]) -> List[dict]:
    return list(state.get(RESULTS) or [])


def get_query(state: MutableMapping[str, Any]) -> List[str]:
    return list(state.get(QUERY) or [])


def get_executed_at(state: MutableMapping[str, Any]) -> Optional[datetime]:
    return state.get(EXECUTED_AT)


def get_error(state: MutableMapping[str, Any]) -> str:
    return state.get(ERROR) or ""


def is_showing_previous(state: MutableMapping[str, Any]) -> bool:
    """直近の検索が失敗し、以前の結果を表示している状態か"""
    return state.get(STATUS) == STATUS_ERROR and has_results(state)


def timestamp_label(state: MutableMapping[str, Any]) -> str:
    """CSVのファイル名に使う、結果の実行日時（現在時刻ではない）"""
    executed = get_executed_at(state)
    return (executed or datetime.now()).strftime("%Y%m%d_%H%M")


def describe_query(state: MutableMapping[str, Any], limit: int = 3) -> str:
    """どの検索条件による結果かを1行で説明する"""
    urls = get_query(state)
    if not urls:
        return "検索条件は記録されていません"
    shown = "、".join(u.split("/")[-1][:40] or u for u in urls[:limit])
    more = f" ほか{len(urls) - limit}件" if len(urls) > limit else ""
    return f"{len(urls)}件のURL（{shown}{more}）"
