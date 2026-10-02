from __future__ import annotations

from urllib.parse import parse_qs, urljoin, urlparse

from bs4 import BeautifulSoup

from ..models import SearchResult

# class DDG puts on sponsored results, written in two parts to avoid typos
AD_CLASS = "result-" + "-ad"

def _unwrap(href: str) -> str | None:
    """DDG may wrap result links in a redirect (uddg param). Return the real URL."""
    try:
        absolute = urljoin("https://duckduckgo.com", href)
        target = parse_qs(urlparse(absolute).query).get("uddg")
        final = target[0] if target else absolute
        parsed = urlparse(final)
        if parsed.scheme not in ("http", "https"):
            return None
        if (parsed.hostname or "").endswith("duckduckgo.com"):
            return None
        return final
    except ValueError:
        return None

def parse_results(html: str) -> list[SearchResult]:
    soup = BeautifulSoup(html, "lxml")
    out: list[SearchResult] = []

    for node in soup.select(".result"):
        if AD_CLASS in (node.get("class") or []):
            continue  # skip ads

        link = node.select_one("a.result__a")
        if link is None:
            continue
        href = link.get("href")
        if not isinstance(href, str) or not href:
            continue

        url = _unwrap(href)
        if url is None:
            continue

        snippet = node.select_one(".result__snippet")
        out.append(
            SearchResult(
                position=len(out) + 1,
                title=link.get_text(strip=True),
                url=url,
                snippet=snippet.get_text(strip=True) if snippet else "",
            )
        )

    return out

def extract_next_page_payload(html: str) -> dict[str, str] | None:
    """Extracts all hidden inputs from the 'Next' pagination form in DDG HTML."""
    soup = BeautifulSoup(html, "lxml")
    for form in soup.find_all("form"):
        if any(inp.get("value") == "Next" for inp in form.find_all("input")):
            payload: dict[str, str] = {}
            for inp in form.find_all("input"):
                name = inp.get("name")
                if name:
                    payload[name] = inp.get("value", "")
            return payload
    return None

