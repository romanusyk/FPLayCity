# Overview
Utilities to fetch and persist Fantasy Premier League news articles authored by “The Scout” from the Premier League content API. This module paginates the news feed, stores new articles under `data/<season>/news/`, and provides helpers to list saved articles with optional date filtering.

# Key Concepts
- **Source**: Premier League content API, filtered to series `fantasy` and content creator `The-Scout`.
- **Pagination and idempotence**: Iterates in pages and stops when encountering the first already-known article (by `id` and `date`), avoiding duplicate writes.
- **Persistence layout**: Each article is written as JSON to `data/2025-2026/news/<id>.json` including URL, date, title, summary, body, and `lastUpdated`.

# Components
- `fetch_news` in `src/fpl/loader/news/pl.py`: Fetch one page of detailed news items.
- `load_recent_news` in `src/fpl/loader/news/pl.py`: Paginate and persist new items until the first known article (or an explicit limit).
- `read_known_news` in `src/fpl/loader/news/pl.py`: Read saved article metadata (omits `body`) for quick listing.
- `read_known_news_content` in `src/fpl/loader/news/pl.py`: Read saved articles including full HTML `body`.
- `main` in `src/fpl/loader/news/pl.py`: CLI entry point for fetching or listing.

# Data/Control Flow
1. `load_recent_news` calls `fetch_news(offset, limit=page_size)`.
2. For each returned item:
   - If already known with the same `date`, stop pagination (assumes newer pages were already saved).
   - Otherwise extract fields and write `data/<season>/news/<id>.json`.
3. Continue advancing `offset` until a known item is encountered, items are exhausted, or `--limit` is reached.

# Public API
- `fetch_news(client, offset=0, limit=10) -> dict`: Returns a page payload (`pageInfo`, `content` list).
- `load_recent_news(client, page_size=10, sleep_sec=0.0, limit=None) -> list[int]`: Saves new items, returns the list of saved IDs.
- `read_known_news() -> list[dict]`: Reads saved items without `body`, sorted by `date` desc.
- `read_known_news_content() -> list[dict]`: Reads saved items with `body`, sorted by `date` desc.

CLI usage (module path):

```bash
python -m src.fpl.loader.news.pl --page-size 10 --limit 20
python -m src.fpl.loader.news.pl --list-known --min-days 7
python -m src.fpl.loader.news.pl --list-known-content --output-json data/2025-2026/dumps/news.json
```

# Key Paths
- Module: `src/fpl/loader/news/pl.py`
- Package export: `src/fpl/loader/news/__init__.py`
- Data directory (current season): `data/2025-2026/news/`

# Related Docs
- Documentation standards and structure in `docs/metadoc.md` — top‑down style, concise writing, explicit symbol+path references.


