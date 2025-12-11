# secure_openai_client.py  (auth-diag + org/project対応)
import os, re
import unicodedata
import streamlit as st
from openai import OpenAI

ASCII_SAFE = re.compile(r"^[\x20-\x7E]+$")           # 可視ASCIIのみ
KEY_PATTERN = re.compile(r"^sk-[A-Za-z0-9\-\_]{20,}$")  # おおよその検証

def _mask(k: str) -> str:
    if not k: return "(missing)"
    if len(k) <= 10: return k[:2] + "..." + k[-2:]
    return f"{k[:5]}...{k[-4:]} (len={len(k)})"

def _clean(s: str | None) -> str | None:
    if not isinstance(s, str): return None
    # ゼロ幅スペースなど“見えない文字”を除去
    s = "".join(ch for ch in s if unicodedata.category(ch)[0] != "C")
    s = s.strip()
    return s

def _get(name: str) -> str | None:
    # Secrets優先 → env
    v = st.secrets.get(name, None)
    if v is None: v = os.getenv(name)
    v = _clean(v)
    return v if v else None

def _validate_key(key: str) -> list[str]:
    errs = []
    if not KEY_PATTERN.match(key or ""):
        errs.append("キー形式が想定外です（'sk-'で開始し、英数/ハイフン/アンダースコアのみ）。")
    if not ASCII_SAFE.match(key):
        errs.append("非ASCII文字（全角・ゼロ幅など）が含まれています。手入力で貼り直してください。")
    return errs

def get_openai_client() -> OpenAI:
    api_key = _get("OPENAI_API_KEY")
    org_id  = _get("OPENAI_ORG_ID")     # あれば使用（任意）
    project = _get("OPENAI_PROJECT")     # あれば使用（任意）

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
        st.info(f"検出値（マスク）: {_mask(api_key)}")
        st.stop()

    try:
        client = OpenAI(api_key=api_key, organization=org_id, project=project)
    except Exception as e:
        st.error(
            "OpenAIクライアントの初期化に失敗しました。\n"
            f"- Error: **{e.__class__.__name__}**\n"
            "- 対処: 新しいキーを発行し直す / `OPENAI_ORG_ID` / `OPENAI_PROJECT` を設定"
        )
        st.info(f"APIキー（マスク）: {_mask(api_key)}")
        if org_id: st.caption(f"ORG（検出）: {org_id}")
        if project: st.caption(f"PROJECT（検出）: {project}")
        st.stop()

    return client
