# app.py  —— Site Crawl & Audit (Safe / Health-check / Robust)
import asyncio, json, io
import streamlit as st
from secure_openai_client import get_openai_client
from crawler import crawl_site
from llm import page_audit

# ---------------------------
# Page setup
# ---------------------------
st.set_page_config(page_title="Site Crawl & Audit (Safe)", page_icon="🕸️")
st.title("サイト自動クロール × ChatGPT分析（安全実装）")

# ==== DIAG START (temporary) ====
import os, httpx, streamlit as st
from secure_openai_client import get_openai_api_key
st.write("🔎 Running minimal auth diagnostic...")

key = get_openai_api_key() or ""
st.write("key startswith sk-:", key.startswith("sk-"))
st.write("key length:", len(key))

# 余計な改行や全角が紛れてないか（TrueならOK）
is_ascii = all(ord(c) < 128 for c in key)
st.write("key is ASCII only:", is_ascii)

if not key:
    st.error("キーが読み込まれていません。Secrets/環境変数を確認してください。")
    st.stop()

# ① まずは生HTTPで /v1/models を叩いて401かどうか確認（SDKよりも確実）
try:
    r = httpx.get(
        "https://api.openai.com/v1/models",
        headers={"Authorization": f"Bearer {key}"},
        timeout=15,
        follow_redirects=True,
    )
    st.write("GET /v1/models -> status_code:", r.status_code)
    if r.status_code == 401:
        st.error("401 Unauthorized：キーが無効/読めていない/プロジェクト紐付け不一致の可能性が高いです。")
        st.stop()
    elif r.status_code >= 400:
        st.error(f"HTTPエラー: {r.status_code}. Cloudのネットワーク/一時障害の可能性。")
        st.stop()
except Exception as e:
    st.error(f"HTTP層で例外発生: {e.__class__.__name__}")
    st.stop()

# ② SDKでも最小呼び出し（models.list → chatの順）
from secure_openai_client import get_openai_client
client = get_openai_client()

ok1 = ok2 = False
try:
    _ = client.models.list()
    ok1 = True
    st.write("SDK models.list: OK")
except Exception as e:
    st.error(f"SDK models.list 失敗: {e.__class__.__name__}")
    st.stop()

try:
    _ = client.chat.completions.create(
        model="gpt-4o-mini", messages=[{"role": "user", "content": "ping"}], max_tokens=1, temperature=0
    )
    ok2 = True
    st.write("SDK chat.completions: OK")
except Exception as e:
    st.error(f"SDK chat.completions 失敗: {e.__class__.__name__}")
    st.info("→ モデル権限/組織ポリシー/プロジェクト紐付けが原因の可能性が高いです。")
    st.stop()

st.success("✅ 診断パス：通信・認証ともOK。以降の本処理へ進みます。")
# ==== DIAG END (temporary) ====


# ---------------------------
# Helpers
# ---------------------------
def ensure_openai_client():
    """Secrets/環境変数からOpenAIクライアントを取得し、起動時に疎通確認も行う。"""
    if "openai_client" in st.session_state and st.session_state.get("openai_ok"):
        return st.session_state["openai_client"]

    client = get_openai_client()  # 内部でキー未設定は stop() 済み

    # ✅ ヘルスチェック：モデル一覧呼び出しで“キーの有効性 & 通信”を確認
    try:
        _ = client.models.list()
        st.caption("🟢 OpenAI: 接続確認OK")
        st.session_state["openai_client"] = client
        st.session_state["openai_ok"] = True
        return client
    except Exception as e:
        st.error(f"🔴 OpenAI接続エラー（{e.__class__.__name__}）")
        st.stop()

def run_async(coro):
    """Streamlitで安全にasync関数を実行（既存ループ衝突対策）。"""
    try:
        return asyncio.run(coro)
    except RuntimeError:
        # まれに既存イベントループがある環境向けに新規ループで実行
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

# ---------------------------
# UI — Inputs
# ---------------------------
root_url = st.text_input("開始URL（同一ドメイン内を対象）", placeholder="https://example.com/")
max_pages = st.slider("最大クロール数", 5, 300, 30)

if "cancel" not in st.session_state:
    st.session_state.cancel = False
if "running" not in st.session_state:
    st.session_state.running = False

col1, col2 = st.columns(2)
start_btn = col1.button("クロール + 分析 開始", disabled=st.session_state.running)
cancel_btn = col2.button("中断", disabled=not st.session_state.running)

if cancel_btn:
    st.session_state.cancel = True
    st.info("中断リクエストを受け付けました。進行中のタスクを安全に停止します…")

# ---------------------------
# Run
# ---------------------------
if start_btn:
    if not root_url.strip():
        st.warning("開始URLを入力してください。")
        st.stop()

    st.session_state.cancel = False
    st.session_state.running = True

    client = ensure_openai_client()

    progress = st.empty()
    status_box = st.empty()
    result_holder = st.empty()

    async def main():
        # クロール
        progress.progress(0.0, text="クロール中…")
        try:
            pages = await crawl_site(root_url.strip(), max_pages=max_pages)
        except Exception as e:
            st.error(f"クロールでエラーが発生しました（{e.__class__.__name__}）。URLやrobots.txt、ネットワーク状態をご確認ください。")
            return {}

        if st.session_state.cancel:
            return {}

        if not pages:
            progress.progress(1.0, text="完了（対象ページなし／薄いページのみ）")
            return {}

        # 分析
        progress.progress(0.5, text=f"分析中…（{len(pages)}ページ）")
        results = {}
        total = len(pages)
        for i, (url, meta) in enumerate(pages.items(), start=1):
            if st.session_state.cancel:
                break
            status_box.write(f"解析 {i}/{total}: {url}")
            try:
                audit = page_audit(
                    client,
                    url,
                    meta.get("title", ""),
                    meta.get("text", "")
                )
            except Exception as e:
                # 特定ページの分析失敗はスキップして続行
                audit = {
                    "page_title": meta.get("title", "") or "",
                    "summary": "",
                    "issues": [f"LLM分析エラー: {e.__class__.__name__}"],
                    "recommendations": [],
                    "evidence": [url],
                }
            results[url] = audit

        progress.progress(1.0, text="完了")
        return results

    results = run_async(main())

    st.session_state.running = False

    # ---------------------------
    # Output
    # ---------------------------
    if st.session_state.cancel:
        st.warning("ユーザー操作により中断されました。")
    elif results:
        st.subheader("結果")
        # JSON整形表示（大規模でも軽めに表示したい場合は抜粋に変更可）
        st.json(results)

        # ダウンロード用
        buf = io.StringIO()
        json.dump(results, buf, ensure_ascii=False, indent=2)
        st.download_button(
            "JSONをダウンロード",
            data=buf.getvalue(),
            file_name="audit_results.json",
            mime="application/json"
        )
    else:
        st.info("結果はありません（対象ページが無い、または全てスキップされた可能性があります）。")

