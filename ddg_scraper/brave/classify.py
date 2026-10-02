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

def classify_brave(http_status: int, html: str, parsed_count: int) -> SearchStatus:
    if http_status >= 500:
        return "error"
    if 300 <= http_status < 400:
        return "blocked"
    if http_status in (403, 429):
        return "blocked"

    if BOT_PATTERNS.search(html):
        return "blocked"

    if parsed_count > 0:
        return "ok"

    if NO_RESULTS_PATTERNS.search(html):
        return "empty"

    return "blocked"
