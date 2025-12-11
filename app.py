# -*- coding: utf-8 -*-
# SEO Crawler Audit (Streamlit, Cloud-ready)
# 機能: robots.txt準拠でサイトをクロールし、主要なSEO不備を自動抽出・CSVダウンロード

import asyncio, re, time, io, csv
from dataclasses import dataclass, asdict
from urllib.parse import urljoin, urldefrag, urlparse
import urllib.robotparser as robotparser

import aiohttp
from aiohttp import ClientTimeout
import streamlit as st
from bs4 import BeautifulSoup
import tldextract

# =============== UI ===============
st.set_page_config(page_title="SEO Crawler Audit", layout="wide")
st.title("🕷️ SEO Crawler Audit（Webアプリ）")
st.write("URLを入力して［クロール開始］を押すと、サイト内の主要なSEO不備を自動抽出します。")

col = st.columns(4)
start_url = col[0].text_input("開始URL（例: https://example.com/）", "https://example.com/")
max_pages = col[1].number_input("最大クロールページ数", 10, 5000, 200, step=10)
max_depth = col[2].number_input("最大深さ", 1, 20, 5)
concurrency = col[3].number_input("同時接続数", 1, 32, 8)

col2 = st.columns(4)
delay_ms = col2[0].number_input("リクエスト間隔（ms/ホスト）", 0, 5000, 200)
ua = col2[1].text_input("User-Agent", "SEO-Audit-Bot/1.0 (+https://example.com)")
same_registrable = col2[2].selectbox("クロール範囲", ["同一ホストのみ", "同一レジストラブルドメイン"], index=1)
respect_robots = col2[3].checkbox("robots.txtを尊重", value=True)

inc_pat = st.text_input("含めるURLパターン（正規表現、任意）", "")
exc_pat = st.text_input("除外するURLパターン（正規表現、任意）", r"\.(pdf|jpg|jpeg|png|gif|svg|webp|css|js|zip|mp4|mp3)(\?|$)")

run = st.button("🚀 クロール開始")

# =============== ユーティリティ ===============
def norm_url(u: str, base: str) -> str:
    if not u: return ""
    u = urljoin(base, u)
    u, _ = urldefrag(u)  # remove fragment
    return u

def same_scope(u: str, seed: str, registrable: bool) -> bool:
    pu, ps = urlparse(u), urlparse(seed)
    if registrable:
        du = tldextract.extract(pu.netloc)
        ds = tldextract.extract(ps.netloc)
        return (du.domain, du.suffix) == (ds.domain, ds.suffix)
    return pu.netloc == ps.netloc

def title_len_ok(t):
    if not t: return False, "タイトル欠落"
    l = len(t.strip())
    if l < 30: return False, f"タイトル短い({l})"
    if l > 65: return False, f"タイトル長い({l})"
    return True, ""

def desc_len_ok(d):
    if not d: return False, "ディスクリプション欠落"
    l = len(d.strip())
    if l < 70: return False, f"D短い({l})"
    if l > 160: return False, f"D長い({l})"
    return True, ""

def words_count(text: str) -> int:
    return len(re.findall(r"\w+", text or ""))

def is_html(resp) -> bool:
    ct = resp.headers.get("Content-Type","").lower()
    return "text/html" in ct or "application/xhtml+xml" in ct

def parse_robots(seed: str, ua: str):
    rp = robotparser.RobotFileParser()
    origin = f"{urlparse(seed).scheme}://{urlparse(seed).netloc}"
    rp.set_url(urljoin(origin, "/robots.txt"))
    try:
        rp.read()
    except Exception:
        pass
    return rp

def xrobots_noindex(headers) -> bool:
    v = headers.get("x-robots-tag", "")
    return "noindex" in v.lower()

def xrobots_nofollow(headers) -> bool:
    v = headers.get("x-robots-tag", "")
    return "nofollow" in v.lower()

