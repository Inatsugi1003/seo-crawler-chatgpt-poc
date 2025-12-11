# secure_openai_client.py  —— expose get_openai_api_key + robust client
import os, re, unicodedata
import streamlit as st
from openai import OpenAI

ASCII_SAFE = re.compile(r"^[\x20-\x7E]+$")
KEY_PATTERN = re.compile(r"^sk-[A-Za-z0-9\-\_]{20,}$")

def _clean(s: str | None) -> str | None:
    if not isinstance(s, str):
        return None
    # ゼロ幅や制御文字を除去してからstrip
    s = "".join(ch for ch in s if unicodedata.category(ch)[0] != "C")
    s = s.strip()
    return s or None

def _get_from_secrets(name: str) -> str | None:
    try:
        v = st.secrets.get(name, None)
    except Exception:
        v = None
    return _clean(v)

def _get_from_env(name: str) -> str | None:
    return _clean(os.getenv(name))

def get_openai_api_key() -> str | None:
    """Secrets優先でAPIキー文字列を返す。無ければNone。"""
    return _get_from_secrets("OPENAI_API_KEY") or _get_from_env("OPENAI_API_KEY")

def _validate_key(key: str) -> list[str]:
    errs = []
    if not KEY_PATTERN.match(key or ""):
        errs.append("キー形式が想定外です（'sk-'で開始し、英数/ハイフン/アンダースコアのみ）。")
    if not ASCII_SAFE.match(key or ""):
        errs.append("非ASCII文字（全角・ゼロ幅/改行など）が含まれています。手入力で貼り直してください。")
    return errs

def get_openai_client() -> OpenAI:
    api_key = get_openai_api_key()
    org_id  = _get_from_secrets("OPENAI_ORG_ID")  or _get_from_env("OPENAI_ORG_ID")
    project = _get_from_secrets("OPENAI_PROJECT") or _get_from_env("OPENAI_PROJECT")

    if not api_key:
        st.error(
            "OpenAI APIキーが見つかりません。\n\n"
            "▶ **Streamlit Cloud**: *App → Settings → Secrets*\n"
            "```\nOPENAI_API_KEY=\"sk-xxxxxxxx...\"\n```\n"
            "（必要に応じて）\n"
            "```\nOPENAI_ORG_ID=\"org_...\"\nOPENAI_PROJECT=\"proj_...\"\n```"
        )
        st.stop()

    problems = _validate_key(api_key)
    if problems:
        st.error("APIキーに問題があります：\n- " + "\n- ".join(problems))
        st.stop()

    try:
        return OpenAI(api_key=api_key, organization=org_id, project=project)
    except Exception:
        st.error(
            "OpenAIクライアントの初期化に失敗しました。"
            "\n- 対処: 新しいキーを発行し直す / `OPENAI_ORG_ID` / `OPENAI_PROJECT` を設定"
        )
        st.stop()
