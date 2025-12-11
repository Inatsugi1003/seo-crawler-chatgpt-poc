# secure_openai_client.py
import os
import streamlit as st
from openai import OpenAI

def _read_key_from_secrets() -> str | None:
    try:
        val = st.secrets.get("OPENAI_API_KEY")
        if isinstance(val, str) and val.strip():
            return val.strip()
    except Exception:
        pass
    return None

def _read_key_from_env() -> str | None:
    val = os.getenv("OPENAI_API_KEY")
    if isinstance(val, str) and val.strip():
        return val.strip()
    return None

def get_openai_api_key() -> str | None:
    return _read_key_from_secrets() or _read_key_from_env()

def get_openai_client() -> OpenAI:
    key = get_openai_api_key()
    if not key:
        st.error(
            "OpenAI APIキーが見つかりません。\n\n"
            "▶ **Streamlit Cloud**: *App → Settings → Secrets* に次を保存してください。\n"
            "```\nOPENAI_API_KEY=\"sk-xxxxxxxxxxxxxxxx\"\n```\n"
            "▶ **ローカル**: プロジェクト直下の `.env` に同様の行を追加（`.gitignore` 済）。"
        )
        st.stop()
    try:
        return OpenAI(api_key=key)
    except Exception as e:
        # エラー種別だけ表示（メッセージに秘密は含まれません）
        st.error(
            "OpenAI クライアントの初期化に失敗しました。"
            f"\n- Error: **{e.__class__.__name__}**"
            "\n- 対処: requirements.txt に `httpx==0.27.2`, `httpcore==1.0.5` を追加し再デプロイしてください。"
        )
        st.stop()
