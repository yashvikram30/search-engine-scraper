from __future__ import annotations

from urllib.parse import urlparse
from bs4 import BeautifulSoup

from ..models import SearchResult

EXCLUDED_DOMAINS = {"search.brave.com", "brave.com", "community.brave.com"}

def _is_ad_element(el) -> bool:
    classes = el.get("class", [])
    if isinstance(classes, str):
        classes = classes.split()
    tokens = set(" ".join(classes).lower().split())
    return bool(tokens & {"ad", "ads", "sponsored"})

def parse_brave_results(html: str) -> list[SearchResult]:
    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception:
        soup = BeautifulSoup(html, "html.parser")

    results: list[SearchResult] = []
    seen_urls: set[str] = set()

    # Locate snippet containers
    containers = soup.select("div.snippet[data-type='search'], div.snippet")

    for c in containers:
        if _is_ad_element(c):
            continue

        link_el = c.select_one("a[href]")
        if not link_el:
            continue

        raw_url = link_el.get("href", "").strip()
        if not raw_url.startswith(("http://", "https://")):
            continue

        parsed_netloc = urlparse(raw_url).netloc.lower()
        if parsed_netloc in EXCLUDED_DOMAINS:
            continue

        if raw_url in seen_urls:
            continue
        seen_urls.add(raw_url)

        title_el = c.select_one(".title, a .title, div.title, h2")
        title = title_el.get_text(strip=True) if title_el else ""
        if not title:
            continue

        desc_el = c.select_one(".snippet-description, .generic-desc, .snippet-content, div.content, p")
        snippet = desc_el.get_text(strip=True) if desc_el else ""

        results.append(
            SearchResult(
                position=len(results) + 1,
                title=title,
                url=raw_url,
                snippet=snippet,
            )
        )

    return results
