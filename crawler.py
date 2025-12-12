# crawler.py — Secure crawler: SSRF guard, robots, size cap, requests-based fetch, rich stats
import asyncio, re, ipaddress, socket
from urllib.parse import urljoin, urldefrag, urlparse
import requests
import tldextract
from bs4 import BeautifulSoup
import urllib.robotparser as rp

# ===== Tunables =====
DEFAULT_DELAY = 1.2
MAX_BYTES = 4 * 1024 * 1024  # 4MB
REQ_TIMEOUT = (10, 30)  # (connect, read) seconds

# ブラウザ相当のUA（ブロック回避用）
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
)

# ===== Helpers =====
def _is_private_ip(host: str) -> bool:
    try:
        infos = socket.getaddrinfo(host, None)
        for _, _, _, _, sockaddr in infos:
            ip = sockaddr[0]
            ip_obj = ipaddress.ip_address(ip)
            if (
                ip_obj.is_private or ip_obj.is_loopback or ip_obj.is_link_local
                or ip_obj.is_reserved or ip_obj.is_multicast
            ):
                return True
    except Exception:
        return False
    return False

def _etld1(host: str) -> str:
    if not host:
        return ""
    t = tldextract.extract(host)
    return f"{t.domain}.{t.suffix}"

def normalize_url(base: str, href: str) -> str | None:
    if not href:
        return None
    url = urljoin(base, href)
    url, _ = urldefrag(url)
    p = urlparse(url)
    if p.scheme not in ("http", "https"):
        return None
    base_host = urlparse(base).hostname or ""
    host = p.hostname or ""
    if _etld1(base_host) != _etld1(host):
        return None
    if _is_private_ip(host):
        return None
    return url

# ===== HTTP fetch (requests + to_thread) =====
_session = requests.Session()
_session.headers.update({
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "Connection": "keep-alive",
})

def _sync_get(url: str):
    # verify=False で TLS 差異を吸収（取得のみ・保存なしのため運用上許容）
    return _session.get(url, timeout=REQ_TIMEOUT, allow_redirects=True, verify=False, stream=True)

async def fetch_text(url: str):
    """
    HTML取得。requests を to_thread で非同期化。3回までリトライ。
    返り値: (status, html_text_or_None, headers_dict_or_reason)
    """
    last_exc = None
    for _ in range(3):
        try:
            resp = await asyncio.to_thread(_sync_get, url)
            final = resp.url
            fp = urlparse(final)
            orig_host = urlparse(url).hostname or ""
            final_host = fp.hostname or ""

            # eTLD+1外 or プライベートIP は拒否（SSRF防御）
            if _etld1(orig_host) != _etld1(final_host) or _is_private_ip(final_host):
                return 451, None, {"Final-URL": final, "Reason": "host_changed_outside_etld1_or_private"}

            ct = (resp.headers.get("Content-Type") or "").lower()
            is_html = ("text/html" in ct) or ("application/xhtml+xml" in ct)

            html = None
            if resp.status_code == 200 and is_html:
                total = 0
                chunks = []
                for chunk in resp.iter_content(8192):
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > MAX_BYTES:
                        break
                    chunks.append(chunk)
                html = b"".join(chunks).decode(errors="ignore") if chunks else None

            hdrs = dict(resp.headers)
            hdrs["Final-URL"] = final
            hdrs["__status"] = str(resp.status_code)
            hdrs["__is_html"] = str(bool(is_html))

            if html:
                return resp.status_code, html, hdrs
        except Exception as e:
            last_exc = e.__class__.__name__
            await asyncio.sleep(1.0)

    return 0, None, {"__exc": last_exc or "Unknown"}

async def get_robots(base_url: str):
    base = f"{urlparse(base_url).scheme}://{urlparse(base_url).netloc}"
    robots_url = urljoin(base, "/robots.txt")
    status, txt, _ = await fetch_text(robots_url)
    parser = rp.RobotFileParser()
    parser.set_url(robots_url)
    parser.parse((txt or "").splitlines())
    return parser

def allowed(robots: rp.RobotFileParser, url: str) -> bool:
    try:
        return robots.can_fetch(UA, url)
    except Exception:
        return True

def _strip_nav(soup: BeautifulSoup):
    for sel in [
        "nav", "footer", "header", "[role=navigation]", ".menu", ".sidebar",
        ".cookie", ".advert", ".ad", ".ads", ".banner"
    ]:
        for t in soup.select(sel):
            t.decompose()

