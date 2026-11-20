# 回帰テスト

改修のたびに再実行して、既存の検索機能とCSV出力が壊れていないことを確認するためのテストです。

## 実行方法

```bash
python tests/test_offline.py       # ネットワーク不要。まずこれを通す
python tests/test_search_state.py  # 検索結果の保持（session_state）
python tests/test_result_schema.py # 検索結果の列構成の正規化・旧セッションの移行
python tests/test_auth.py          # メール＋パスワード認証（偽クライアント）
python tests/test_app_render.py    # アプリ画面の描画（Streamlit の AppTest）
python tests/test_sdk_compat.py    # 実 Anthropic SDK との引数互換（通信なし）
python tests/test_product_key.py   # URL正規化・対象ドメイン検証（候補リスト用）
python tests/test_sql_migrations.py # SQLマイグレーションの静的テスト（DB接続なし）
python tests/test_candidates.py    # 候補保存（save_candidate RPC・偽クライアント）
python tests/test_summit.py        # サミット特典モード（制限版画面・偽関数）
python tests/test_sender_profiles.py # 送信者プロフィール（偽クライアント）
python tests/test_cases_sql_migrations.py # 案件管理（0007〜0009）のSQL静的テスト
python tests/test_cases.py         # 案件管理（第1段階「返信獲得」）のデータ層（偽クライアント）
python tests/test_approach_messages.py # 初回アプローチ文面のチャネル別テンプレート生成（決定論的・AI不使用）
python tests/test_import_order.py  # 画面モジュール間のimport順序に依存しないこと（起動不能の再発防止）
python tests/test_network.py       # 外部サイトへ実アクセスする
```

追加パッケージは不要です（pytest があれば `pytest tests/` でも動きます）。
いずれも失敗があると終了コードが 0 以外になります。

## テストを分けている理由

| ファイル | 依存 | 失敗したときの意味 |
|---|---|---|
| `test_offline.py` | なし（決定論的） | **コードの不具合**。必ず直す |
| `test_search_state.py` | なし（決定論的） | **コードの不具合**。必ず直す |
| `test_result_schema.py` | なし（決定論的） | **コードの不具合**。必ず直す |
| `test_auth.py` | なし（決定論的） | **コードの不具合**。必ず直す |
| `test_app_render.py` | なし（決定論的） | **コードの不具合**。必ず直す |
| `test_sdk_compat.py` | インストール済みの Anthropic / Supabase SDK | **SDKの破壊的変更**。渡している引数を直す |
| `test_product_key.py` | なし（決定論的） | **コードの不具合**。必ず直す |
| `test_sql_migrations.py` | なし（決定論的・DB接続なし） | **SQLの不具合**。RLS自体が効くかはフェーズ3Eの実データテストで別途確認する |
| `test_candidates.py` | なし（決定論的） | **コードの不具合**。必ず直す |
| `test_summit.py` | なし（決定論的） | **コードの不具合**。必ず直す |
| `test_sender_profiles.py` | なし（決定論的） | **コードの不具合**。必ず直す |
| `test_cases_sql_migrations.py` | なし（決定論的・DB接続なし） | **SQLの不具合**。RLS・状態機械が実際に効くかはフェーズ3Eの実データテストで別途確認する |
| `test_cases.py` | なし（決定論的） | **コードの不具合**。必ず直す |
| `test_approach_messages.py` | なし（決定論的・AI API不使用） | **コードの不具合**。必ず直す |
| `test_import_order.py` | なし（サブプロセスを起動するが外部通信はしない） | **起動不能になる不具合**。画面モジュール間で互いをimportする構造に戻していないか確認する |
| `test_network.py` | 外部サイトの状態 | 掲載終了・仕様変更・IPブロックの可能性がある。コードの不具合とは限らないため、まず原因を切り分ける |

`test_app_render.py` は Streamlit 同梱の `st.testing.v1.AppTest` を使う。
ブラウザも追加パッケージも不要で、検索を実行しないため外部AI APIも呼ばない。

## テストしないこと

- **Supabase へも通信しません。** 認証は偽クライアントで検証しています
  （`test_sdk_compat.py` はシグネチャを `inspect` で調べるだけで、実際のログインは行いません）
- **外部AI API（Anthropic）は呼びません。** プロンプトの組み立ては偽クライアントで捕捉して検証しています
  （`test_sdk_compat.py` は実SDKのシグネチャを `inspect` で調べるだけで、`create()` を呼びません）
- APIキー・秘密情報は一切含みません
- 本番の分析処理（1件30秒以上かかる処理）は実行しません
- 外部サイトへの書き込みは行いません（読み取りのみ）

## 主なカバー範囲

