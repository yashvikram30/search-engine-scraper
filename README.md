# Search Scraper API (DuckDuckGo, Brave HTTP & Brave Playwright)

Multi-engine web scraper returning structured search results (position, title, URL, snippet) for a query and region, behind a common engine protocol (`SearchEngine`) with proxy pooling, rate limiting, and block detection.

## Architecture

1. **`SearchEngine` interface (`ddg_scraper.models`)**: Protocol implemented by `DuckDuckGoEngine`, `BraveEngine` (HTTP), and `BravePlaywrightEngine` (headless browser).
2. **Proxy pool & pacing (`ddg_scraper.net`)**: Paces per-proxy requests, rotates on retries, and quarantines blocked proxies with cooldown.
3. **DuckDuckGo Engine (`ddg_scraper.ddg`)**:
   - `parse.py`: Extracts position, title, URL, snippet, filters ads, and extracts pagination tokens (`vqd`, `s`, `dc`).
   - `classify.py`: Classifies DDG responses (`ok`, `empty`, `blocked`, `error`).
   - `engine.py`: Chained multi-page pagination with session tokens.
4. **Brave Engine - HTTP (`ddg_scraper.brave.engine`)**:
   - Lightweight `httpx` based scraper using offset pagination.
5. **Brave Playwright Engine (`ddg_scraper.brave.playwright_engine`)**:
   - Headless Chromium browser automation via Playwright with stealth scripts (evading `navigator.webdriver` and browser automation flags).
   - Solves client-side JavaScript execution and hydration barriers on Brave Search.
6. **Local API Server (`server.py`)**: Multi-threaded HTTP server routing requests to DDG, Brave HTTP, or Brave Playwright via query parameters or JSON payload.

## Setup & Running

Install dependencies:
```bash
pip install -r requirements.txt
playwright install chromium
```

> [!NOTE]
> On fresh Linux / WSL environments, Chromium requires standard OS libraries. Run:
> ```bash
> sudo playwright install-deps
> ```

Run test suite:
```bash
python -m pytest
```

Start the local server:
```bash
python server.py
```

## API Usage

### 1. DuckDuckGo Search
- **GET**: `http://localhost:8080/search?q=machine+learning&pages=3&engine=ddg&method=POST`
- **POST**:
  ```json
  {
    "query": "python asyncio tutorial",
    "pages": 3,
    "engine": "ddg",
    "method": "POST"
  }
  ```

### 2. Brave Search (HTTP)
- **GET**: `http://localhost:8080/search?q=rust+concurrency&pages=3&engine=brave`
- **POST**:
  ```json
  {
    "query": "golang microservices",
    "pages": 3,
    "engine": "brave"
  }
  ```

### 3. Brave Search (Playwright Headless Browser)
- **GET**: `http://localhost:8080/search?q=kubernetes+deployments&pages=2&engine=brave-playwright`
- **POST**:
  ```json
  {
    "query": "distributed systems consensus",
    "pages": 2,
    "engine": "brave-playwright"
  }
  ```

### 4. Server Health
- **GET**: `http://localhost:8080/health`

## Testing via Postman & REST Client

- **Postman Collection**: Import `ddg_scraper_postman_collection.json` (organized into DuckDuckGo, Brave HTTP, Brave Playwright, Health, and Direct endpoints).
- **VS Code REST Client**: Open and run requests directly from `ddg-requests.http`.
