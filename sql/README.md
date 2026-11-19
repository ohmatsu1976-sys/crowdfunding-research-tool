# データベース定義（フェーズ3A）

マイ候補リストのデータベース基盤。**受講生ごとのデータ分離を、画面ではなく
PostgreSQL の行レベルセキュリティ（RLS）と権限で担保する。**

SQL Editor に貼るだけにせず、ここにマイグレーションとして残している。
環境を作り直すときは同じ順序で流せば同じ状態を再現できる。

## 適用順序

Supabase 管理画面 → SQL Editor で、**この順に、続けて**実行する。

| # | ファイル | 内容 |
|---|---|---|
| 1 | `migrations/0001_tables.sql` | 4テーブル・制約・インデックス |
| 2 | `migrations/0002_functions.sql` | `is_admin()` / `save_candidate()` / profiles自動作成 / `updated_at`自動更新 |
| 3 | `migrations/0003_rls_grants.sql` | RLS有効化・ポリシー・GRANT / REVOKE |
| 4 | `migrations/0004_backfill_profiles.sql` | 既存利用者の profiles 初期登録 |
| 5 | `migrations/0005_admin.sql.example` | 管理者の登録（**雛形**。user_id を差し替えて実行） |
| 6 | `migrations/0006_sender_profiles.sql` | `sender_profiles` テーブル・RLS・GRANT（送信者プロフィール） |
| 7 | `migrations/0007_cases_tables.sql` | `cases`/`case_events`/`case_targets` の3テーブル・制約・索引（案件管理） |
| 8 | `migrations/0008_cases_functions.sql` | 案件管理の公開6RPC・内部2関数・`updated_at`自動更新トリガー |
| 9 | `migrations/0009_cases_rls_grants.sql` | 案件管理のRLS有効化・ポリシー・列単位GRANT |

最後に `verify.sql` を実行する。Supabase SQL Editor は複数の SELECT のうち
最後の結果しか表示しないため、`verify.sql` は全検査を UNION ALL で
**1つの結果表**にまとめてある（`check_name` / `expected` / `actual` / `ok` /
`detail` の5列）。上から下まで `ok` 列が **すべて `true`** であることを
確認する。表の最後には「すべてのチェックが成功」という集約行があり、
そこだけ見ても一括で判定できる。

> **0002単独の実行が完了した時点で、外部から関数を呼べない状態になる。**
> 0002 は各関数を作成した直後、同じトランザクション内で PUBLIC / anon /
> authenticated から EXECUTE を REVOKE している。0003 はそこから
> `is_admin()` と `save_candidate()` の2つだけを authenticated へ改めて
> GRANT するだけで、権限の遮断そのものは0002の中で完結している。

すべて `begin; … commit;` で囲み、`if not exists` / `create or replace` /
`drop … if exists` を使っているので、**何度実行しても壊れない**。

## 可視範囲

| テーブル | 受講生 | 管理者 | 未ログイン |
|---|---|---|---|
| `products` | **自分が保存した商品のみ** | 全件（閲覧） | なし |
| `saved_items` | 自分の行のみ（閲覧・更新・削除） | 全件（**閲覧のみ**） | なし |
| `profiles` | 自分の行のみ（表示名だけ更新可） | 全件（**閲覧のみ**） | なし |
| `app_admins` | **到達できない** | 到達できない | なし |
| `sender_profiles` | 自分の行のみ（閲覧・登録・更新・削除） | 自分の行のみ（管理者の特別扱いなし） | なし |
| `cases` | 自分の行のみ（閲覧・一部列の更新・削除。**登録は専用RPC経由のみ**） | 自分の行のみ（管理者の特別扱いなし） | なし |
| `case_events` | 自分の行のみ（**閲覧のみ**。書き込みはすべて専用RPC経由） | 自分の行のみ（管理者の特別扱いなし） | なし |
| `case_targets` | 自分の行のみ（閲覧・登録・更新・削除） | 自分の行のみ（管理者の特別扱いなし） | なし |