- CSVの列構成（24列）とExcel互換の書き出し
- 判定の確度（データ取得済み／参考値）の出し分け
- **メーカー名の採用可否**（`rest` のような一般語をメーカー名にしない）
- 「不明」が英文営業メールに混入しないこと
- AI分析プロンプトの欠損表記
- **実SDKとの引数互換**（`messages.create` に渡す引数が実際に受け付けられるか）
- サマリー表HTMLの並び順・全文表示・HTMLエスケープ
- Kickstarterの取得経路（本体／Kicktraq／stats.json）
- **検索結果の保持**（再実行・失敗・クリア時のふるまい、セッション間の独立）
- **列構成の正規化**（旧形式の結果を既定値で補い、表示時の KeyError を防ぐ）
- **旧 session_state の移行**（列が増えたデプロイ後も開いたままのセッションが落ちない）
- **認証**（未ログインで検索・AI処理へ到達しない、初回パスワード変更、ログアウトで全消去、
  失敗理由を区別しない文言、トークンを画面へ出さない、クライアントを共有・キャッシュしない）
- **候補リストのURL正規化**（Kickstarter/Indiegogo/ZECZEC の3ドメインのみ許可。
  ホスト名を解析して完全一致で判定し、類似ドメイン・サブドメイン偽装を拒否する）
- **SQLマイグレーションの静的検査**（ファイルの破損なし、権限遮断が関数作成の直後にあること、
  秘密情報や実UUIDが含まれないこと、アプリ側とDB側のドメイン許可リストが一致すること）
- **候補保存**（`save_candidate` RPCのみを使用、user_idを渡さない、products/saved_itemsへ
  直接書き込まない、already_saved判定、複数件の成功・保存済み・失敗の集計、NaN/Infinity/
  日付型を含む値の安全な正規化、失敗時に例外内容を出さない、ログアウトでcand_状態が消える）
- **マイ候補リスト**（サイドバーの画面切替、本人の候補だけを表示、select("*")不使用、
  活動メモ・ステータス・優先度の編集がDBのCHECK制約と一致、user_id/product_idを更新しない、
  アーカイブ・解除、削除前の確認必須、saved_itemsのみ削除しproductsは消さない、
  更新・削除対象をidとuser_idの両方で明示、保存日時を日本時間で表示）
- **管理者ビュー**（is_admin() RPCの結果だけで判定しuser_metadata/メールでは判定しない、
  app_adminsへ直接触れない、一般利用者には画面・選択肢自体を出さない、閲覧専用で
  update/delete/archiveの呼び出しが無い、活動メモ・ステータス・優先度が編集不能、
  saved_items/products/profilesをselect("*")なしで取得しprofilesは別クエリでuser_id結合、
  管理者自身のuser_idでは絞り込まない、利用者・ステータス・優先度・アーカイブ状態で
  絞り込み、保存日時の新しい順、1ページ50件のページング、画面切替の下見と実際の描画
  直前の再確認が食い違っても内容を表示しない、失敗時は固定文言のみ）
- **サミット特典モード**（app_metadata.roleがsummitの利用者だけに制限版画面を表示、
  本人が書き換え可能なuser_metadata.roleは一切参照しない、roleが未設定・不正値は
  student扱い、roleの"admin"はis_admin() RPCとは無関係で権限を一切持たない、
  営業メール生成・保存機能・履歴・高度分析（優先度/判定の確度/調達額等）・
  生徒専用機能（画面切替・マイ候補リスト・管理者ビュー）を一切表示しない、
  list/文字列/空値いずれでも配列の生表示にならない、不正URL・取得失敗時に
  安全な文言のみ表示、student/adminの既存画面は無変更）
- **送信者プロフィール**（氏名（英語表記）は必須・会社名・屋号（英語表記）は任意で
  Supabaseへ利用者ごとに保存、user_idごとのデータ分離・RLS・他人の行は
  取得/更新/削除できない、前後空白除去・空文字拒否・会社名が空欄や前後空白のみ
  でも拒否せず空文字として保存、日本語入力は実際に日本語が含まれる項目
  （氏名／会社名・屋号）だけを名指しした警告のみで拒否しない、氏名+会社名の
  重複登録を一意制約で防止、同一利用者がis_default=trueを複数持てないことを
  DB側の部分一意索引でも保証し競合時は重複登録とは別の文言を返す、1件だけ/既定/
  最後に使用したプロフィールの自動選択（複数かつ無根拠なら推測で選ばない）、
  削除前の確認必須、会社名が空のときプルダウン表示・営業メール本文のどちらにも
  「【会社名・屋号】」等のプレースホルダや余分な空行・区切り記号（｜）が残らない
  ことをbuild_approach_email側で実証、会社名がある場合は氏名・会社名の両方が
  従来どおり反映される、選択プロフィールが既存のanalyze_with_claude呼び出しへ
  そのまま渡る、ログアウトでsprof_状態が消える、サミット特典版には追加しない、
  画面切替の下見と同じ理由でauth.apply_session()を使わない非破壊な
  セッション設定、0006_sender_profiles.sqlの全CREATE文がガード付きで複数回
  実行しても壊れないことの静的検査）

