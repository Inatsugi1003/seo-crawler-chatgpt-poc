# crawler.py — crawl + rich extraction (meta/h1/links/img/ld+json/viewport)
import asyncio, re
from urllib.parse import urljoin, urldefrag, urlparse
import aiohttp
import tldextract
from bs4 import BeautifulSoup

DEFAULT_DELAY = 0.75
TIMEOUT = aiohttp.ClientTimeout(total=20, connect=5)

def normalize_url(base: str, href: str) -> str | None:
    if not href: return None
    url = urljoin(base, href)
    url, _ = urldefrag(url)
    p = urlparse(url)
    if not p.scheme.startswith("http"): return None
    # 同一ホストのみ
    if urlparse(base).netloc != p.netloc: return None
    return url

async def fetch_text(session: aiohttp.ClientSession, url: str):
    try:
        async with session.get(url, timeout=TIMEOUT, headers={"Accept-Encoding": "gzip, br"}) as r:
            ct = r.headers.get("Content-Type","")
            html = await r.text(errors="ignore") if (r.status == 200 and "text/html" in ct) else None
            return r.status, html, dict(r.headers)
    except Exception:
        return 0, None, {}

def parse_robots(robots_txt: str):
    disallows, crawl_delay = [], None
    blocks = re.split(r'(?i)User-agent:\s*\*', robots_txt)
    rules = blocks[-1] if len(blocks) > 1 else robots_txt
    for line in rules.splitlines():
        m = re.search(r'(?i)Disallow:\s*(\S+)', line)
        if m: disallows.append(m.group(1).strip())
        m2 = re.search(r'(?i)Crawl-delay:\s*([\d\.]+)', line)
        if m2: crawl_delay = float(m2.group(1))
    return disallows, crawl_delay

async def get_robots_and_delay(session, base_url: str):
    base = f"{urlparse(base_url).scheme}://{urlparse(base_url).netloc}"
    status, txt, _ = await fetch_text(session, urljoin(base, "/robots.txt"))
    disallows, crawl_delay = parse_robots(txt or "")
    return disallows, crawl_delay if crawl_delay else DEFAULT_DELAY

def allowed(disallows: list[str], path: str) -> bool:
    if not disallows: return True
    return not any(path.startswith(d) for d in disallows if d and d != "/")

def _strip_nav(soup: BeautifulSoup):
    for sel in ["nav", "footer", "header", "[role=navigation]", ".menu", ".sidebar", ".cookie", ".advert"]:
        for t in soup.select(sel):
            t.decompose()

def extract_rich(url: str, html: str):
    soup = BeautifulSoup(html, "html.parser")
    # meta robots
    robots_tag = soup.find("meta", attrs={"name": lambda x: x and x.lower()=="robots"})
    if robots_tag:
        content = (robots_tag.get("content") or "").lower()
        if "noindex" in content or "nofollow" in content:
            return {"skip_by_meta": True}

    # remove nav/footer before text extraction
    _strip_nav(soup)
    main = soup.find("main") or soup.find("article") or soup.body or soup
    text = (main.get_text("\n", strip=True) if main else soup.get_text("\n", strip=True))
    text = re.sub(r"\n{3,}", "\n\n", text)

    title_tag = soup.title
    title = title_tag.get_text(strip=True) if title_tag else ""

    md = ""
    md_tag = soup.find("meta", attrs={"name": lambda x: x and x.lower()=="description"})
    if md_tag: md = md_tag.get("content") or ""

    h1 = ""
    h1_tag = soup.find("h1")
    if h1_tag: h1 = h1_tag.get_text(" ", strip=True)

    # viewport
    viewport = ""
    vp_tag = soup.find("meta", attrs={"name": lambda x: x and x.lower()=="viewport"})
    if vp_tag: viewport = vp_tag.get("content") or ""

    # ld+json
    has_ldjson = bool(soup.find("script", attrs={"type": "application/ld+json"}))

    # images (alt)
    img_nodes = soup.find_all("img")
    imgs = []
    for im in img_nodes:
        src = im.get("src") or ""
        alt = im.get("alt") or ""
        imgs.append({"src": src, "alt": alt})

    # internal links
    links = set()
    for a in soup.find_all("a", href=True):
        nu = normalize_url(url, a["href"])
        if nu: links.add(nu)

    # word/para count（簡易）
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
        "images": imgs,                 # [{src, alt}]
        "links": list(links),           # internal only
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

async def crawl_site(root_url: str, max_pages=50):
    visited, queue = set([root_url]), [root_url]
    results = {}  # url -> rich dict
    limiter = DomainLimiter(concurrency=2)

    async with aiohttp.ClientSession() as session:
        disallows, delay = await get_robots_and_delay(session, root_url)

        async def worker():
            while queue and len(results) < max_pages:
                url = queue.pop(0)
                path = urlparse(url).path or "/"
                if not allowed(disallows, path):
                    results[url] = {"status": 451, "url": url}
                    continue
                sem = limiter.sem(url)
                async with sem:
                    status, html, _ = await fetch_text(session, url)
                    if status != 200 or not html:
                        results[url] = {"status": status, "url": url}
                    else:
                        rich = extract_rich(url, html)
                        if rich.get("skip_by_meta"):
                            results[url] = {"status": 200, "url": url, "skipped": True}
                        else:
                            results[url] = rich
                            for link in rich.get("links", []):
                                if link not in visited and len(results) + len(queue) < max_pages:
                                    visited.add(link)
                                    queue.append(link)
                await asyncio.sleep(delay)

        workers = [asyncio.create_task(worker()) for _ in range(4)]
        await asyncio.gather(*workers)

    # 薄いページ除外（例：本文400語未満）
    final = {}
    for u, v in results.items():
        if v.get("status") == 200 and not v.get("skipped") and v.get("word_count", 0) >= 400:
            final[u] = v
    return final
