# -*- coding: utf-8 -*-
"""画面間で共有する session_state キー（依存を一切持たない定数モジュール）

candidates_ui.py（画面切替の親）と cases_ui.py（案件管理画面）の両方が
参照する値をここへ正本として置く。どちらのファイルも互いをimportせず、
この依存のないモジュールだけを読む。

【この修正の理由】
以前は、cases_ui.py が candidates_ui.py の属性（candidates_ui.CASES_FLASH_MESSAGE
等）をモジュール読み込み時（LAST_MESSAGE = candidates_ui.CASES_FLASH_MESSAGE の
ような代入文）に参照していた。本番で、candidates_ui.py の初期化が完了する前に
この属性へアクセスされ AttributeError になる不具合が発生した
（Streamlitは複数セッションを並行して処理するため、importの完了順序に
依存するコードは本番で実際に競合しうる）。
このファイルはどのプロジェクト内モジュールもimportしない（streamlitにも
依存しない）ため、cases_ui.py・candidates_ui.py のどちらが先に読み込まれても、
どちらも常に完全に初期化された状態でこの値を読める。

新しいキー・画面を追加する場合も、2つの画面モジュールの間で直接
import させずに、必要な値はここへ追加すること。
"""

# ── 画面切替（candidates_ui.render_view_switch が描画する
#    st.radio(key=VIEW) の値）───────────────────────────────────────────────
VIEW_DASHBOARD = "dashboard"
VIEW_SEARCH = "search"
VIEW_DISCOVER = "discover"
VIEW_LIST = "list"
VIEW_CASES = "cases"
VIEW_ADMIN = "admin"

# st.radio(key=VIEW) が描画済みの状態で、別の画面から画面切替を予約するための
# キー（直接 VIEW へ代入すると「instantiated後のウィジェットキー変更」として
# Streamlitが例外にするため、次の実行の冒頭で反映する）。
PENDING_VIEW = "cand_pending_view"

# ── 商品案件管理（第1段階「返信獲得」）──────────────────────────────────────────
# マイ候補リスト画面（candidates_ui.render_list_screen）の「商品案件を作成」
# ボタンから、遷移先の案件一覧/詳細画面（cases_ui）へ渡す、選択中の案件idと
# 表示するメッセージ。
SELECTED_CASE_ID = "case_selected_id"
CASES_FLASH_MESSAGE = "case_last_message"
