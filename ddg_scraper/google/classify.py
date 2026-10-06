from __future__ import annotations

import re

from ..models import SearchStatus

# Only phrases that appear on Google's block page, not in normal page scripts.
BLOCK_PATTERNS = re.compile(r"unusual traffic|captcha-form|g-recaptcha", re.I)
NO_RESULTS_PATTERNS = re.compile(r"did not match any documents|No results found for", re.I)
RESULTS_MARKUP = re.compile(r"id=[\"']search[\"']", re.I)


def explain_google_response(
    http_status: int, url: str, html: str, parsed_count: int
) -> tuple[SearchStatus, str]:
    """Return (status, human readable reason) for a Google results page."""
    if parsed_count > 0:
        return "ok", f"Parsed {parsed_count} results (HTTP {http_status})"
    if "/sorry/" in url or "consent." in url:
        return "blocked", f"Redirected to a block or consent page ({url[:80]})"
    if http_status in (403, 429):
        return "blocked", f"Rate limited or forbidden (HTTP {http_status})"
    if http_status >= 500:
        return "error", f"Server error (HTTP {http_status})"
    if BLOCK_PATTERNS.search(html):
        return "blocked", "Bot or CAPTCHA pattern found in page"
    if NO_RESULTS_PATTERNS.search(html):
        return "empty", "Confirmed zero results"
    if RESULTS_MARKUP.search(html) and "<h3" in html:
        # Results are on the page but the parser could not read them. Not a block.
        return "error", "Results page loaded but nothing was parsed (link or layout format changed)"
    return "blocked", "0 results with no explanation (layout change or JS shell)"


def classify_google(http_status: int, url: str, html: str, parsed_count: int) -> SearchStatus:
    status, _ = explain_google_response(http_status, url, html, parsed_count)
    return status