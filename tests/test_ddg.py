from pathlib import Path

from ddg_scraper.ddg.classify import classify
from ddg_scraper.ddg.parse import parse_results

FIXTURES = Path(__file__).parent / "fixtures"
OK = (FIXTURES / "ddg_ok.html").read_text(encoding="utf8")
BLOCKED = (FIXTURES / "ddg_blocked.html").read_text(encoding="utf8")

def test_parses_results_from_a_saved_page():
    results = parse_results(OK)
    assert len(results) > 5
    assert results[0].url.startswith(("http://", "https://"))
    assert len(results[0].title) > 0

def test_skips_ads_and_internal_links():
    urls = [r.url for r in parse_results(OK)]
    assert not any("duckduckgo.com" in u for u in urls)
    assert "Sponsored thing" not in [r.title for r in parse_results(OK)]

def test_good_page_is_ok():
    assert classify(200, OK, len(parse_results(OK))) == "ok"

def test_challenge_page_is_blocked():
    assert classify(200, BLOCKED, 0) == "blocked"

def test_challenge_form_with_single_quotes_is_blocked():
    assert classify(200, "<form id='challenge-form'></form>", 0) == "blocked"

def test_429_and_redirects_are_blocked():
    assert classify(429, "", 0) == "blocked"
    assert classify(303, "", 0) == "blocked"

def test_5xx_is_error():
    assert classify(503, "", 0) == "error"

def test_no_results_page_is_empty():
    assert classify(200, "<div>No results.</div>", 0) == "empty"

def test_extract_next_page_payload():
    from ddg_scraper.ddg.parse import extract_next_page_payload
    payload = extract_next_page_payload(OK)
    assert payload is not None
    assert payload["vqd"] == "test-token-12345"
    assert payload["s"] == "6"
    assert payload["dc"] == "7"
    assert payload["kl"] == "us-en"

def test_extract_next_page_payload_returns_none_when_no_form():
    from ddg_scraper.ddg.parse import extract_next_page_payload
    assert extract_next_page_payload("<div>No forms here</div>") is None

