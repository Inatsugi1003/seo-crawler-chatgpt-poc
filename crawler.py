import asyncio, re, time
from urllib.parse import urljoin, urldefrag, urlparse
import aiohttp
import tldextract
from bs4 import BeautifulSoup

DEFAULT_DELAY = 0.75  # robotsに無ければ礼儀ディレイ
TIMEOUT = aiohttp.ClientTimeout(total=20, connect=5)

def normalize_url(base: str, href: str) -> str | None:
    if not href: return None
    url = urljoin(base, href)
    url, _ = urldefrag(url)
    p = urlparse(url)
    if not p.scheme.startswith("http"): return None
    # 同一ホスト内のみクロールする
    if urlparse(base).netloc != p.netloc: return None
    return url

async def fetch_text(session: aiohttp.ClientSession, url: str) -> tuple[int, str | None, dict]:
    try:
        async with session.get(url, timeout=TIMEOUT, headers={"Accept-Encoding": "gzip, br"}) as r:
            if r.status == 200 and "text/html" in r.headers.get("Content-Type",""):
                return r.status, await r.text(errors="ignore"), dict(r.headers)
            return r.status, None, dict(r.headers)
    except Exception:
        return 0, None, {}

def parse_robots(robots_txt: str):
    disallows, crawl_delay = [], None
    # 最小実装：User-agent:* のセクションのみ評価
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

def should_skip_by_meta(html: str) -> bool:
    # <meta name="robots" content="noindex,nofollow"> などを弾く
    soup = BeautifulSoup(html, "html.parser")
    tag = soup.find("meta", attrs={"name": lambda x: x and x.lower() == "robots"})
    if not tag: return False
    content = (tag.get("content") or "").lower()
    return "noindex" in content or "nofollow" in content

def extract_links(base_url: str, html: str) -> set[str]:
    soup = BeautifulSoup(html, "html.parser")
    links = set()
    for a in soup.find_all("a", href=True):
        u = normalize_url(base_url, a["href"])
        if u: links.add(u)
    return links

def extract_main_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    # ナビ/フッター除去の簡易ヒューリスティック
    for sel in ["nav", "footer", "header", "[role=navigation]", ".menu", ".sidebar", ".cookie", ".advert"]:
        for t in soup.select(sel):
            t.decompose()
    main = soup.find("main") or soup.find("article") or soup.body
    text = (main.get_text("\n", strip=True) if main else soup.get_text("\n", strip=True))
    # 余分な空行を軽く圧縮
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text

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
    results = {}  # url -> {"title","text","status"}
    limiter = DomainLimiter(concurrency=2)

    async with aiohttp.ClientSession() as session:
        disallows, delay = await get_robots_and_delay(session, root_url)

        async def worker():
            while queue and len(results) < max_pages:
                url = queue.pop(0)
                path = urlparse(url).path or "/"
                if not allowed(disallows, path):
                    results[url] = {"status": 451, "title":"", "text":""}
                    continue
                sem = limiter.sem(url)
                async with sem:
                    status, html, headers = await fetch_text(session, url)
                    if status != 200 or not html:
                        results[url] = {"status": status, "title":"", "text":""}
                    else:
                        if should_skip_by_meta(html):
                            results[url] = {"status": 200, "title":"", "text":""}
                        else:
                            text = extract_main_text(html)
                            title = BeautifulSoup(html, "html.parser").title
                            results[url] = {"status": 200, "title": (title.get_text(strip=True) if title else ""), "text": text}
                            for link in extract_links(url, html):
                                if link not in visited:
                                    visited.add(link)
                                    queue.append(link)
                await asyncio.sleep(delay)

        workers = [asyncio.create_task(worker()) for _ in range(4)]
        await asyncio.gather(*workers)

    # 薄いページ除外（例：500文字未満）
    return {u:v for u,v in results.items() if len(v.get("text","")) >= 500}
