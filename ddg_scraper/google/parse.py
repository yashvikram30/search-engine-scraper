from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup

from ..models import SearchResult

ELLIPSES = ("…", "...")


def _is_google(url: str) -> bool:
    return urlparse(url).netloc.lower().endswith("google.com")


def _find_cite(a) -> str:
    """Displayed URL (breadcrumb) for a result link.

    Looks inside the link first, then climbs a few levels, but stops as soon as
    the block holds more than one result so it never borrows a neighbour's cite.
    """
    node = a
    for _ in range(6):
        if node is None or len(node.select("a h3")) > 1:
            break
        cite = node.select_one("cite")
        if cite:
            text = cite.get_text(" ", strip=True)
            if text:
                return text
        node = node.parent
    return ""


def _url_from_breadcrumb(text: str) -> str:
    """Rebuild a URL from Google's 'https://site.com › a › b' display text.

    Best effort: query strings are not shown, and a shortened breadcrumb (with
    an ellipsis) stops at the last full segment, so the URL may be a parent path.
    """
    parts = [p.strip() for p in re.split(r"\s*›\s*", text) if p.strip()]
    if not parts:
        return ""
    origin = parts[0]
    if not origin.startswith(("http://", "https://")):
        origin = "https://" + origin
    if "." not in urlparse(origin).netloc:
        return ""
    segments: list[str] = []
    for seg in parts[1:]:
        if any(e in seg for e in ELLIPSES):
            break
        segments.append(seg.replace(" ", "%20"))
    return origin.rstrip("/") + ("/" + "/".join(segments) if segments else "")


def _resolve_url(a, cite: str) -> str:
    href = (a.get("href") or "").strip()
    if href.startswith("/url?"):  # old style redirect link
        href = parse_qs(urlparse(href).query).get("q", [""])[0]
    if href.startswith(("http://", "https://")) and not _is_google(href):
        return href  # direct link
    # Google now serves /goto?url=<opaque token>, which holds no readable URL.
    return _url_from_breadcrumb(cite) if cite else ""


def parse_google_results(html: str) -> list[SearchResult]:
    """Extract organic results (title, url, snippet) from a Google results page.

    Titles are reliable. URLs are exact when Google gives direct links and are
    rebuilt from the displayed breadcrumb when it gives /goto links. Snippets
    are best effort, because Google's markup changes often.
    """
    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception:
        soup = BeautifulSoup(html, "html.parser")

    root = soup.select_one("#search") or soup
    results: list[SearchResult] = []
    seen: set[str] = set()

    for h3 in root.select("a h3"):
        a = h3.find_parent("a")
        if a is None:
            continue

        title = h3.get_text(strip=True)
        if not title:
            continue

        cite = _find_cite(a)
        url = _resolve_url(a, cite)
        if not url or _is_google(url) or url in seen:
            continue
        seen.add(url)

        # Walk up until the block holds more text than the title; the rest is the snippet.
        snippet = ""
        node = a
        for _ in range(5):
            node = node.parent
            if node is None or len(node.select("a h3")) > 1:
                break
            text = node.get_text(" ", strip=True)
            if len(text) > len(title) + 40:
                snippet = text.replace(title, "", 1)
                if cite:
                    snippet = snippet.replace(cite, "", 1)
                snippet = snippet.strip()[:300]
                break

        results.append(
            SearchResult(position=len(results) + 1, title=title, url=url, snippet=snippet)
        )

    return results