from __future__ import annotations

import re

from ..models import SearchStatus

CHALLENGE_FORM = re.compile(r"""id=["']challenge-form["']""")  # either quote style
BOT_WORDS = re.compile(r"anomaly|unusual traffic|captcha", re.I)
NO_RESULTS = re.compile(r"no results", re.I)

def explain_ddg_response(http_status: int, html: str, parsed_count: int) -> tuple[SearchStatus, str]:
    """Classify the response and provide an explanation of why it was or wasn't scrapable."""
    if http_status >= 500:
        return "error", f"Server error (HTTP {http_status})"
    if 300 <= http_status < 400:
        return "blocked", f"Redirect detected (HTTP {http_status}); redirects indicate anomaly mitigation"
    if http_status in (403, 429):
        return "blocked", f"Rate limited or forbidden (HTTP {http_status})"
    if CHALLENGE_FORM.search(html):
        return "blocked", f"Anomaly challenge form detected (id='challenge-form') in HTTP {http_status} response"

    if parsed_count > 0:
        return "ok", f"Successfully parsed {parsed_count} results (HTTP {http_status})"

    bot_match = BOT_WORDS.search(html)
    if bot_match:
        return "blocked", f"Bot detection keyword ('{bot_match.group(0)}') detected in HTTP {http_status} body"
    if NO_RESULTS.search(html):
        return "empty", f"Confirmed zero search results ('no results' text in HTML)"

    # 200 with no results and no explanation, treat as blocked and retry
    return "blocked", f"HTTP {http_status} returned 0 results without 'no results' confirmation (possible silent rate limit or unknown DOM)"


def classify(http_status: int, html: str, parsed_count: int) -> SearchStatus:
    """Never trust a 200. Look at the content."""
    status, _ = explain_ddg_response(http_status, html, parsed_count)
    return status
