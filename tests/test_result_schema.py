# -*- coding: utf-8 -*-
"""検索結果のスキーマ正規化と、古い session_state からの移行テスト

実行: python tests/test_result_schema.py

表示直前の df[[...]] で KeyError が出た不具合の再発防止。
ネットワークも外部AI APIも使わない。
"""

import sys

from _harness import run  # noqa: E402
import research_crowdfunding as r  # noqa: E402
import result_schema as rschema  # noqa: E402
import search_state as sstate  # noqa: E402
from summary_table import build_summary_html  # noqa: E402


def _current_row(name="現行商品", priority="A"):
    """1. 現行形式の正常な結果（build_row をそのまま使う）"""
    project = {"name": name, "maker": "Mono+Mono", "url": "https://example.com/p",
               "platform": "Kickstarter", "raised_jpy": 1000000, "raised_usd": 6666,
               "backers": 100, "genre": "ガジェット", "description": "説明"}
    analysis = {"japanese_market_reason": "日本でも需要がある", "priority": priority,
                "priority_reason": "理由", "concerns": "特になし",
                "appeal_points": "訴求", "japanese_competitors": "競合",
                "approach_subject": "S", "approach_body": "B"}
    contact = {"official_url": "https://example.com", "email": "a@example.com",
               "contact_form": "未確認", "facebook": "未確認",
               "instagram": "未確認", "linkedin": "未確認"}
    return r.build_row(project, analysis, contact)


def _drop(row, *columns):
    out = dict(row)
    for column in columns:
        out.pop(column, None)
    return out


def _fallback_row():
    """4. AI分析失敗時のフォールバック結果（analysis が既定値だけ）"""
    project = {"name": "分析失敗商品", "maker": "", "url": "https://example.com/x",
               "platform": "Kickstarter", "raised_jpy": 0, "raised_usd": 0,
               "backers": 0, "genre": "", "description": "", "_from_slug": True}
    analysis = {"japanese_market_reason": "（Claude未接続のため省略）",
                "appeal_points": "（Claude未接続のため省略）",
                "japanese_competitors": "未確認", "priority": "B",
                "priority_reason": "（Claude未接続のため省略）", "concerns": "未確認"}
    return r.build_row(project, analysis, {})


# ── 1. 現行形式 ───────────────────────────────────────────────────────────────

def test_current_rows_are_unchanged():
    """現行形式の正常な結果は一切書き換えられない"""
    row = _current_row()
    fixed, missing = rschema.normalize_row(row)
    assert missing == [], str(missing)
    assert fixed == row, "正常な行が書き換えられている"


def test_existing_values_are_never_overwritten():
    """存在する値を既定値で上書きしない（空文字も尊重する）"""
    row = dict(_current_row())
    row["メーカー名"] = "NIID"
    row["公式サイトURL"] = ""            # 空文字は「値がある」扱い
    row["調達額(円)"] = 123456
    fixed, _ = rschema.normalize_row(row)
    assert fixed["メーカー名"] == "NIID"
    assert fixed["公式サイトURL"] == ""
    assert fixed["調達額(円)"] == 123456


# ── 2〜3. 旧形式 ──────────────────────────────────────────────────────────────

def test_old_row_without_confidence_column():
    """「判定の確度」が無い旧形式の結果を補える"""
    row = _drop(_current_row(), "判定の確度")
    fixed, missing = rschema.normalize_row(row)
    assert missing == ["判定の確度"], str(missing)
    assert fixed["判定の確度"] == "データ取得済み", fixed["判定の確度"]


def test_old_row_confidence_is_low_when_amount_missing():
    """調達額が取れていない旧形式の行は安全側（参考値）に倒す"""
    row = _drop(_current_row(), "判定の確度")
    row["調達額(USD)"] = 0
    fixed, _ = rschema.normalize_row(row)
    assert fixed["判定の確度"] == "参考値（データ不足）", fixed["判定の確度"]


def test_old_row_missing_many_columns():
    """複数列が不足した旧形式の結果でも現在のスキーマになる"""
    row = _drop(_current_row(), "判定の確度", "注意点・懸念点", "LinkedIn",
                "支援者数", "日本販売時の訴求ポイント", "競合する日本商品")
    fixed, missing = rschema.normalize_row(row)
    assert set(fixed) >= set(r.CSV_FIELDS), str(set(r.CSV_FIELDS) - set(fixed))
    assert set(missing) == {"判定の確度", "注意点・懸念点", "LinkedIn",
                            "支援者数", "日本販売時の訴求ポイント", "競合する日本商品"}


def test_defaults_match_existing_conventions():
    """既定値が既存の欠損表記ルールと整合する"""
    fixed, _ = rschema.normalize_row({"商品名": "最小限"})
    assert fixed["メーカー名"] == "不明"
    assert fixed["優先度"] == "B"
    assert fixed["メールアドレス"] == "未確認"
    assert fixed["問い合わせフォームURL"] == "未確認"
    assert fixed["注意点・懸念点"] == "未確認"
    assert fixed["競合する日本商品"] == "未確認"
    assert fixed["日本で売れそうな理由"] == "（Claude未接続のため省略）"
    assert fixed["優先度の理由"] == "（Claude未接続のため省略）"
    assert fixed["判定の確度"] == "参考値（データ不足）"