`app_admins` はポリシーを1つも作らず、権限も与えていない。
読めるのは `SECURITY DEFINER` 関数 `public.is_admin()` だけ。

## 書き込みの経路

候補の保存は **`public.save_candidate()` を通す以外に方法がない。**
`products` と `saved_items` への INSERT 権限は誰にも与えておらず、
INSERT ポリシーも1つも無い。

保存者は関数内の `auth.uid()` でのみ決まる。**引数に `user_id` を取らない。**

`url_key` は `=` の完全一致だけで使う。`LIKE` / `ILIKE` / `text_search` には
使わないため、1回の呼び出しで触れるのは0件か1件で、走査による発見ができない。

対象ドメインは **Kickstarter・Indiegogo・ZECZEC の3つ**（`kickstarter.com` /
`indiegogo.com` / `zeczec.com`）。判定は文字列の部分一致ではなく、
`https://` の直後から最初の `/` までを取り出した「ホスト名」と許可リストを
**完全一致**で比較する。`zeczec.com.example.com`（後ろに何か続く偽装）も
`example-zeczec.com`（似た名前の偽装）も、抽出したホスト名がどちらの
文字列とも一致しないため拒否される。アプリ側の `product_key.py` も
`urllib.parse.urlsplit()` でホスト名を厳密に取り出す同じ考え方で判定し、
同じ許可リスト（`ALLOWED_HOSTS`）を持つ。ドメインを追加するときは
両方を同時に更新する（`tests/test_sql_migrations.py` が一致を検証する）。

## 確認すること（`verify.sql`）

56個の検査を1行ずつ行う（結果表は上から順にこの並び）。先頭25個は0001〜0005の、
次の8個（26〜33）は0006（`sender_profiles`）の、残り23個（34〜56）は
0007〜0009（案件管理・第1段階「返信獲得」）の検査。

1. 4テーブルが存在する
2. 4テーブルすべてで RLS が有効
3. `products` / `saved_items` / `profiles` / `app_admins` のポリシー数（1 / 3 / 2 / 0）
4. INSERT ポリシーが0件（4テーブル合計）
5. `anon` が4テーブルの SELECT/INSERT/UPDATE/DELETE を一切持たない
6. `authenticated` が `products` / `saved_items` へ直接 INSERT できない
7. `products` にテーブル全体 SELECT 権限が無い
8. `products` の `created_by` / `created_at` を `authenticated` が取得できない
9. `saved_items` の UPDATE 可能列が `memo` / `status` / `priority_override` / `archived` だけ
10. `saved_items` の `user_id` / `product_id` / `saved_at` / `updated_at` を直接更新できない
    （依頼文にある `created_at` は `saved_items` には無い列のため、監査用の `saved_at` で確認する）
11. `profiles` の UPDATE 可能列が `display_name` だけ
12. `app_admins` を `anon` / `authenticated` が直接操作できない
13. `authenticated` が EXECUTE できるのは `is_admin` と `save_candidate` の2つだけ
14. `handle_new_user` / `set_updated_at` を `authenticated` が直接実行できない
15. `anon` が4関数のいずれも実行できない
16. 3関数（`is_admin` / `save_candidate` / `handle_new_user`）が `SECURITY DEFINER`
17. 4関数すべてで `search_path` が固定されている
18. トリガー4本・インデックス2本・一意制約2件が存在する
19. `profiles` の件数が `auth.users` と一致する
20. `app_admins` に管理者が1名以上登録されている
21. `sender_profiles` テーブルが存在する
22. `sender_profiles` で RLS が有効
23. `sender_profiles` のポリシー数（4）
24. `anon` が `sender_profiles` の SELECT/INSERT/UPDATE/DELETE を一切持たない
25. `sender_profiles` で更新できるのは `sender_name_en`/`company_name_en`/`is_default`/`last_used_at` だけ
26. `sender_profiles` の INSERT ポリシーが `auth.uid()` との WITH CHECK を持つ
27. `sender_profiles` に `(user_id, sender_name_en, company_name_en)` の一意制約がある
28. `sender_profiles` の `is_default` が `user_id` ごとに部分一意索引で制限されている
    （1利用者につき `is_default=true` は最大1件。同時更新の競合もDB側で防ぐ）
