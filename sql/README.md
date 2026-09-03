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

最後に `verify.sql` を実行し、**「ok」がすべて `true`** であることを確認する。

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

- 4テーブルの RLS が有効
- ポリシー数（products 1 / saved_items 3 / profiles 2 / app_admins 0）と INSERT ポリシー0件
- `anon` に4テーブルの権限が無い
- `authenticated` に INSERT 権限が無い
- `products` のテーブル全体 SELECT が無く、`created_by` / `created_at` を読めない
- `saved_items` の UPDATE が `memo` / `status` / `priority_override` / `archived` の4列だけ
- `profiles` の UPDATE が `display_name` だけ
- 4関数すべてに `search_path` が固定され、`anon` は EXECUTE できない
- `authenticated` が EXECUTE できるのは `is_admin` と `save_candidate` の2つだけ
- トリガー4本・インデックス2本・一意制約2件
- `profiles` の件数が `auth.users` と一致

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

**権限だけやり直す**ときは `0003_rls_grants.sql` を再実行すればよい
（`drop policy if exists` があるので重複しない）。

フェーズ3Aの時点ではアプリからこれらのテーブルを使っていないため、
ロールバックしても**検索・AI分析・認証には影響しない**。

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

## テスト

SQL は本番へ適用する前に、静的テストで機械的に点検している。

```bash
python tests/test_sql_migrations.py
```

RLS が実際に効いているかは本物の Supabase でしか確認できない。
受講生A・受講生B・管理者の3アカウントによる実データ分離テスト（12項目）を
フェーズ3Eで別途実施する。