def test_defaults_do_not_break_types():
    """URL・調達額などの型を壊さない"""
    fixed, _ = rschema.normalize_row({"商品名": "最小限"})
    for column in ("調達額(円)", "調達額(USD)", "支援者数"):
        assert isinstance(fixed[column], int), f"{column} が {type(fixed[column])}"
    for column in ("掲載URL", "公式サイトURL"):
        assert isinstance(fixed[column], str), f"{column} が {type(fixed[column])}"


def test_unknown_extra_column_is_kept():
    """現在のスキーマに無い列を捨てない（将来の列削除で情報を失わない）"""
    row = dict(_current_row())
    row["将来削除された列"] = "値"
    fixed, _ = rschema.normalize_row(row)
    assert fixed["将来削除された列"] == "値"


# ── 4〜5. フォールバック・一部欠損 ────────────────────────────────────────────

def test_ai_failure_fallback_row_is_valid():
    """AI分析失敗時のフォールバック結果はそのまま表示できる"""
    fixed, missing = rschema.normalize_row(_fallback_row())
    assert missing == [], str(missing)
    assert fixed["判定の確度"] == "参考値（データ不足）"
    assert fixed["優先度"] == "B"


def test_partially_missing_row():
    """一部項目だけ欠けた結果を補える"""
    row = _drop(_current_row(), "メーカー名", "掲載URL")
    fixed, missing = rschema.normalize_row(row)
    assert fixed["メーカー名"] == "不明"
    assert fixed["掲載URL"] == ""
    assert set(missing) == {"メーカー名", "掲載URL"}


def test_none_and_nan_are_treated_as_missing():
    """None・NaN も欠損として扱う（NaN が画面へ出ない）"""
    row = dict(_current_row())
    row["判定の確度"] = None
    row["メールアドレス"] = float("nan")
    fixed, missing = rschema.normalize_row(row)
    assert fixed["判定の確度"] == "データ取得済み"
    assert fixed["メールアドレス"] == "未確認"
    assert set(missing) == {"判定の確度", "メールアドレス"}


# ── 6〜7. 空・新旧混在 ────────────────────────────────────────────────────────

def test_empty_results():
    """空の結果でも落ちない"""
    rows, missing = rschema.normalize_rows([])
    assert rows == [] and missing == []


def test_mixed_old_and_new_rows():
    """新旧形式が混在した複数行をまとめて正規化できる"""
    rows, missing = rschema.normalize_rows([
        _current_row("新形式", "A"),
        _drop(_current_row("旧形式", "B"), "判定の確度"),
        _drop(_current_row("もっと古い", "C"), "判定の確度", "LinkedIn"),
    ])
    assert len(rows) == 3
    for row in rows:
        assert set(row) >= set(r.CSV_FIELDS)
    assert set(missing) == {"LinkedIn", "判定の確度"}, str(missing)


def test_missing_columns_are_reported_in_csv_order():
    """欠けた列は CSV の列順で報告する（原因を隠さない）"""
    rows, missing = rschema.normalize_rows([
        _drop(_current_row(), "判定の確度", "商品名")])
    assert missing == ["商品名", "判定の確度"], str(missing)


def test_display_columns_are_part_of_csv_fields():
    """表示に必要な列がCSVの列定義から外れていない"""
    for column in rschema.DISPLAY_COLUMNS:
        assert column in r.CSV_FIELDS, f"{column} が CSV_FIELDS に無い"


def test_non_dict_row_is_rejected():
    """辞書でない行は移行不能として明示的に失敗させる（黙って通さない）"""
    try:
        rschema.normalize_row(["これは行ではない"])
    except TypeError:
        return
    raise AssertionError("辞書でない行が通ってしまった")


def test_display_columns_survive_stale_csv_fields():
    """CSV_FIELDS が古いまま読み込まれていても表示列は補われる

    Streamlit は再実行時に import 済みモジュールを読み直さないことがあり、
    デプロイ直後に古い CSV_FIELDS を掴んだままになりうる。
    その状態でも表示に必要な列が欠けないことを確認する。
    """
    original = rschema.CSV_FIELDS
    rschema.CSV_FIELDS = [c for c in original if c != "判定の確度"]
    try:
        assert "判定の確度" in rschema.required_columns()
        fixed, missing = rschema.normalize_row({"商品名": "X"})
        assert "判定の確度" in fixed
        assert "判定の確度" in missing
    finally:
        rschema.CSV_FIELDS = original


# ── 8. session_state の移行 ───────────────────────────────────────────────────

