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

## 補足

リポジトリ直下の `test_cs.py` は Kickstarter のページ構造を調べるための探索用スクリプトで、
`.gitignore` により追跡されていません。役割は `tests/test_network.py` の
`test_kickstarter_direct_fetch` が引き継いでいます。
