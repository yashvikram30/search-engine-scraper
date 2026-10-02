from __future__ import annotations

import re

from ..models import SearchStatus

CHALLENGE_FORM = re.compile(r"""id=["']challenge-form["']""")  # either quote style
BOT_WORDS = re.compile(r"anomaly|unusual traffic|captcha", re.I)
NO_RESULTS = re.compile(r"no results", re.I)

def classify(http_status: int, html: str, parsed_count: int) -> SearchStatus:
    """Never trust a 200. Look at the content."""
    if http_status >= 500:
        return "error"
    if 300 <= http_status < 400:
        return "blocked"  # redirects are not followed
    if http_status in (403, 429):
        return "blocked"
    if CHALLENGE_FORM.search(html):
        return "blocked"

    if parsed_count > 0:
        return "ok"

    if BOT_WORDS.search(html):
        return "blocked"
    if NO_RESULTS.search(html):
        return "empty"

    # 200 with no results and no explanation, treat as blocked and retry
    return "blocked"