# =============== データ構造 ===============
@dataclass
class PageAudit:
    url: str
    status: int
    depth: int
    final_url: str
    redirected: int
    canonical: str
    canonical_status: str
    robots_meta: str
    noindex: bool
    nofollow: bool
    x_noindex: bool
    x_nofollow: bool
    title: str
    title_issue: str
    description: str
    desc_issue: str
    h1_count: int
    images: int
    images_missing_alt: int
    internal_links: int
    external_links: int
    broken_internal_links: int
    word_count: int

# =============== コアクロール ===============
async def crawl(seed, max_pages, max_depth, concurrency, delay_ms, ua, inc_pat, exc_pat, respect_robots, registrable_scope):
    sem = asyncio.Semaphore(concurrency)
    seen, results, link_graph = set(), [], {}
    queue = asyncio.Queue()
    await queue.put((seed, 0))
    seen.add(seed)

    include_re = re.compile(inc_pat) if inc_pat else None
    exclude_re = re.compile(exc_pat) if exc_pat else None
    rp = parse_robots(seed, ua) if respect_robots else None
    last_req_time = {}
    TIMEOUT = ClientTimeout(total=20)

    async def polite_wait(host):
        # 簡易レート制御：ホストごとにdelay_ms待機
        if delay_ms <= 0: return
        t = time.time()
        last = last_req_time.get(host, 0)
        wait = (last + delay_ms/1000.0) - t
        if wait > 0:
            await asyncio.sleep(wait)

    async with aiohttp.ClientSession(timeout=TIMEOUT, headers={"User-Agent": ua}) as session:
        async def fetch(url, depth):
            host = urlparse(url).netloc
            await polite_wait(host)
            last_req_time[host] = time.time()
            redirected = 0
            try:
                async with session.get(url, allow_redirects=True) as resp:
                    status = resp.status
                    final_url = str(resp.url)
                    redirected = len(resp.history)
                    if not is_html(resp):
                        return status, final_url, "", None
                    html = await resp.text(errors="ignore")
                    return status, final_url, html, resp.headers
            except Exception:
                return 0, url, "", {}

        async def worker():
            nonlocal results
            while not queue.empty() and len(results) < max_pages:
                url, depth = await queue.get()

                # robots.txt
                if respect_robots and rp is not None and not rp.can_fetch(ua, url):
                    queue.task_done()
                    continue

                status, final_url, html, headers = await fetch(url, depth)
                if not html:
                    # 非HTML or エラー
                    results.append(PageAudit(
                        url=url, status=status, depth=depth, final_url=final_url, redirected=0,
                        canonical="", canonical_status="", robots_meta="", noindex=False, nofollow=False,
                        x_noindex=xrobots_noindex(headers or {}), x_nofollow=xrobots_nofollow(headers or {}),
                        title="", title_issue="非HTML/取得不可", description="", desc_issue="",
                        h1_count=0, images=0, images_missing_alt=0, internal_links=0, external_links=0,
                        broken_internal_links=0, word_count=0
                    ))
                    queue.task_done()
                    continue

                soup = BeautifulSoup(html, "html.parser")

                # robots meta
                robots_meta = ""
                meta_robots = soup.find("meta", attrs={"name":"robots"})
                if meta_robots and meta_robots.get("content"):
                    robots_meta = meta_robots.get("content","").lower()
                noindex = "noindex" in robots_meta
                nofollow = "nofollow" in robots_meta

                # title / description
                title = (soup.title.string.strip() if soup.title and soup.title.string else "").strip()
                title_ok, title_issue = title_len_ok(title)
                desc = ""
                md = soup.find("meta", attrs={"name":"description"})
                if md and md.get("content"):
                    desc = md.get("content","").strip()
                desc_ok, desc_issue = desc_len_ok(desc)

                # canonical
                canonical = ""
                link_c = soup.find("link", rel=lambda v: v and "canonical" in v)
                if link_c and link_c.get("href"):
                    canonical = norm_url(link_c.get("href"), final_url)
                canonical_status = "OK"
                if canonical and canonical.rstrip("/") != final_url.rstrip("/"):
                    canonical_status = "自己参照ではない"

                # hreflang（存在チェックのみ）
                # hreflangs = soup.find_all("link", rel=lambda v: v and "alternate" in v, hreflang=True)

                # images alt
                images = soup.find_all("img")
                img_total = len(images)
                img_miss = sum(1 for im in images if not im.get("alt"))

                # links
                a_tags = soup.find_all("a", href=True)
                intern = extern = 0
                broken_internal = 0
                children = []
                for a in a_tags:
                    href = norm_url(a.get("href"), final_url)
                    if not href.startswith(("http://","https://")):
                        continue
                    # フィルタ
                    if include_re and not include_re.search(href):
                        continue
                    if exclude_re and exclude_re.search(href):
                        continue
                    if not same_scope(href, start_url, registrable_scope):
                        extern += 1
                        continue
                    intern += 1
                    children.append(href)

                link_graph[final_url] = children

                # ワード数（本文テキストの概算）
                for s in soup(["script","style","noscript"]): s.decompose()
                text = soup.get_text(" ", strip=True)
                wc = words_count(text)

                # ページ結果
                results.append(PageAudit(
                    url=url, status=status, depth=depth, final_url=final_url, redirected=redirected,
                    canonical=canonical, canonical_status=canonical_status,
                    robots_meta=robots_meta, noindex=noindex, nofollow=nofollow,
                    x_noindex=xrobots_noindex(headers or {}), x_nofollow=xrobots_nofollow(headers or {}),
                    title=title, title_issue=("" if title_ok else title_issue),
                    description=desc, desc_issue=("" if desc_ok else desc_issue),
                    h1_count=len(soup.find_all("h1")),
                    images=img_total, images_missing_alt=img_miss,
                    internal_links=intern, external_links=extern,
                    broken_internal_links=broken_internal,  # 簡易
                    word_count=wc
                ))

                # 次URL投入（BFS）
                if depth < max_depth:
                    for nxt in children:
                        if nxt in seen: continue
                        if len(results) + queue.qsize() >= max_pages: break
                        if respect_robots and rp is not None and not rp.can_fetch(ua, nxt):
                            continue
                        seen.add(nxt)
                        await queue.put((nxt, depth+1))

                queue.task_done()

        workers = [asyncio.create_task(worker()) for _ in range(concurrency)]
        await queue.join()
        for w in workers:
            w.cancel()
        return results

