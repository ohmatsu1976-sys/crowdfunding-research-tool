# -*- coding: utf-8 -*-
"""検索結果の保持（st.session_state の読み書き）

Streamlit はボタン操作のたびにスクリプトを先頭から再実行するため、
検索結果をローカル変数に置いたままだと操作の直後に消えてしまう。
ここでは結果をセッション単位で保持する。

この module は Streamlit に依存せず、辞書ライクなオブジェクトを受け取る。
そのため外部APIもブラウザも使わずにテストできる。
"""

from datetime import datetime
from typing import Any, Callable, List, MutableMapping, Optional

# session_state のキー（他機能と衝突しないよう search_ で始める）
RESULTS = "search_results"          # 検索結果の行（list[dict]）
QUERY = "search_query"              # その結果を生んだ検索条件（URLの一覧）
EXECUTED_AT = "search_executed_at"  # 検索実行日時（datetime）
STATUS = "search_status"            # 直近の検索の成否: None / "ok" / "error"
ERROR = "search_error"              # 直近のエラーメッセージ
FAILED_URLS = "search_failed_urls"  # 取得できなかったURL
LOG = "search_log"                  # 処理ログ
SCHEMA = "search_schema_version"    # 保持中の結果の列構成のバージョン（int）
NOTICE = "search_notice"            # 移行・初期化を利用者へ知らせる文言

KEYS = (RESULTS, QUERY, EXECUTED_AT, STATUS, ERROR, FAILED_URLS, LOG,
        SCHEMA, NOTICE)

STATUS_OK = "ok"
STATUS_ERROR = "error"


def _defaults() -> dict:
    return {RESULTS: [], QUERY: [], EXECUTED_AT: None,
            STATUS: None, ERROR: "", FAILED_URLS: [], LOG: [],
            SCHEMA: None, NOTICE: ""}


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
                 executed_at: Optional[datetime] = None,
                 schema_version: Optional[int] = None) -> bool:
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
    state[SCHEMA] = schema_version
    state[NOTICE] = ""
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


# ── 列構成の変更に追随する（デプロイ後も開きっぱなしのセッションを守る）──────────

def migrate_state(state: MutableMapping[str, Any],
                  normalize: Callable[[List[dict]], Any],
                  current_version: int) -> str:
    """保持中の結果を現在の列構成へそろえる

    列が追加・変更されたデプロイの後も、開いたままのブラウザセッションが
    古い形式の結果を持ち続けて表示時に落ちることがないようにする。

    - 現在より古いバージョンなら normalize() で新形式へ移行する
    - 安全に移行できない場合は検索結果だけを初期化する（入力欄には触らない）
    - どちらの場合も現在のバージョンを記録し、同じ処理を繰り返さない

    戻り値は利用者へ表示する説明文（何もしなかったときは空文字）。
    """
    init_state(state)
    if state.get(SCHEMA) == current_version:
        return ""
    if not state.get(RESULTS):
        # 表示する結果が無いなら、移行の必要も知らせる必要もない
        state[SCHEMA] = current_version
        return ""
    try:
        rows, missing = normalize(list(state[RESULTS]))
        if not rows:
            raise ValueError("移行後の結果が0件になりました")
        state[RESULTS] = rows
        state[SCHEMA] = current_version
        if missing:
            notice = ("表示中の検索結果は以前の形式だったため、現在の形式へそろえました"
                      f"（補った項目: {'、'.join(missing)}）。"
                      "正確な内容は再検索してご確認ください。")
        else:
            notice = ""
        state[NOTICE] = notice
        return notice
    except Exception as e:
        # 移行できない結果を抱えたままだと毎回同じエラーになるため、結果だけ捨てる
        clear_results(state)
        state[SCHEMA] = current_version
        notice = ("表示中だった検索結果は現在の形式へ移行できなかったため、"
                  f"検索結果のみを初期化しました（理由: {type(e).__name__}）。"
                  "入力欄のURLは残っています。もう一度検索してください。")
        state[NOTICE] = notice
        return notice


def get_notice(state: MutableMapping[str, Any]) -> str:
    """移行・初期化の説明文（表示したら clear_notice で消す）"""
    return state.get(NOTICE) or ""


def clear_notice(state: MutableMapping[str, Any]) -> None:
    state[NOTICE] = ""
