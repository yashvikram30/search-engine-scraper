# DuckDuckGo Scraper: Findings, Architecture and Rollout Plan

**Date**: October 2, 2026
**Status**: Verified and Operational (25 of 25 Tests Passing)
**Package**: ddg-scraper

## 1. Executive Summary

This project implements a modular, proxy-aware web scraper for DuckDuckGo that returns structured web results (position, title, url, snippet) for any query, region, and page depth behind an interchangeable engine protocol (SearchEngine).

The initial implementation was bootstrapped against synthetic fixtures modeled on SearXNG. In subsequent phases, the engine was tested and verified against live DuckDuckGo endpoints, reverse-engineering DuckDuckGo's live anti-bot defenses and multi-page pagination mechanics.

## 2. Live DuckDuckGo Discoveries and Research Findings

### A. The GET vs. POST Divergence
The project specifications noted conflicting source documentation regarding whether DuckDuckGo's /html/ endpoint prefers GET or POST. Live testing confirmed:

* GET Method:
  * Cloud / Datacenter IP: Returns HTTP 202 with an anomaly challenge page (//duckduckgo.com/anomaly.js and challenge-form).
  * Clean Residential / Browser IP: Frequently succeeds without challenge due to clean IP reputation and browser TLS fingerprints.
  * Verdict: High risk on automated datacenter proxies.

* POST Method:
  * Cloud / Datacenter IP: Returns HTTP 200 with full HTML search results (when accompanied by browser Sec-Fetch headers).
  * Clean Residential / Browser IP: Returns HTTP 200 with full HTML search results.
  * Verdict: Recommended standard for automation.

### B. Link Structure
* Direct examination of the live HTML confirmed that search result links are direct URLs (such as https://www.python.org/) rather than wrapped redirect links (uddg=...).
* The parser (_unwrap) handles both direct and redirect formats defensively.

### C. Required Browser Headers
DuckDuckGo checks navigation metadata. Omitting Sec-Fetch headers increases the challenge rate even on POST requests. The engine sends:
```python
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
}
```

### D. Single-IP Block Limits and Cooldown
* Sending 10 to 20 unpaced requests from a single IP triggers DuckDuckGo's anomaly rate limiter.
* The block manifests as HTTP 202 (CAPTCHA challenge) or HTTP 429.
* Cooldown duration: DuckDuckGo blocks are temporary, typically expiring after 15 to 60 minutes of zero traffic.

## 3. Reverse-Engineered Multi-Page Pagination

### The Challenge
DuckDuckGo does not support traditional URL offset pagination (?page=2 or ?s=30). Attempting to guess parameters or jump to page 2 directly triggers an immediate anomaly block.

### The Solution: Dynamic Token Affinity
Every valid results page includes a hidden HTML form for the Next button:
```html
<form action="/html/" method="post">
  <input type="submit" value="Next" />
  <input type="hidden" name="q" value="python tutorial" />
  <input type="hidden" name="s" value="10" />
  <input type="hidden" name="dc" value="11" />
  <input type="hidden" name="vqd" value="4-31101848122989890533371569577319053479" />
  <input type="hidden" name="api" value="d.js" />
  <input type="hidden" name="v" value="l" />
  <input type="hidden" name="o" value="json" />
  <input type="hidden" name="kl" value="us-en" />
  <input type="hidden" name="nextParams" value="" />
</form>
```

### Live Pagination Metrics
* Page 1: 10 results returned (HTTP 200). Next form extracted: s=10, dc=11, vqd=4-3110...
* Page 2: 15 results returned (HTTP 200). Next form extracted: s=25, dc=28, vqd=4-3340...
* Page 3: 15 results returned (HTTP 200).

The scraper implements extract_next_page_payload() in ddg_scraper/ddg/parse.py to read all hidden inputs from the previous page and submits them verbatim via POST, ensuring the dynamic session token (vqd) is never broken.

Flow:
1. Client requests pages = 3
2. Fetch Page 1 via POST (Initial Payload) -> yields 10 results and extracts vqd, s=10
3. Fetch Page 2 via POST (Chained Payload) -> yields 15 results and extracts updated vqd, s=25
4. Fetch Page 3 via POST (Chained Payload) -> yields 15 results
5. Combine into single SearchResponse with contiguous positions 1 through 40

## 4. Architecture and Implemented Components

```text
ddg-scraper/
├── ddg_scraper/
│   ├── models.py        # SearchQuery (with max_pages), SearchResult, SearchResponse, SearchEngine protocol
│   ├── net.py           # ProxyPool (per-proxy pacing and quarantine), backoff, TtlCache
│   ├── trial.py         # Batch runner reporting latency and status distribution
│   └── ddg/
│       ├── parse.py     # HTML parser, ad stripper, extract_next_page_payload()
│       ├── classify.py  # Response status classifier (ok, empty, blocked, error)
│       └── engine.py    # DuckDuckGoEngine with multi-page loop, caching, and backoff
├── tests/
│   ├── fixtures/
│   │   ├── ddg_ok.html      # Valid page fixture with results and Next form
│   │   └── ddg_blocked.html # Challenge form fixture
│   ├── test_ddg.py      # Parser, classifier, and Next form tests (10 tests)
│   ├── test_engine.py   # MockTransport engine and multi-page tests (11 tests)
│   └── test_pool.py     # Pacing, rotation, and quarantine tests (4 tests)
├── server.py            # Local REST API server exposing /search and /health
├── ddg-requests.http    # VS Code REST Client testing script with variables
├── ddg_scraper_postman_collection.json # Ready-to-import Postman Collection
├── queries.txt          # Sample query batch for trial evaluation
└── requirements.txt     # httpx, beautifulsoup4, lxml, pytest
```

## 5. Verification Status

### Unit and Mock Test Suite
All 25 tests pass cleanly:
```text
tests/test_ddg.py ..........                                             [ 40%]
tests/test_engine.py ...........                                         [ 84%]
tests/test_pool.py ....                                                  [100%]

============================== 25 passed in 6.25s ==============================
```

### Live Multi-Page Test Run
Executed against live DuckDuckGo via server.py:
```text
Endpoint: GET http://localhost:8080/search?q=python+asyncio+tutorial&pages=2&method=POST
Status: 200 OK
Pages Requested: 2
Pages Scraped: 2
Total Results: 24
First Result: Position 1 -> Python's asyncio: A Hands-On Walkthrough - Real Python
Last Result:  Position 24 -> Hands-On Python 3 Concurrency With the asyncio Module - Real Python
```

## 6. Residential Proxies: Why the Pool Remains Essential

Even when purchasing residential proxies, ProxyPool is necessary for three reasons:

1. Session Affinity (Token Retention): Pure rotating proxies change the exit IP on every request. If Page 1 is on IP A and Page 2 is on IP B, DuckDuckGo rejects the vqd token. The pool manages sticky sessions per query so all pages of a search stay on the same IP.
2. Pre-Burnt IP Quarantining: Shared residential pools often hand out IPs already flagged by other scrapers. When a block is received, ProxyPool.report(entry, "blocked") isolates the bad IP and acquires a fresh one for the retry.
3. Pacing and Cost Control: Enforces MIN_INTERVAL_S to protect expensive per-GB residential proxy bandwidth.

## 7. How We Will Proceed (Rollout Roadmap)

### Next Steps:

1. Step 1: Configure Sticky Residential Proxies
   * Populate PROXY_URLS with sticky residential endpoints (such as http://user-session-1:pass@gate:port, http://user-session-2:...).
   * Set COOLDOWN_S=1800 (30 minutes) and MIN_INTERVAL_S=1.5.

2. Step 2: Run the 1,000-Query Trial Batch
   * Run ddg_scraper.trial across 1,000 queries in queries.txt.
   * Decision Gate Criteria:
     * Success rate (ok + empty) at or above 95 percent.
     * p95 latency under acceptable threshold.
     * Cost per 1,000 queries within budget.

3. Step 3: Implement Fallback Search Engines
   * Implement BraveEngine and BingEngine adhering to the same SearchEngine protocol.
   * When DuckDuckGo proxies enter cooldown or encounter persistent anomalies, automatically fallback to Brave.

## 8. Sample Search Results Obtained from Live Scraper

Below are real sample outputs captured directly from live scraping runs.

### Sample A: Single-Page Live Query ("python programming language", Page 1)

```json
{
  "engine": "duckduckgo",
  "query": "python programming language",
  "region": "us-en",
  "status": "ok",
  "attempts": 1,
  "latency_ms": 1042,
  "results_count": 10,
  "results": [
    {
      "position": 1,
      "title": "Welcome to Python.org",
      "url": "https://www.python.org/",
      "snippet": "The mission of the Python Software Foundation is to promote, protect, and advance the Python programming language, and to support and facilitate the growth of a diverse and international community of Python programmers."
    },
    {
      "position": 2,
      "title": "Download Python | Python.org",
      "url": "https://www.python.org/downloads/",
      "snippet": "The official home of the Python Programming Language."
    },
    {
      "position": 3,
      "title": "Python (programming language) - Wikipedia",
      "url": "https://en.wikipedia.org/wiki/Python_(programming_language)",
      "snippet": "Python supports multiple programming paradigms but with an emphasis on object-oriented programming and dynamic typing. Guido van Rossum began working on Python in the late 1980s as a successor to the ABC programming language."
    },
    {
      "position": 4,
      "title": "Python Tutorial - W3Schools",
      "url": "https://www.w3schools.com/python/",
      "snippet": "Python is a popular programming language. Python can be used on a server to create web applications. Start learning Python now."
    },
    {
      "position": 5,
      "title": "Python For Beginners | Python.org",
      "url": "https://www.python.org/about/gettingstarted/",
      "snippet": "Learning. Before getting started, you may want to find out which IDEs and text editors are tailored to make Python editing easy, browse the list of introductory books, or look at code samples."
    }
  ]
}
```

### Sample B: Multi-Page Chained Query ("python asyncio tutorial", 2 Pages Chained)

Demonstrates how Page 1 transitions directly into Page 2 using the dynamic vqd token, maintaining unbroken result positions (1 to 24):

* Page 1 Results:
  * Position 1: Python's asyncio: A Hands-On Walkthrough (https://realpython.com/async-io-python/)
  * Position 2: asyncio - Asynchronous I/O - Python 3 Documentation (https://docs.python.org/3/library/asyncio.html)
  * Position 3: Async IO in Python: A Complete Walkthrough (https://towardsdatascience.com/async-io-in-python-tutorial)
  * Positions 4 through 10: Retrieved and verified from Page 1

* Page 2 Results (Chained via dynamic vqd token):
  * Position 11: Asyncio Tutorial: A Guide to Concurrency in Python (https://www.datacamp.com/tutorial/asyncio-tutorial-python)
  * Position 12: Asynchronous Programming in Python with asyncio (https://www.twilio.com/blog/asynchronous-programming-python-asyncio)
  * Positions 13 through 23: Retrieved and verified from Page 2
  * Position 24: Hands-On Python 3 Concurrency With the asyncio Module (https://realpython.com/hands-on-python-3-concurrency-with-asyncio)

Verification Notes:
* Result links are direct URLs with no redirect wrapping.
* All ad nodes and internal DDG links are stripped.
* Multi-page positions remain continuous across chained requests.
