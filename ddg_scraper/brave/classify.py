from __future__ import annotations

import re
from ..models import SearchStatus

BOT_PATTERNS = re.compile(
    r"needs JavaScript to function|flagged as being suspicious|schedule a captcha|turnstile|cf-challenge",
    re.I,
)
NO_RESULTS_PATTERNS = re.compile(
    r"no results found|try different keywords|didn't match any documents",
    re.I,
)

def explain_brave_response(http_status: int, html: str, parsed_count: int) -> tuple[SearchStatus, str]:
    if http_status >= 500:
        return "error", f"Server error (HTTP {http_status})"
    if 300 <= http_status < 400:
        return "blocked", f"Redirect detected (HTTP {http_status})"
    if http_status in (403, 429):
        return "blocked", f"Rate limited or forbidden (HTTP {http_status})"
    
    if parsed_count > 0:
        return "ok", f"Successfully parsed {parsed_count} results (HTTP {http_status})"

    bot_match = BOT_PATTERNS.search(html)
    if bot_match:
        return "blocked", f"Bot defense pattern ('{bot_match.group(0)}') detected in HTTP {http_status} body"


    if NO_RESULTS_PATTERNS.search(html):
        return "empty", f"Confirmed zero results ('no results found' in HTML)"

    return "blocked", f"HTTP {http_status} returned 0 results without empty confirmation (Cloudflare challenge or layout change)"


def classify_brave(http_status: int, html: str, parsed_count: int) -> SearchStatus:
    status, _ = explain_brave_response(http_status, html, parsed_count)
    return status
