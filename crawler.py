# crawler.py — Secure crawler: SSRF guard, strict robots, size cap, rich extraction + stats
import asyncio, re, ipaddress, socket
from urllib.parse import urljoin, urldefrag, urlparse
import aiohttp
import tldextract
from bs4 import BeautifulSoup
import urllib.robotparser as rp

DEFAULT_DELAY = 0.75
TIMEOUT = aiohttp.ClientTimeout(total=20, connect=5)
MAX_BYTES = 4 * 1024 * 1024  # 4MB 上限

# ===============================
# 🟢 改善済み UA（安全・中立・誤認なし）
# ===============================
UA = "SiteAuditBot/1.0 (+https://github.com/inatsugi1003)"
# ↑ あなたの GitHub ID に置換済み。メールは任意（公開しないなら削除OK）。
# 例）UA = "SiteAuditBot/1.0 (internal use; no contact info)"

def _is_private_ip(host: str) -> bool:
    try:
        infos = socket.getaddrinfo(host, None)
        for family, *_rest, sockaddr in infos:
            ip = sockaddr[0]
            ip_obj = ipaddress.ip_address(ip)
            if (
                ip_obj.is_private or ip_obj.is_loopback or
                ip_obj.is_link_local or ip_obj.is_reserved or ip_obj.is_multicast
            ):
                return True
    except Exception:
        return False
    return False

def normalize_url(base: str, href: str) -> str | None:
    if not href:
        return None
    url = urljoin(base, href)
    url, _ = urldefrag(url)
    p = urlparse(url)
    if p.scheme not in ("http", "https"):
        return None
    base_host = urlparse(base).netloc
    if p.netloc != base_host:
        return None
    host_only = p.hostname or ""
    if _is_private_ip(host_only):
        return None
    return url

async def fetch_text(session: aiohttp.ClientSession, url: str):
    try:
        async with session.get(
            url,
            timeout=TIMEOUT,
            headers={
                "User-Agent": UA,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
                "Accept-Encoding": "gzip, br",
                "Connection": "keep-alive",
                "Cache-Control": "no-cache",
            },
            allow_redirects=True,
        ) as r:
            final = str(r.url)
            fp = urlparse(final)
            if fp.netloc != urlparse(url).netloc or _is_private_ip(fp.hostname or ""):
                return 451, None, {"Final-URL": final, "Reason": "host_changed_or_private"}

            ct = (r.headers.get("Content-Type") or "").lower()
            is_html = ("text/html" in ct) or ("application/xhtml+xml" in ct)

            html = None
            if r.status == 200 and is_html:
                total = 0
                chunks = []
                async for chunk in r.aiter_bytes():
                    total += len(chunk)
                    if total > MAX_BYTES:
                        break
                    chunks.append(chunk)
                html = b"".join(chunks).decode(errors="ignore") if chunks else None

            headers = dict(r.headers)
            headers["Final-URL"] = final
            headers["__status"] = str(r.status)
            headers["__is_html"] = str(bool(is_html))
            return r.status, html, headers
    except Exception as e:
        return 0, None, {"__exc": e.__class__.__name__}

async def get_robots(session, base_url: str):
    base = f"{urlparse(base_url).scheme}://{urlparse(base_url).netloc}"
    robots_url = urljoin(base, "/robots.txt")
    status, txt, _ = await fetch_text(session, robots_url)
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

    h1 = ""
    h1_tag = soup.find("h1")
    if h1_tag:
        h1 = h1_tag.get_text(" ", strip=True)

    viewport = ""
    vp_tag = soup.find("meta", attrs={"name": lambda x: x and x.lower() == "viewport"})
    if vp_tag:
        viewport = vp_tag.get("content") or ""

    has_ldjson = bool(soup.find("script", attrs={"type": "application/ld+json"}))

    img_nodes = soup.find_all("img")
    imgs = []
    for im in img_nodes:
        src = im.get("src") or ""
        alt = im.get("alt") or ""
        imgs.append({"src": src, "alt": alt})

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
