# app.py — crawl -> metrics -> LLM suggestions -> dashboard (secure + stats)
import asyncio, json, io, csv
import streamlit as st
from secure_openai_client import get_openai_client
from crawler import crawl_site
from analyzer import compute_metrics
from llm import page_audit

st.set_page_config(page_title="Site Crawl & Audit (Safe)", page_icon="🕸️")
st.title("サイト自動クロール × ChatGPT分析（安全実装・拡張版）")

client = get_openai_client()
try:
    _ = client.models.list()
    st.caption("🟢 OpenAI: 接続確認OK")
except Exception as e:
    st.error(f"OpenAI接続エラー: {e.__class__.__name__}")
    st.stop()

root_url = st.text_input("開始URL（同一ドメイン内を対象）", placeholder="https://example.com/")
max_pages = st.slider("最大クロール数", 5, 300, 40)
colA, colB = st.columns([2,1])
with colA:
    min_words = st.slider("最低本文語数（薄いページ除外）", 0, 1000, 400, 50)
with colB:
    include_thin = st.checkbox("薄いページも含める", value=False, help="チェックすると語数条件を無視して全件を対象にします")

if "cancel" not in st.session_state:
    st.session_state.cancel = False
if "running" not in st.session_state:
    st.session_state.running = False

col1, col2 = st.columns(2)
start_btn = col1.button("クロール + 分析 開始", disabled=st.session_state.running)
cancel_btn = col2.button("中断", disabled=not st.session_state.running)

if cancel_btn:
    st.session_state.cancel = True
    st.info("中断リクエストを受け付けました…")

def run_async(coro):
    try:
        return asyncio.run(coro)
    except RuntimeError:
        loop = asyncio.new_event_loop()
        try:
            asyncio.set_event_loop(loop)
            return loop.run_until_complete(coro)
        finally:
            try:
                loop.run_until_complete(loop.shutdown_asyncgens())
            except Exception:
                pass
            loop.close()

if start_btn:
    if not root_url.strip():
        st.warning("開始URLを入力してください。")
        st.stop()

    st.session_state.running = True
    st.session_state.cancel = False

    progress = st.empty()
    status_box = st.empty()

    async def main():
        progress.progress(0.0, text="クロール中…")
        try:
            pages, stats = await crawl_site(
                root_url.strip(),
                max_pages=max_pages,
                min_words=min_words,
                include_thin=include_thin
            )
        except Exception as e:
            st.error(f"クロールエラー: {e.__class__.__name__}")
            return {}, {}, {}

        if st.session_state.cancel:
            return {}, {}, {}

        with st.expander("クロール内訳（診断）", expanded=True):
    st.write({
        "crawled": stats.get("crawled", 0),
        "status_200_html": stats.get("status_200_html", 0),
        "final_kept": stats.get("final_kept", 0),
        "filtered_thin": stats.get("filtered_thin", 0),
        "skipped_noindex": stats.get("skipped_noindex", 0),
        "robots_denied": stats.get("robots_denied", 0),
        "fetch_error": stats.get("fetch_error", 0),
        "min_words": min_words,
        "include_thin": include_thin,
    })
    fails = stats.get("fail_samples") or []
    if fails:
        st.markdown("**失敗サンプル（最大5件）**")
        for f in fails:
            st.code(f, language="json")


        if not pages:
            progress.progress(1.0, text="完了")
            return {}, {}, stats

        # メトリクス計算
        progress.progress(0.4, text="メトリクス算出中…")
        metrics_map = {u: compute_metrics(p) for u, p in pages.items()}

        # LLM提案
        progress.progress(0.7, text="LLM提案生成中…")
        audits = {}
        total = len(pages)
        for i, (u, page) in enumerate(pages.items(), start=1):
            if st.session_state.cancel:
                break
            status_box.write(f"分析 {i}/{total}: {u}")
            try:
                audits[u] = page_audit(client, page, metrics_map[u])
            except Exception as e:
                audits[u] = {
                    "summary": "",
                    "top_issues": [f"LLMエラー: {e.__class__.__name__}"],
                    "recommendations": []
                }

        progress.progress(1.0, text="完了")
        return metrics_map, audits, stats

    metrics_map, audits, stats = run_async(main())
    st.session_state.running = False

    if not metrics_map:
        st.info("対象ページがありません。（語数条件や noindex/robots により除外された可能性があります）")
        st.stop()

    st.subheader("ページ別スコア（SEO/UX）")
    rows = []
    for u, m in metrics_map.items():
        rows.append({
            "URL": u,
            "Title": (m.get("title") or "")[:60],
            "SEO": m.get("seo_score"),
            "UX": m.get("ux_score"),
            "Words": m.get("word_count"),
            "Alt%": m.get("images_alt_ratio"),
            "Links": m.get("internal_links"),
            "LD+JSON": "Yes" if m.get("has_ldjson") else "No",
            "Viewport": "Yes" if m.get("has_viewport") else "No",
            "MetaDesc": "Yes" if m.get("has_meta_description") else "No",
            "H1": "Yes" if m.get("has_h1") else "No",
        })
    st.dataframe(rows, use_container_width=True, hide_index=True)

    st.subheader("詳細（提案）")
    for u in sorted(metrics_map.keys()):
        with st.expander(u, expanded=False):
            m = metrics_map[u]
            a = audits.get(u, {})
            st.markdown(f"**Title:** {m.get('title','')}")
            st.markdown(f"- SEO: {m.get('seo_score')} / UX: {m.get('ux_score')}")
            st.markdown(f"- Words: {m.get('word_count')}  Links: {m.get('internal_links')}  Alt%: {m.get('images_alt_ratio')}")
            st.markdown(f"- LD+JSON: {'Yes' if m.get('has_ldjson') else 'No'} / Viewport: {'Yes' if m.get('has_viewport') else 'No'} / MetaDesc: {'Yes' if m.get('has_meta_description') else 'No'} / H1: {'Yes' if m.get('has_h1') else 'No'}")
            if a.get("summary"):
                st.markdown(f"**Summary:** {a['summary']}")
            if a.get("top_issues"):
                st.markdown("**Top Issues:**")
                for it in a["top_issues"]:
                    st.write(f"- {it}")
            if a.get("recommendations"):
                st.markdown("**Recommendations:**")
                for it in a["recommendations"]:
                    st.write(f"- {it}")

    st.subheader("エクスポート")
    bundle = {u: {"metrics": metrics_map[u], "audit": audits.get(u, {})} for u in metrics_map}
    buf = io.StringIO()
    json.dump(bundle, buf, ensure_ascii=False, indent=2)
    st.download_button("JSON（全件）をダウンロード", data=buf.getvalue(),
                       file_name="audit_full.json", mime="application/json")

    csv_buf = io.StringIO()
    fieldnames = ["URL","Title","SEO","UX","Words","Alt%","Links","LD+JSON","Viewport","MetaDesc","H1"]
    writer = csv.DictWriter(csv_buf, fieldnames=fieldnames)
    writer.writeheader()
    for r in rows:
        writer.writerow(r)
    st.download_button("CSV（スコア表）をダウンロード", data=csv_buf.getvalue(),
                       file_name="audit_scores.csv", mime="text/csv")

