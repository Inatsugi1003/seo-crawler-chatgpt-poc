# secure_openai_client.py  ← 差し替え
import os
import streamlit as st
from openai import OpenAI

def _read_key_from_secrets() -> str | None:
    try:
        # st.secrets は KeyError を投げないので get でOK
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
            "▶ **Streamlit Cloud**: *App → Settings → Secrets* に下記を追加してください。\n"
            "```\nOPENAI_API_KEY=\"sk-xxxxxxxxxxxxxxxx\"\n```\n"
            "▶ **ローカル**: `.env` に同様の行を作成（`.gitignore`で保護済み）。"
        )
        st.stop()
    try:
        return OpenAI(api_key=key)
    except Exception:
        # ここは詳細を出さない（Cloudは自動でマスクする）
        st.error("OpenAIクライアントの初期化に失敗しました。APIキー形式や依存関係をご確認ください。")
        st.stop()