29. `cases` / `case_events` / `case_targets` の3テーブルが存在し、3テーブルすべてで RLS が有効
30. `cases` / `case_events` / `case_targets` のポリシー数（3 / 1 / 4）。`cases` と `case_events` には
    INSERT ポリシーが無い（登録・送受信の記録は専用RPC経由のみ）
31. `anon` が3テーブルの SELECT/INSERT/UPDATE/DELETE を一切持たない
32. `authenticated` が `cases` へ直接INSERT、`case_events` へ直接INSERT/UPDATE/DELETEできない
33. `cases` の UPDATE 可能列が `memo`/`next_action_at`/`next_action_label`/`archived`/
    `primary_channel` の5つだけ（状態機械に関わる列はRPC経由のみ）
34. `case_targets` の UPDATE 可能列が `approach_target`/`reply_target` の2つだけ
35. `authenticated` が実行できるのは案件管理の公開6RPC
    （`create_case_from_candidate`/`log_case_event`/`void_case_event`/
    `correct_case_event`/`redact_case_event`/`save_ai_assist_result`）だけで、
    内部専用の2関数（`_case_apply_event`/`_case_recompute`）は実行できない
36. `anon` が案件管理の新規関数（公開6＋内部2）のいずれも実行できない
37. 公開6RPCすべてが `SECURITY DEFINER` かつ `search_path` が固定されている
38. `cases`/`case_targets` の `updated_at` 自動更新トリガーが2本存在する
39. `cases` に `(user_id, product_id)` の一意制約、`case_targets` に主キー制約が存在する
40. `case_events` に `voided_at`/`redacted_at`/`ai_assist` 列が存在する
    （訂正・伏せ字化・取消しの構造そのものの確認）
41. `case_targets` の `approach_target`/`reply_target` に範囲チェック制約（0〜100000）がある
42. 案件管理の公開6RPCがいずれも `user_id` を引数に取らない

いずれも `has_table_privilege()` / `has_column_privilege()` /
`has_function_privilege()` を使う。これらは指定したロール名の実際のACLを
直接調べる関数で、SQL Editor の接続ロールが `anon`/`authenticated` の
メンバーかどうかに左右されない（`information_schema` の
`role_table_grants` 等はメンバーシップで見え方が変わりうるため使わない）。

メールアドレス・user_id・トークンの実値は返さない。件数・真偽値・列名の
一覧だけを返す。

## ロールバック

**適用前に戻す**（テーブルごと削除。データも消える）。

```sql
begin;
drop trigger if exists on_auth_user_created       on auth.users;
drop trigger if exists products_set_updated_at    on public.products;
drop trigger if exists saved_items_set_updated_at on public.saved_items;
drop trigger if exists profiles_set_updated_at    on public.profiles;

drop function if exists public.save_candidate(text, text, jsonb);
drop function if exists public.handle_new_user();
drop function if exists public.set_updated_at();
drop function if exists public.is_admin();

drop table if exists public.saved_items;   -- products より先（外部キー）
drop table if exists public.products;
drop table if exists public.profiles;
drop table if exists public.app_admins;
commit;
```

**`sender_profiles`（0006）だけを戻す**ときは、他のテーブルとは独立しているため
これだけで良い：

```sql
begin;
drop trigger if exists sender_profiles_set_updated_at on public.sender_profiles;
drop table if exists public.sender_profiles;
commit;
```

**案件管理（0007〜0009）だけを戻す**ときは、他のテーブルとは独立しているため
これだけで良い：