def _state_with(rows, version=None) -> dict:
    state: dict = {}
    sstate.init_state(state)
    sstate.save_success(state, rows, ["https://example.com/p"],
                        schema_version=version)
    return state


def test_migration_upgrades_old_session():
    """古い session_state の結果を新形式へ移行する"""
    state = _state_with([_drop(_current_row(), "判定の確度")], version=1)
    notice = sstate.migrate_state(state, rschema.normalize_rows,
                                  rschema.SCHEMA_VERSION)
    assert "判定の確度" in notice, notice
    assert sstate.get_results(state)[0]["判定の確度"] == "データ取得済み"
    assert state[sstate.SCHEMA] == rschema.SCHEMA_VERSION


def test_migration_runs_only_once():
    """移行後に同じ処理を繰り返さない（同じエラーを再発させない）"""
    state = _state_with([_drop(_current_row(), "判定の確度")], version=1)
    sstate.migrate_state(state, rschema.normalize_rows, rschema.SCHEMA_VERSION)
    again = sstate.migrate_state(state, rschema.normalize_rows,
                                 rschema.SCHEMA_VERSION)
    assert again == "", again


def test_migration_is_noop_for_current_version():
    """現行バージョンの結果には触らない"""
    row = _current_row()
    state = _state_with([row], version=rschema.SCHEMA_VERSION)
    assert sstate.migrate_state(state, rschema.normalize_rows,
                                rschema.SCHEMA_VERSION) == ""
    assert sstate.get_results(state) == [row]


def test_migration_clears_results_when_impossible():
    """移行できない結果は説明を出して検索結果だけ初期化する"""
    state = _state_with([_current_row()], version=1)
    state[sstate.RESULTS] = ["行ではない文字列"]
    state["urls_text_widget"] = "https://example.com/keep"
    notice = sstate.migrate_state(state, rschema.normalize_rows,
                                  rschema.SCHEMA_VERSION)
    assert "初期化" in notice, notice
    assert sstate.has_results(state) is False
    assert state["urls_text_widget"] == "https://example.com/keep", "入力欄まで消えた"
    assert state[sstate.SCHEMA] == rschema.SCHEMA_VERSION
    assert sstate.migrate_state(state, rschema.normalize_rows,
                                rschema.SCHEMA_VERSION) == "", "毎回繰り返している"


def test_migration_without_results_is_silent():
    """結果が無いセッションには何も表示しない"""
    state: dict = {}
    sstate.init_state(state)
    assert sstate.migrate_state(state, rschema.normalize_rows,
                                rschema.SCHEMA_VERSION) == ""
    assert state[sstate.SCHEMA] == rschema.SCHEMA_VERSION


def test_new_search_records_schema_version():
    """新しい検索結果には現在のスキーマ版が記録される"""
    state: dict = {}
    sstate.init_state(state)
    sstate.save_success(state, [_current_row()], ["u"],
                        schema_version=rschema.SCHEMA_VERSION)
    assert state[sstate.SCHEMA] == rschema.SCHEMA_VERSION
    assert sstate.get_notice(state) == ""


# ── 9〜10. 正規化後の描画とCSV ────────────────────────────────────────────────

def test_summary_table_renders_after_normalization():
    """正規化した旧形式の結果でサマリー表を描画できる"""
    import pandas as pd
    rows, _ = rschema.normalize_rows([
        _drop(_current_row("旧形式商品", "A"), "判定の確度", "メーカー名")])
    df = pd.DataFrame(rows, columns=r.CSV_FIELDS)
    summary_df = df.reindex(columns=rschema.DISPLAY_COLUMNS)   # 表示側と同じ操作
    assert list(summary_df.columns) == rschema.DISPLAY_COLUMNS
    html = build_summary_html(summary_df.to_dict("records"))
    assert "旧形式商品" in html and "不明" in html


def test_display_selection_never_raises_key_error():
    """表示側の列選択が KeyError を投げない（今回の不具合の直接の再現）"""
    import pandas as pd
    df = pd.DataFrame([{"商品名": "列が足りない行"}])
    summary_df = df.reindex(columns=rschema.DISPLAY_COLUMNS)   # 例外にならない
    assert list(summary_df.columns) == rschema.DISPLAY_COLUMNS


def test_csv_has_all_columns_after_normalization():
    """正規化した結果はCSV出力でも必要列が揃う"""
    import csv
    import io
    import pandas as pd
    rows, _ = rschema.normalize_rows([_drop(_current_row(), "判定の確度", "LinkedIn")])
    text = pd.DataFrame(rows, columns=r.CSV_FIELDS).to_csv(
        index=False, encoding="utf-8-sig")
    header = next(csv.reader(io.StringIO(text)))
    assert header == r.CSV_FIELDS, str(header)
    record = next(csv.DictReader(io.StringIO(text)))
    assert record["判定の確度"] == "データ取得済み"
    assert record["LinkedIn"] == "未確認"


if __name__ == "__main__":
    sys.exit(run(dict(globals()), "検索結果スキーマの正規化・移行テスト"))
