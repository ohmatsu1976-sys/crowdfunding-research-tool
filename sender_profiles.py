# -*- coding: utf-8 -*-
"""送信者プロフィール（営業メール用の氏名・会社名の英語表記）

利用者ごとに複数保存し、画面のプルダウンから選んで営業メール生成へ渡す。
DBアクセスは public.sender_profiles テーブルへ直接行う（products/saved_items
と違い、insert-once・ドメイン検証のような複雑な制御が不要なため、
save_candidate() のような専用RPCは使わない。sql/migrations/0006_sender_profiles.sql
参照）。

Streamlit にも認証にも依存しない。クライアントを引数で受け取るだけなので、
偽クライアントで外部通信なしにテストできる（candidates.py と同じ方針）。
本人のJWTをクライアントへ設定する処理（auth.apply_session）は呼び出し側
（sender_profiles_ui.py）の責務とする。

【重要】このモジュールはプロフィール情報の保存・取得だけを行う。
認証・画面モード判定・管理者権限の判定ロジック（auth.py / candidates.py が
別途担う）には一切関わらない。
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

MAX_NAME_LEN = 200
MAX_COMPANY_LEN = 200

# 氏名・会社名として選ぶ列だけを明示する（全列取得の書き方は使わない）
_PROFILE_SELECT = (
    "id,user_id,sender_name_en,company_name_en,is_default,last_used_at,"
    "created_at,updated_at"
)

# ── 利用者へ見せる文言（例外の中身・Supabaseの内部情報は一切含めない）────────
LIST_FAILED = "送信者プロフィールを取得できませんでした。時間をおいて、もう一度お試しください。"
SAVE_FAILED = "送信者プロフィールを保存できませんでした。時間をおいて、もう一度お試しください。"
UPDATE_FAILED = "送信者プロフィールを更新できませんでした。時間をおいて、もう一度お試しください。"
DELETE_FAILED = "送信者プロフィールを削除できませんでした。時間をおいて、もう一度お試しください。"
DUPLICATE = "同じ氏名・会社名の送信者プロフィールが既に登録されています。"
DEFAULT_CONFLICT = "既定プロフィールの設定が競合しました。もう一度お試しください。"
EMPTY_NAME = "氏名（英語表記）を入力してください。"
NAME_TOO_LONG = f"氏名は{MAX_NAME_LEN}文字以内で入力してください。"
COMPANY_TOO_LONG = f"会社名・屋号は{MAX_COMPANY_LEN}文字以内で入力してください。"
CREATE_OK = "送信者プロフィールを登録しました。"
UPDATE_OK = "送信者プロフィールを更新しました。"
DELETE_OK = "送信者プロフィールを削除しました。"

# ひらがな・カタカナ・漢字（CJK統合漢字の主要範囲）
_JAPANESE_RE = re.compile(r"[぀-ヿ一-鿿]")


class Profile:
    """送信者プロフィール1件（sender_profiles の1行）"""

    __slots__ = ("id", "user_id", "sender_name_en", "company_name_en",
                "is_default", "last_used_at", "created_at", "updated_at")

    def __init__(self, **fields: Any):
        for key in self.__slots__:
            setattr(self, key, fields.get(key))

    @property
    def label(self) -> str:
        """プルダウン表示用（例: "Hirohito Ohmatsu｜Base on Base LLC"）"""
        if self.company_name_en:
            return f"{self.sender_name_en}｜{self.company_name_en}"
        return self.sender_name_en


# ── 入力の整形・検証 ───────────────────────────────────────────────────────────

def normalize_text(value: Any) -> str:
    """前後の空白を除去する（保存前に必ず通す）"""
    return str(value or "").strip()


def contains_japanese(text: Any) -> bool:
    """ひらがな・カタカナ・漢字が含まれるか（英語表記を促す警告の判定用）"""
    return bool(_JAPANESE_RE.search(str(text or "")))


def japanese_warning(name: Any, company: Any) -> str:
    """日本語が含まれる項目だけを対象にした警告文（無ければ空文字）

    氏名・会社名のどちらかにだけ日本語が含まれる場合、両方に触れる文言を
    出すと「入力していない項目まで指摘された」と誤解されるため、実際に
    日本語が含まれる項目名だけを文中に挙げる。拒否ではなく警告のみ
    （validate_fields では日本語を理由に拒否しない）。
    """
    fields = []
    if contains_japanese(name):
        fields.append("氏名")
    if contains_japanese(company):
        fields.append("会社名・屋号")
    if not fields:
        return ""
    return (f"{'・'.join(fields)}に日本語が含まれています。"
            "海外メーカーへ送るメールに使用するため、英語表記をおすすめします。")


def validate_fields(name: Any, company: Any) -> str:
    """問題があればその文言、無ければ空文字を返す（空文字拒否・文字数のみ検証）

    アポストロフィ・ハイフン・ピリオド・カンマ・アンパサンド・数字・アクセント
    記号などは英語圏の氏名・会社名で通常使われるため、文字種では拒否しない。
    日本語の有無は contains_japanese() 側で警告としてのみ扱う（ここでは拒否しない）。
    """
    name = normalize_text(name)
    company = normalize_text(company)
    if not name:
        return EMPTY_NAME
    if len(name) > MAX_NAME_LEN:
        return NAME_TOO_LONG
    if len(company) > MAX_COMPANY_LEN:
        return COMPANY_TOO_LONG
    return ""


def pick_auto_selected(profiles: List[Profile]) -> Optional[Profile]:
    """次回表示時に自動選択するプロフィールを決める

    優先順:
      1. 1件だけならそれ
      2. is_default のもの（複数あれば last_used_at が新しい方。無ければ先頭）
      3. last_used_at が設定されている中で最も新しいもの
      4. いずれも無ければ None（未選択のまま。推測で選ばない）
    """
    if not profiles:
        return None
    if len(profiles) == 1:
        return profiles[0]

    defaults = [p for p in profiles if p.is_default]
    if defaults:
        return sorted(defaults, key=lambda p: p.last_used_at or "", reverse=True)[0]

    used = [p for p in profiles if p.last_used_at]
    if used:
        return sorted(used, key=lambda p: p.last_used_at, reverse=True)[0]

    return None


# ── 例外の判別（秘密情報・内部詳細は一切外へ出さない）──────────────────────────

_DEFAULT_CONFLICT_CONSTRAINT = "sender_profiles_one_default_per_user"


def _is_unique_violation(exc: Exception) -> bool:
    if getattr(exc, "code", "") == "23505":
        return True
    text = str(exc).lower()
    return "duplicate key" in text or "unique constraint" in text


def _unique_violation_message(exc: Exception) -> str:
    """一意制約違反の文言を、違反した制約の種類に応じて選ぶ

    is_defaultは1利用者につき最大1件という制約をDB側でも保証している
    （アプリ側の_clear_other_defaultsは事前のベストエフォートで、同時更新の
    競合までは防げないため）。この制約名が例外に含まれていれば、
    「氏名・会社名が重複」とは別の、競合用の文言を返す。
    """
    if _DEFAULT_CONFLICT_CONSTRAINT in str(exc):
        return DEFAULT_CONFLICT
    return DUPLICATE


# ── 取得 ──────────────────────────────────────────────────────────────────────

def list_profiles(client, user_id: str) -> Optional[List[Profile]]:
    """本人のプロフィール一覧を取得する

    RLSにより他人の行は返らない前提だが、user_idでも明示的に絞る
    （二重に守る。candidates.list_saved_items と同じ考え方）。
    失敗時は None を返す（0件の正常系と区別するため。呼び出し側は
    None を LIST_FAILED 表示のトリガーとして扱う）。
    """
    if not user_id:
        return []
    try:
        response = (client.table("sender_profiles")
                   .select(_PROFILE_SELECT)
                   .eq("user_id", user_id)
                   .order("sender_name_en")
                   .execute())
    except Exception:
        return None
    rows = getattr(response, "data", None)
    if rows is None:
        return None
    return [Profile(**row) for row in rows if isinstance(row, dict)]


# ── 登録・更新・削除 ───────────────────────────────────────────────────────────

def _clear_other_defaults(client, user_id: str, keep_id: str = "") -> None:
    """同一利用者の他のis_defaultをfalseに戻す（1利用者につき実質0/1件に保つ）"""
    try:
        query = (client.table("sender_profiles")
                 .update({"is_default": False})
                 .eq("user_id", user_id)
                 .eq("is_default", True))
        if keep_id:
            query = query.neq("id", keep_id)
        query.execute()
    except Exception:
        pass  # 既定フラグの整理に失敗しても、本体の保存自体は妨げない


def create_profile(client, user_id: str, name: Any, company: Any,
                   is_default: bool = False) -> Tuple[Optional[str], str]:
    """新規登録する。戻り値は (profile_id, エラー文言)。成功時エラー文言は空"""
    error = validate_fields(name, company)
    if error:
        return None, error
    if not user_id:
        return None, SAVE_FAILED

    name, company = normalize_text(name), normalize_text(company)
    if is_default:
        _clear_other_defaults(client, user_id)
    try:
        response = (client.table("sender_profiles")
                   .insert({"user_id": user_id, "sender_name_en": name,
                           "company_name_en": company, "is_default": bool(is_default)})
                   .execute())
    except Exception as e:
        if _is_unique_violation(e):
            return None, _unique_violation_message(e)
        return None, SAVE_FAILED

    data = getattr(response, "data", None) or []
    if not data or not isinstance(data[0], dict) or not data[0].get("id"):
        return None, SAVE_FAILED
    return data[0]["id"], ""


def update_profile(client, user_id: str, profile_id: str, name: Any, company: Any,
                   is_default: Optional[bool] = None) -> str:
    """既存プロフィールを更新する。戻り値はエラー文言（成功時は空文字）"""
    error = validate_fields(name, company)
    if error:
        return error
    if not user_id or not profile_id:
        return UPDATE_FAILED

    name, company = normalize_text(name), normalize_text(company)
    fields: Dict[str, Any] = {"sender_name_en": name, "company_name_en": company}
    if is_default is not None:
        fields["is_default"] = bool(is_default)
        if is_default:
            _clear_other_defaults(client, user_id, keep_id=profile_id)

    try:
        (client.table("sender_profiles")
              .update(fields)
              .eq("id", profile_id)
              .eq("user_id", user_id)
              .execute())
    except Exception as e:
        if _is_unique_violation(e):
            return _unique_violation_message(e)
        return UPDATE_FAILED
    return ""


def delete_profile(client, user_id: str, profile_id: str) -> bool:
    """本人のプロフィール1件を削除する"""
    if not user_id or not profile_id:
        return False
    try:
        (client.table("sender_profiles")
              .delete()
              .eq("id", profile_id)
              .eq("user_id", user_id)
              .execute())
        return True
    except Exception:
        return False


def touch_last_used(client, user_id: str, profile_id: str) -> bool:
    """このプロフィールを「最後に使用した」として記録する（営業メール生成の実行時に呼ぶ）"""
    if not user_id or not profile_id:
        return False
    try:
        now_iso = datetime.now(timezone.utc).isoformat()
        (client.table("sender_profiles")
              .update({"last_used_at": now_iso})
              .eq("id", profile_id)
              .eq("user_id", user_id)
              .execute())
        return True
    except Exception:
        return False
