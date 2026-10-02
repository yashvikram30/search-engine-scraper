# Search Scraper API (DuckDuckGo & Brave)

Multi-engine web scraper returning structured search results (position, title, URL, snippet) for a query and region, behind a common engine protocol (`SearchEngine`) with proxy pooling, rate limiting, and block detection.

## Architecture

1. **`SearchEngine` interface (`ddg_scraper.models`)**: Protocol implemented by `DuckDuckGoEngine` and `BraveEngine`.
2. **Proxy pool & pacing (`ddg_scraper.net`)**: Paces per-proxy requests, rotates on retries, and quarantines blocked proxies with cooldown.
3. **DuckDuckGo Engine (`ddg_scraper.ddg`)**:
   - `parse.py`: Extracts position, title, URL, snippet, filters ads, and extracts pagination tokens (`vqd`, `s`, `dc`).
   - `classify.py`: Classifies DDG responses (`ok`, `empty`, `blocked`, `error`).
   - `engine.py`: Chained multi-page pagination with session tokens.
4. **Brave Engine (`ddg_scraper.brave`)**:
   - `parse.py`: Extracts structured results, removes ads and internal feedback links.
   - `classify.py`: Detects Cloudflare/Brave bot challenges (`429`, `turnstile`, captcha) and empty responses.
   - `engine.py`: Offset-based multi-page pagination (`offset=0, 1, 2...`).
5. **Local API Server (`server.py`)**: Multi-threaded HTTP server routing requests to DDG or Brave via query parameters or JSON payload.

## Setup & Running

Install dependencies:
```bash
pip install -r requirements.txt
```

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

### 2. Brave Search
- **GET**: `http://localhost:8080/search?q=rust+concurrency&pages=3&engine=brave`
- **POST**:
  ```json
  {
    "query": "golang microservices",
    "pages": 3,
    "engine": "brave"
  }
  ```

### 3. Server Health
- **GET**: `http://localhost:8080/health`

## Testing via Postman & REST Client

- **Postman Collection**: Import `ddg_scraper_postman_collection.json` (organized into DuckDuckGo, Brave, Health, and Direct endpoints).
- **VS Code REST Client**: Open and run requests directly from `ddg-requests.http`.
