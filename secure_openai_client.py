import os
import streamlit as st
from openai import OpenAI

def _get_key_from_secrets():
    try:
        return st.secrets.get("OPENAI_API_KEY")
    except Exception:
        return None

def _get_key_from_env():
    return os.getenv("OPENAI_API_KEY")

def _get_key_from_user():
    st.info("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")
    key = st.text_input("OpenAI APIキー", type="password", key="openai_key_input")
    if key:
        st.session_state["_openai_key"] = key  # セッションのみ
    return st.session_state.get("_openai_key")

def get_openai_api_key() -> str | None:
    return _get_key_from_secrets() or _get_key_from_env() or _get_key_from_user()

def get_openai_client() -> OpenAI:
    key = get_openai_api_key()
    if not key:
        st.stop()  # 未入力ならここで止めて漏えい経路を作らない
    # キーは絶対にprint/st.write/logに出さない
    return OpenAI(api_key=key)