```sql
begin;
drop trigger if exists cases_set_updated_at        on public.cases;
drop trigger if exists case_targets_set_updated_at on public.case_targets;

drop function if exists public.save_ai_assist_result(uuid, jsonb);
drop function if exists public.redact_case_event(uuid);
drop function if exists public.correct_case_event(uuid, text, text);
drop function if exists public.void_case_event(uuid, text);
drop function if exists public.log_case_event(uuid, text, text, text, timestamptz, text, text, text);
drop function if exists public.create_case_from_candidate(uuid);
drop function if exists public._case_recompute(uuid);
drop function if exists public._case_apply_event(text, text, text, timestamptz);

drop table if exists public.case_events;   -- cases より先（外部キー）
drop table if exists public.cases;
drop table if exists public.case_targets;
commit;
```

**権限だけやり直す**ときは `0003_rls_grants.sql`（0001〜0006分）または
`0009_cases_rls_grants.sql`（案件管理分）を再実行すればよい
（`drop policy if exists` があるので重複しない）。

フェーズ3Aの時点ではアプリからこれらのテーブルを使っていないため、
ロールバックしても**検索・AI分析・認証には影響しない**。同様に、案件管理
（0007〜0009）のロールバックも既存の検索・AI分析・認証・候補保存・送信者
プロフィールには一切影響しない（独立した新規テーブルのため）。

## 案件管理（第1段階「返信獲得」・0007〜0009）

マイ候補リストの1件から「商品案件」を作り、送信・フォロー・返信・条件確認/回答・
状態変更を1本のタイムライン（`case_events`）に記録する。既存の `products` /
`saved_items` / `profiles` / `app_admins` / `sender_profiles`、および0001〜0006
のマイグレーションには一切変更を加えない。アプリ側のデータ層は `cases.py`。

**7状態**（`cases.status`。状態と個々のメッセージ履歴は分離している）:

| 内部値 | 日本語表示 |
|---|---|
| `not_contacted` | 未送信 |
| `awaiting_first_reply` | メーカーからの初回返信待ち |
| `awaiting_our_reply` | こちらの回答待ち |
| `awaiting_next_reply` | メーカーからの再返信待ち |
| `advanced` | 商談へ進んだ（手動のみ） |
| `on_hold` | 保留 |
| `lost` | 見送り・終了 |

**フェーズ**（`cases.phase`。将来拡張用。第1回実装では `reply` だけを使用）:
`reply` → `deal` → `plan` → `launch`。

**イベント種別**（`case_events.kind`）: `initial` / `followup1` / `followup2` /
`reply` / `our_reply` / `condition_check` / `condition_answer` /
`status_change` / `note`。返信後も受信・送信を何度でも記録できる
（状態機械が拒否するのは、現在の状態と矛盾する種別だけ）。

**状態機械**は `public._case_apply_event()`（純粋なロジック）に実装し、
`public.log_case_event()` RPCが呼ぶ。初回送信からJST基準で3日後に
「1回目フォロー」、1回目フォロー送信から7日後に「2回目フォロー」の期限を
自動設定する。返信（`reply`/`condition_answer`）を記録すると期限は解除され
`awaiting_our_reply` になり、こちらの回答（`our_reply`/`condition_check`）を
記録すると `awaiting_next_reply` になる。`advanced` への変更は常に利用者の
手動操作（`status_change`イベント）だけで行う。

**履歴の訂正・伏せ字化・取消し**（いずれも物理削除はしない）:

| 操作 | RPC | 動作 |
|---|---|---|
| 訂正 | `correct_case_event()` | 元の行を取消し済みにし、訂正済みの新しい行を追加して紐づける |
| 伏せ字化 | `redact_case_event()` | `message_text`/`translated_text`/`ai_assist`を復元不能な固定文言に置き換える |
| 取消し | `void_case_event()` | 取消し後、有効なイベントだけを発生順に再生して状態を再計算する。後続の履歴と矛盾する取消しは例外になり、トランザクション全体が失敗する（部分的な更新は残らない） |

