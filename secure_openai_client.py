import os
import streamlit as st
from openai import OpenAI

def get_openai_api_key():
    # Streamlit Secrets > OPENAI_API_KEY を優先
    key = st.secrets.get("OPENAI_API_KEY", None)
    # ローカルの場合は環境変数から取得
    if not key:
        key = os.getenv("OPENAI_API_KEY")
    return key

def get_openai_client():
    key = get_openai_api_key()
    if not key:
        st.error("OpenAI APIキーが設定されていません。")
        st.stop()
    return OpenAI(api_key=key)
