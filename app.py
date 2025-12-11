import asyncio, json, io
import streamlit as st
from secure_openai_client import get_openai_client
from crawler import crawl_site
from llm import page_audit

st.set_page_config(page_title="Site Crawl & Audit (Safe)", page_icon="🕸️")

st.title("サイト自動クロール × ChatGPT分析（安全実装）")

root_url = st.text_input("開始URL（同一ドメイン内を対象）", placeholder="https://example.com/")
max_pages = st.slider("最大クロール数", 5, 200, 30)
if "cancel" not in st.session_state:
    st.session_state.cancel = False

col1, col2 = st.columns(2)
start_btn = col1.button("クロール+分析 開始")
cancel_btn = col2.button("中断")

if cancel_btn:
    st.session_state.cancel = True
    st.info("中断リクエストを受け付けました。少しお待ちください。")

if start_btn and root_url:
    st.session_state.cancel = False
    client = get_openai_client()

    progress = st.empty()
    status_box = st.empty()
    result_holder = st.empty()

    async def run():
        progress.progress(0.0, text="クロール中…")
        pages = await crawl_site(root_url, max_pages=max_pages)
        if st.session_state.cancel:
            return {}

        progress.progress(0.5, text=f"分析中…（{len(pages)}ページ）")
        results = {}
        i = 0
        for url, meta in pages.items():
            if st.session_state.cancel:
                break
            i += 1
            status_box.write(f"解析 {i}/{len(pages)}: {url}")
            audit = page_audit(client, url, meta.get("title",""), meta.get("text",""))
            results[url] = audit

        progress.progress(1.0, text="完了")
        return results

    results = asyncio.run(run())

    if results:
        st.subheader("結果")
        # 表示（JSON整形）
        st.json(results)

        # エクスポート
        buf = io.StringIO()
        json.dump(results, buf, ensure_ascii=False, indent=2)
        st.download_button("JSONをダウンロード", data=buf.getvalue(), file_name="audit_results.json", mime="application/json")
    else:
        st.warning("結果はありません（中断された可能性があります）")