def extract_rich(url: str, html: str):
    soup = BeautifulSoup(html, "html.parser")

    robots_tag = soup.find("meta", attrs={"name": lambda x: x and x.lower() == "robots"})
    if robots_tag:
        content = (robots_tag.get("content") or "").lower()
        if "noindex" in content or "nofollow" in content:
            return {"skip_by_meta": True}

    _strip_nav(soup)
    main = soup.find("main") or soup.find("article") or soup.body or soup
    text = (main.get_text("\n", strip=True) if main else soup.get_text("\n", strip=True))
    text = re.sub(r"\n{3,}", "\n\n", text)

    title_tag = soup.title
    title = title_tag.get_text(strip=True) if title_tag else ""
    md = ""
    md_tag = soup.find("meta", attrs={"name": lambda x: x and x.lower() == "description"})
    if md_tag:
        md = md_tag.get("content") or ""
    h1 = soup.find("h1")
    h1 = h1.get_text(" ", strip=True) if h1 else ""
    viewport = soup.find("meta", attrs={"name": lambda x: x and x.lower() == "viewport"})
    viewport = viewport.get("content") if viewport else ""
    has_ldjson = bool(soup.find("script", attrs={"type": "application/ld+json"}))

    imgs = [{"src": im.get("src") or "", "alt": im.get("alt") or ""} for im in soup.find_all("img")]
    links = set()
    for a in soup.find_all("a", href=True):
        nu = normalize_url(url, a["href"])
        if nu:
            links.add(nu)

    words = len(re.findall(r"\w+", text))
    paras = len([p for p in text.split("\n\n") if p.strip()])

    return {
        "skip_by_meta": False,
        "status": 200,
        "url": url,
        "title": title,
        "meta_description": md,
        "h1": h1,
        "viewport": viewport,
        "has_ldjson": has_ldjson,
        "images": imgs,
        "links": list(links),
        "text": text,
        "word_count": words,
        "para_count": paras,
    }

class DomainLimiter:
    def __init__(self, concurrency=2):
        self._sems = {}
        self._conc = concurrency
    def sem(self, url: str) -> asyncio.Semaphore:
        d = tldextract.extract(url)
        domain = f"{d.domain}.{d.suffix}"
        if domain not in self._sems:
            self._sems[domain] = asyncio.Semaphore(self._conc)
        return self._sems[domain]

# ===== Public API =====
async def crawl_site(root_url: str, max_pages=50, min_words=400, include_thin=False):
    visited, queue = set([root_url]), [root_url]
    results = {}
    stats = {
        "crawled": 0,
        "final_kept": 0,
        "filtered_thin": 0,
        "skipped_noindex": 0,
        "robots_denied": 0,
        "fetch_error": 0,
        "status_200_html": 0,
        "fail_samples": [],
    }

    robots = await get_robots(root_url)

    async def worker():
        nonlocal stats
        limiter = DomainLimiter(concurrency=2)
        while queue and len(results) < max_pages:
            url = queue.pop(0)

            if not allowed(robots, url):
                results[url] = {"status": 451, "url": url}
                stats["robots_denied"] += 1
                continue

            if _is_private_ip(urlparse(url).hostname or ""):
                results[url] = {"status": 451, "url": url}
                stats["robots_denied"] += 1
                continue

            async with limiter.sem(url):
                status, html, hdrs = await fetch_text(url)
                stats["crawled"] += 1
                if status != 200 or not html:
                    results[url] = {"status": status, "url": url}
                    stats["fetch_error"] += 1
                    if len(stats["fail_samples"]) < 5:
                        stats["fail_samples"].append({
                            "url": url,
                            "status": status,
                            "final_url": (hdrs or {}).get("Final-URL"),
                            "is_html": (hdrs or {}).get("__is_html"),
                            "content_type": (hdrs or {}).get("Content-Type"),
                            "reason": (hdrs or {}).get("Reason") or (hdrs or {}).get("__exc"),
                        })
                else:
                    stats["status_200_html"] += 1
                    rich = extract_rich(url, html)
                    if rich.get("skip_by_meta"):
                        results[url] = {"status": 200, "url": url, "skipped": True}
                        stats["skipped_noindex"] += 1
                    else:
                        results[url] = rich
                        for link in rich.get("links", []):
                            if link not in visited and len(results) + len(queue) < max_pages:
                                visited.add(link)
                                queue.append(link)
            await asyncio.sleep(DEFAULT_DELAY)

    workers = [asyncio.create_task(worker()) for _ in range(4)]
    await asyncio.gather(*workers)

    final = {}
    for u, v in results.items():
        if v.get("status") == 200 and not v.get("skipped"):
            if include_thin or (v.get("word_count", 0) >= min_words):
                final[u] = v
                stats["final_kept"] += 1
            else:
                stats["filtered_thin"] += 1
    return final, stats