# =============== 実行 ===============
if run:
    # 入力チェック
    try:
        p = urlparse(start_url)
        assert p.scheme in ("http","https") and p.netloc
    except Exception:
        st.error("開始URLが不正です。https:// から始まるURLを入力してください。")
        st.stop()

    st.info("クロールを開始しました。完了までしばらくお待ちください。")
    progress = st.progress(0)
    status_box = st.empty()

    # 進捗の見せ方：適当に数回更新（簡易）
    async def run_crawl():
        registrable_scope = (same_registrable == "同一レジストラブルドメイン")
        res = await crawl(
            start_url.strip(),
            int(max_pages),
            int(max_depth),
            int(concurrency),
            int(delay_ms),
            ua.strip(),
            inc_pat.strip(),
            exc_pat.strip(),
            respect_robots,
            registrable_scope
        )
        return res

    results = asyncio.run(run_crawl())

    # 進捗UI更新（完了）
    progress.progress(100)
    status_box.success(f"クロール完了: {len(results)}ページ")

    # 集計と不備抽出
    import pandas as pd
    df = pd.DataFrame([asdict(r) for r in results])

    # 重複タイトル、薄いコンテンツ、メタ欠落 等
    issues = []
    if not df.empty:
        # 重複タイトル
        dup_titles = (df[df["title"].str.len()>0]
                      .groupby("title").size().reset_index(name="count")
                      .query("count > 1"))
        if not dup_titles.empty:
            issues.append(f"重複タイトル {len(dup_titles)}件")

        # タイトル/ディスクリプション欠落・長短
        bad_title = df[df["title_issue"]!=""]
        bad_desc  = df[df["desc_issue"]!=""]

        # noindex/nofollow
        noindex_pages = df[df["noindex"] | df["x_noindex"]]
        # H1異常
        h1_anom = df[(df["h1_count"]==0) | (df["h1_count"]>1)]
        # 画像alt欠落率
        img_rows = df[df["images"]>0]
        img_bad = img_rows[ img_rows["images_missing_alt"]/img_rows["images"] > 0.3 ]
        # 薄いコンテンツ
        thin = df[df["word_count"] < 300]

        summary = {
            "クロール総数": len(df),
            "200ページ数": int((df["status"]==200).sum()),
            "リダイレクト": int((df["redirected"]>0).sum()),
            "エラー(>=400)": int((df["status"]>=400).sum()),
            "タイトル問題": len(bad_title),
            "メタD問題": len(bad_desc),
            "noindex検出": len(noindex_pages),
            "H1異常": len(h1_anom),
            "画像alt>30%欠落": len(img_bad),
            "薄いコンテンツ(<300語)": len(thin),
            "重複タイトルグループ": len(dup_titles),
        }

        st.subheader("📊 サマリー")
        st.table(pd.DataFrame(list(summary.items()), columns=["項目","件数"]))

        st.subheader("🛠️ 不備リスト（主要）")
        tabs = st.tabs(["タイトル問題","メタD問題","noindex","H1異常","画像alt欠落率高","薄いコンテンツ","重複タイトル"])
        with tabs[0]:
            st.dataframe(bad_title[["final_url","title","title_issue","status","depth"]])
        with tabs[1]:
            st.dataframe(bad_desc[["final_url","description","desc_issue","status","depth"]])
        with tabs[2]:
            st.dataframe(noindex_pages[["final_url","robots_meta","x_noindex","x_nofollow","status"]])
        with tabs[3]:
            st.dataframe(h1_anom[["final_url","h1_count","status","depth","title"]])
        with tabs[4]:
            st.dataframe(img_bad[["final_url","images","images_missing_alt","status","depth"]])
        with tabs[5]:
            st.dataframe(thin[["final_url","word_count","status","depth","title"]])
        with tabs[6]:
            if dup_titles.empty:
                st.write("重複タイトルなし")
            else:
                st.dataframe(dup_titles)

        st.subheader("📄 全ページ結果")
        st.dataframe(df)

        # ダウンロード（CSV）
        csv_buf = io.StringIO()
        df.to_csv(csv_buf, index=False, encoding="utf-8")
        st.download_button("📥 全結果CSVをダウンロード", data=csv_buf.getvalue().encode("utf-8"),
                           file_name="seo_crawl_audit.csv", mime="text/csv")

        # レポート（簡易HTML）
        html_buf = io.StringIO()
        html_buf.write("<html><head><meta charset='utf-8'><title>SEO Crawl Report</title></head><body>")
        html_buf.write("<h1>SEO Crawl Report</h1>")
        html_buf.write("<h2>Summary</h2><ul>")
        for k,v in summary.items():
            html_buf.write(f"<li>{k}: {v}</li>")
        html_buf.write("</ul>")
        html_buf.write("<h2>Pages</h2>")
        html_buf.write(df.to_html(index=False))
        html_buf.write("</body></html>")
        st.download_button("📥 HTMLレポートをダウンロード", data=html_buf.getvalue().encode("utf-8"),
                           file_name="seo_crawl_report.html", mime="text/html")

    else:
        st.warning("有効なページを取得できませんでした。開始URL/robots/範囲設定をご確認ください。")

