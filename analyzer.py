# analyzer.py — rule-based metrics & scores
from collections import Counter

def ratio(a: int, b: int) -> float:
    return (a / b) if b else 0.0

def compute_metrics(page: dict) -> dict:
    """crawler.py の rich dict から、指標とスコアを作る"""
    title = (page.get("title") or "").strip()
    md = (page.get("meta_description") or "").strip()
    h1 = (page.get("h1") or "").strip()
    wc = int(page.get("word_count") or 0)
    paras = int(page.get("para_count") or 0)
    imgs = page.get("images", []) or []
    links = page.get("links", []) or []
    viewport = (page.get("viewport") or "").lower()
    has_ldjson = bool(page.get("has_ldjson"))

    # image alt coverage
    total_img = len(imgs)
    alt_ok = sum(1 for im in imgs if (im.get("alt") or "").strip())
    alt_ratio = ratio(alt_ok, total_img)

    # link depth proxy（URLの / の数）
    depth = page["url"].count("/")

    # internal link fanout
    unique_links = len(set(links))

    # heuristic scores (0-100)
    seo = 0
    seo += 15 if title else 0
    seo += 15 if md else 0
    seo += 10 if h1 else 0
    seo += 10 if has_ldjson else 0
    seo += 10 if alt_ratio >= 0.66 else (5 if alt_ratio >= 0.33 else 0)
    seo += 10 if unique_links >= 10 else (5 if unique_links >= 3 else 0)
    seo += 10 if 500 <= wc <= 3000 else (5 if wc > 3000 else 0)  # 長すぎも軽微減点
    seo += 10 if paras >= 5 else 0
    seo = min(100, seo)

    ux = 0
    ux += 20 if "width=device-width" in viewport else 0
    ux += 10 if 500 <= wc <= 2500 else 5
    ux += 10 if paras >= 6 else 5 if paras >=3 else 0
    # CTAっぽい文言が本文に含まれていそうか（簡易）
    text = page.get("text","").lower()
    cta_hits = sum(1 for kw in ["お問い合わせ","予約","資料請求","無料相談","contact","apply","signup","申し込み"] if kw in text)
    ux += 10 if cta_hits >= 2 else (5 if cta_hits == 1 else 0)
    ux += 10 if depth <= 6 else 5
    ux += 10 if unique_links >= 5 else 0
    ux = min(100, ux)

    return {
        "url": page["url"],
        "title": title,
        "word_count": wc,
        "images": total_img,
        "images_alt_filled": alt_ok,
        "images_alt_ratio": round(alt_ratio, 2),
        "internal_links": unique_links,
        "has_ldjson": has_ldjson,
        "has_viewport": ("width=device-width" in viewport),
        "has_meta_description": bool(md),
        "has_h1": bool(h1),
        "seo_score": seo,
        "ux_score": ux,
    }