**AI返信アシスタントの結果保存**は `save_ai_assist_result()` RPCを用意した。
**第1回実装ではAI APIを一切呼び出さない**（保存先だけを先に作る）。対象は
本人の受信イベント（`reply`/`condition_answer`）だけで、`ai_assist`以外の
列は変更しない。固定スキーマは `cases.py` の
`AI_ASSIST_REQUIRED_FIELDS`/`validate_ai_assist_payload()` を参照。

**月間目標**（`case_targets`）は利用者ごと・月ごとに保存する
（主キー `(user_id, target_month)`）。`target_month` は常にJST基準の月初日に
正規化し、過去月の目標は上書きされない。

**案件の作成**は `create_case_from_candidate(p_saved_item_id)` RPCの1本のみ。
`cases`への直接INSERT権限は与えていない。商品情報・連絡先候補
（`contact_snapshot`。許可リストのキーだけをコピー）はDB側で`products`から
取得してスナップショットするため、後から`saved_items`が削除・変更されても
案件の表示は壊れない。同一利用者・同一商品の重複案件は一意制約
（`cases_user_product_unique`）と`on conflict do nothing`で二重クリックでも
作られない。

## 補足・既知の制限

- **対象ドメインは Kickstarter と Indiegogo のみ。**
  `save_candidate()` の正規表現で許可している。
  検索ツールは ZECZEC（`zeczec.com`）にも対応しているが、
  現在の指定ではその商品を保存できない。追加する場合は
  `0002_functions.sql` の正規表現に1つ足し、`product_key.py` の正規化と
  合わせる（フェーズ3Bで判断する）。
- **`force row level security` は使っていない。**
  有効にすると `SECURITY DEFINER` 関数（テーブル所有者として動く）が
  `app_admins` を読めなくなり、`is_admin()` が壊れる。
- **メールアドレス変更の同期は範囲外。**
  アプリにメール変更機能が無いため発生しない。
  `handle_new_user()` は INSERT 時のみ動く。
- **`products` の孤児行は残す。**
  どの `saved_items` からも参照されなくなっても削除しない。
  RLS により受講生からは取得できない状態を維持する。自動削除は実装しない。
- **`service_role` / secret key は使わない。**
  すべて SQL Editor（postgres ロール）と、アプリ側の
  `SUPABASE_PUBLISHABLE_KEY` ＋ 本人の JWT だけで完結する。
- **案件管理（0007〜0009）は、画面（ダッシュボード・案件一覧・マイ候補リストの
  作成ボタン・AI返信アシスタント）をまだ実装していない。**
  今回はDBマイグレーション・RLS・RPC・Pythonのデータ層（`cases.py`）・
  オフラインテストまで。`streamlit_app.py` / `candidates_ui.py` /
  `auth_ui.py` は無変更。
- **`advanced`（商談へ進んだ）から先の段階（フェーズ`deal`/`plan`/`launch`）の
  テーブル・画面はまだ無い。** `cases.phase`列は将来拡張用に用意しているが、
  第1回実装では`reply`だけを使う。
- **月間目標の自動バックフィルは行わない。** `case_targets`は利用者が設定した
  月だけに行ができる（設定していない月は0件として扱う。`get_month_target()`
  参照）。

## テスト

SQL は本番へ適用する前に、静的テストで機械的に点検している。

```bash
python tests/test_sql_migrations.py             # 0001〜0006 / verify.sql
python tests/test_cases_sql_migrations.py       # 0007〜0009（案件管理）/ verify.sqlの追記分
python tests/test_cases.py                      # cases.py のオフラインテスト
```

RLS が実際に効いているかは本物の Supabase でしか確認できない。
受講生A・受講生B・管理者の3アカウントによる実データ分離テスト（12項目）を
フェーズ3Eで別途実施する（案件管理のRLSも同じタイミングで実データ検証する）。