- **案件管理・第1段階「返信獲得」**（`cases`/`case_events`/`case_targets`のSQL静的検査：
  RLS・列単位GRANT・`anon`に権限が無いこと・公開6RPCが`user_id`を引数に取らないこと・
  本人確認の条件句・重複フォロー防止とイベント種別ごとの遷移条件の構造的な証拠・
  取消し時に`_case_recompute()`で再計算し例外を握りつぶさないこと・伏せ字化で
  `ai_assist`も消えること・二重クリックでの案件重複防止。`cases.py`のオフライン
  テスト：JST日付計算（UTC→JSTの月境界をまたぐケースを含む）、連絡先候補からの
  優先チャネル決定（既存`contact_info.resolve_priority()`の再利用）、RPCの
  戻り値・例外（`invalid_transition`を含む）の安全な文言への変換、月間目標の
  月別upsert・負数/上限のクランプ、ダッシュボード集計（送信数は案件数で数える、
  返信はコホート方式で初回送信月の実績に反映、商談移行数は`advanced_at`で独立に
  数える）、今日やることの優先順位（`awaiting_our_reply`→期限超過フォロー→
  本日期限フォロー→未送信→手動リマインダーの順、同順位は期限→作成日時の古い順、
  最大5件、アーカイブ済み・終了状態の除外）。AI支援結果はまだAI APIを呼び出さず、
  固定スキーマの検証と保存RPCの制約だけを検証する）。
  画面（`cases_ui.py`・`candidates_ui.py`、`test_app_render.py`内）：
  通常版の既定画面がダッシュボード（商品リサーチではない）になったこと、
  サミット特典版には一切表示されないこと、今日やることからの案件詳細への遷移、
  マイ候補リストの「商品案件を作成」ボタン（二重クリック・失敗時の安全な文言・
  作成後に案件詳細へ自動遷移）、案件一覧・案件詳細の基本表示、状態ごとに
  出る操作（初回送信／フォローアップ／返信受信／こちらの返信／状態変更）が
  `log_case_event`RPCを正しい引数で呼ぶこと、取消し済みイベントが履歴に出ない
  こと、月間目標の保存、ログアウトでcase_状態が消えること

- **初回アプローチ文面（`approach_messages.py`）**：AI APIを一切使わず決定論的な
  テンプレートだけで文面（件名・本文）を組み立てること、連絡チャネル（メール／
  公式サイト／問い合わせフォーム／掲載ページ／連絡先未確定は正式な長文＋件名、
  Instagram／Facebookは件名なしの短文DM、LinkedInは件名なしだが宛名ありの
  短文）で書式が分かれること、メーカー名が「不明」や空のときに英文の宛名
  （Dear ... Team,）へ混入させないこと、送信者名が無い場合は[Your Name]の
  プレースホルダで文面が壊れないこと、会社名の有無で自己紹介・署名に空行や
  余分な文言が残らないこと、未知のチャネル値でも例外を出さず汎用文面に
  フォールバックすること、商品名が空でも汎用的な言い回しで埋めること。
  画面（`cases_ui.py`）：未送信（`not_contacted`）の案件詳細に文面案を
  チャネル別に表示し利用者が編集・コピーできること、「初回メッセージを
  送信した」ボタンは送信事実を**記録するだけ**（この画面からは送信しない）
  だと明示する注記があること、既存の送信者プロフィール（`sender_profiles.py`）
  を読んで文面に反映すること（登録・編集・削除はしない）、サミット特典版には
  表示されないこと（案件管理そのものが非表示のため）、既存の状態遷移・RPC・
  RLS・SQLは無変更であること。

- **画面モジュール間のimport順序**（`ui_state_keys.py`・`test_import_order.py`）：
  `candidates_ui.py`（画面切替）と`cases_ui.py`（案件管理）は、共有する
  session_stateキーを依存の無い`ui_state_keys.py`から読み、互いを一切
  importしない（静的検査で確認）。`cases_ui`→`candidates_ui`・
  `candidates_ui`→`cases_ui`のどちらの順でimportしても、またアプリ本体
  （`streamlit_app.py`）をクリーンなPythonプロセスでimportしても
  AttributeErrorが出ないことを、実際に新しいサブプロセスを起動して確認する
  （同一プロセス内の2回目以降のimportはsys.modulesのキャッシュで
  常に成功して見えるため、再発の検出には使えない）。

## 補足

リポジトリ直下の `test_cs.py` は Kickstarter のページ構造を調べるための探索用スクリプトで、
`.gitignore` により追跡されていません。役割は `tests/test_network.py` の
`test_kickstarter_direct_fetch` が引き継いでいます。
