# Overview
Fetcher for Premier League "The Scout" stories. The loader assigns each article to the correct upcoming gameweek, persists the raw payload using `JsonSnapshotStore` under `data/<season>/news/<gameweek>/<collection>/raw/<id>_<timestamp>.json`, and can list previously stored articles for specific gameweeks and collections.

# Key Concepts
- **Snapshot-driven metadata**: `load_recent_news` never calls `bootstrap`. Instead it reads the latest `data/<season>/bootstrap_*.json` snapshot via `JsonSnapshotStore` to obtain `Gameweek` deadlines before fetching news.
- **Two-gameweek default window**: After the first page is fetched the script infers “next gameweek” from the newest article, sets `last_gw` to that value (when not provided), and sets `first_gw = max(1, last_gw - 1)`. Pagination stops once a page contains articles older than `first_gw`, but every fetched article on that page is still persisted.
- **Fail-loud persistence**: Articles with missing IDs or timestamps raise immediately. Every article is saved even if `--limit` is reached.
- **Timestamped storage**: Articles are stored using `JsonSnapshotStore` with timestamped filenames (`{id}_{timestamp}.json`). Only the latest snapshot is kept per article (older snapshots are automatically deleted).
- **Gameweek-specific listing**: `--list-known` and `--list-known-content` load articles for specific gameweeks and collections using `list_saved_news()` with required `gameweek` and `collection` parameters.

# Components
- `NewsCollectionConfig` in `src/fpl/loader/news/pl.py`: Declarative configuration for each source (API params + converter).
- `fetch_news` in `src/fpl/loader/news/pl.py`: Calls the PL content API with offset/limit and logs page metadata.
- `load_recent_news` in `src/fpl/loader/news/pl.py`: Main pagination loop. Loads gameweek deadlines from the bootstrap snapshot, converts API records via `news_json_to_model` in `src/fpl/loader/convert/news.py`, persists per-article files using `JsonSnapshotStore`, enforces the two-gameweek window, and honors `--limit` only after the current page finishes saving.
- `list_saved_news` in `src/fpl/loader/news/pl.py`: Loads articles from disk for a specific gameweek and collection using `JsonSnapshotStore.load_latest()` for each article, and returns JSON-ready dicts (optionally stripping `body` before printing).
- CLI `main` in `src/fpl/loader/news/pl.py`: Argument parser powering both the fetch workflow and the simple listing mode.

# Data/Control Flow
1. `main` resolves the collection config (currently only `fpl_scout`) and either lists saved news or triggers fetch mode.
2. Fetch mode:
   - Load the `bootstrap` snapshot through `JsonSnapshotStore` to build `Gameweek` objects (no network call).
   - For each offset: `fetch_news` → convert each article using `news_json_to_model`, assign a gameweek via `_assign_gameweek`, and persist the raw article JSON using `JsonSnapshotStore` under `data/<season>/news/<gw>/<collection>/raw/<id>_<timestamp>.json`.
   - After the first page derive default `first_gw`/`last_gw` if the user omitted them; stop fetching once a page contains articles older than `first_gw` or once `--limit` is met (after saving the page).
3. Listing mode loads articles for specific gameweeks and collections using `list_saved_news()` with `gameweek` and `collection` parameters, and prints compact summaries (optionally including body HTML).

# Public API
- CLI fetch: `uv run python -m src.fpl.loader.news.pl fpl_scout --last-gw M [--first-gw N --page-size K --limit L --sleep-sec S]`
- CLI list without body: `uv run python -m src.fpl.loader.news.pl fpl_scout --last-gw M --list-known [--first-gw N]`
- CLI list with body: `uv run python -m src.fpl.loader.news.pl fpl_scout --last-gw M --list-known-content [--first-gw N]`
- Programmatic helpers:
  - `async fetch_news(client, config, offset, limit) -> dict`
  - `async load_recent_news(client, config, gameweeks, season, page_size, sleep_sec, limit, first_gw, last_gw) -> list[int]`
  - `list_saved_news(collection, gameweek, include_body, season) -> list[dict]`

# Key Paths
- Loader implementation: `src/fpl/loader/news/pl.py`
- Convert helpers: `src/fpl/loader/convert/news.py`
- Data root for the current season: `data/2025-2026/news/<gameweek>/<collection>/raw/<id>_<timestamp>.json`

# Related Docs
- News north star (data model, storage hierarchy) — `docs/news_ns.md`
- Loader overview (API snapshots + registries) — `src/fpl/loader/README.md`
- Convert module overview — `src/fpl/loader/convert/README.md`
