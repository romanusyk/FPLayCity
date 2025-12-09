# Overview
Fetcher and processor for Premier League "The Scout" stories. The system consists of three stages:
1. **Collection**: Fetches raw articles and assigns them to gameweeks.
2. **Extraction**: Uses LLM (Gemini) to extract structured facts about players.
3. **Validation**: Validates extracted facts against system data and stores confirmable insights.

Persists data using `JsonSnapshotStore` under `data/<season>/news/<gameweek>/<collection>/<layer>/<id>_<timestamp>.json`.

# Key Concepts
- **Snapshot-driven metadata**: `load_recent_news` never calls `bootstrap`. Instead it reads the latest `data/<season>/bootstrap_*.json` snapshot via `JsonSnapshotStore` to obtain `Gameweek` deadlines before fetching news.
- **Two-gameweek default window**: After the first page is fetched the script infers “next gameweek” from the newest article, sets `last_gw` to that value (when not provided), and sets `first_gw = max(1, last_gw - 1)`. Pagination stops once a page contains articles older than `first_gw`, but every fetched article on that page is still persisted.
- **Fail-loud persistence**: Articles with missing IDs or timestamps raise immediately. Every article is saved even if `--limit` is reached.
- **Timestamped storage**: Articles are stored using `JsonSnapshotStore` with timestamped filenames (`{id}_{timestamp}.json`). Only the latest snapshot is kept per article (older snapshots are automatically deleted).
- **Gameweek-specific listing**: `--list-known` and `--list-known-content` load articles for specific gameweeks and collections using `list_saved_news()` with required `gameweek` and `collection` parameters.
- **Strict Validation**: The validation layer fails loudly if an extracted player name does not match the system's player data for the given ID, ensuring no hallucinated data enters the facts layer.

# Components
- **Collection**:
  - `NewsCollectionConfig` in `src/fpl/loader/news/pl.py`: Declarative configuration for each source (API params + converter).
  - `fetch_news` in `src/fpl/loader/news/pl.py`: Calls the PL content API.
  - `load_recent_news` in `src/fpl/loader/news/pl.py`: Main pagination loop.
  - `list_saved_news` in `src/fpl/loader/news/pl.py`: Loads raw articles from disk.

- **Extraction**:
  - `process_article` in `src/fpl/loader/news/llm.py`: Sends raw article content to Gemini with player context to extract facts.
  - `main` in `src/fpl/loader/news/llm.py`: CLI for batch processing articles for a gameweek.

- **Validation**:
  - `validate.py`: Reads "gemini" layer output, verifies player identity, and converts to `NewsFact` objects.
  - `list_saved_facts`: Helper to load validated facts for models.

# Data/Control Flow
1. **Fetch**: `src/fpl/loader/news/pl.py` downloads raw news to `.../raw/`.
2. **Extract**: `src/fpl/loader/news/llm.py` reads `raw`, queries LLM, saves to `.../gemini/`.
3. **Validate**: `src/fpl/loader/news/validate.py` reads `gemini`, validates names, saves to `.../facts/`.
4. **Consume**: Models use `Query.news_facts_by_player` or `Query.news_facts_by_gameweek` to access validated facts.

# Public API

## CLI
- **Fetch**: `uv run python -m src.fpl.loader.news.pl fpl_scout --last-gw M [--first-gw N ...]`
- **List Raw**: `uv run python -m src.fpl.loader.news.pl fpl_scout --last-gw M --list-known`
- **Extract**: `uv run python -m src.fpl.loader.news.llm fpl_scout --last-gw M [--first-gw N]`
- **Validate**: `uv run python -m src.fpl.loader.news.validate fpl_scout --last-gw M [--first-gw N]`

## Programmatic Helpers
- `async fetch_news(...)`
- `async load_recent_news(...)`
- `list_saved_news(...)`
- `list_saved_facts(season, gameweek, collection) -> list[NewsFact]`

# Key Paths
- Implementations: `src/fpl/loader/news/`
- Data root: `data/2025-2026/news/<gameweek>/<collection>/<layer>/<id>.json`
  - Layers: `raw`, `gemini`, `facts`

# Related Docs
- News north star (data model, storage hierarchy) — `docs/news_ns.md`
- Loader overview (API snapshots + registries) — `src/fpl/loader/README.md`
