# Overview
Fetcher and processor for Premier League "The Scout" stories. The system consists of three stages:
1. **Collection**: Fetches raw articles and assigns them to gameweeks.
2. **Extraction**: Uses an LLM via `claude -p` to extract structured facts about players.
3. **Validation**: Validates extracted facts against system data and stores confirmable insights.

Persists data using `JsonSnapshotStore` under `data/<season>/news/<gameweek>/<collection>/<layer>/<id>_<timestamp>.json`.

# Key Concepts
- **Snapshot-driven metadata**: `load_recent_news` never calls `bootstrap`. Instead it reads the latest `data/<season>/bootstrap_*.json` snapshot via `JsonSnapshotStore` to obtain `Gameweek` deadlines before fetching news.
- **Two-gameweek default window**: After the first page is fetched the script infers “next gameweek” from the newest article, sets `last_gw` to that value (when not provided), and sets `first_gw = max(1, last_gw - 1)`. Pagination stops once a page contains articles older than `first_gw`, but every fetched article on that page is still persisted.
- **Fail-loud persistence**: Articles with missing IDs or timestamps raise immediately. Every article is saved even if `--limit` is reached.
- **Timestamped storage**: Articles are stored using `JsonSnapshotStore` with timestamped filenames (`{id}_{timestamp}.json`). Only the latest snapshot is kept per article (older snapshots are automatically deleted).
- **Gameweek-specific listing**: `--list-known` and `--list-known-content` load articles for specific gameweeks and collections using `list_saved_news()` with required `gameweek` and `collection` parameters.
- **Strict Validation**: The validation layer checks every extracted player id against the game's own player list and rejects any fact whose id and name disagree, so no hallucinated player reaches the facts layer. Spelling is forgiven and identity is not: `comparable_name` treats `Odegaard` and `Ødegaard` as the same player, while `Murillo` filed under Aina's id is rejected. Both of those are real cases from the first live run.
- **A rejected fact is dropped, counted and logged — never silently, and never fatally.** One wrong id used to raise and abandon the whole gameweek, which left the news layer empty. Now the run continues and fails only if the rejection *rate* exceeds `MAX_REJECT_RATE` (25%), which means something systemic like a bootstrap snapshot from the wrong season.
- **No API key.** Extraction runs through the `claude` CLI, which is already authenticated. `src/fpl/client/claude_cli.py` replaced a Gemini SDK client and its `GEMINI_API_KEY`.
- **The extractor runs isolated.** `claude -p` started inside this repo reads `CLAUDE.md` and behaves like an engineering agent — the first trial refused to extract, citing this repo's own rule against inventing player data. Every call therefore runs in an empty scratch directory with tools disabled.
- **No server-side JSON schema.** The CLI cannot enforce `response_schema` the way the Gemini SDK could, so `RESPONSE_SCHEMA` is stated in the prompt and the reply is parsed defensively and checked for required keys on the way back.

# Components
- **Collection**:
  - `NewsCollectionConfig` in `src/fpl/loader/news/pl.py`: Declarative configuration for each source (API params + converter).
  - `fetch_news` in `src/fpl/loader/news/pl.py`: Calls the PL content API.
  - `load_recent_news` in `src/fpl/loader/news/pl.py`: Main pagination loop.
  - `list_saved_news` in `src/fpl/loader/news/pl.py`: Loads raw articles from disk.

- **Extraction**:
  - `ClaudeCliClient` in `src/fpl/client/claude_cli.py`: runs `claude -p`, parses the JSON envelope, retries, and checks the reply's shape.
  - `process_article` in `src/fpl/loader/news/llm.py`: sends raw article content plus player context to the model and writes the `extracted` layer.
  - `main` in `src/fpl/loader/news/llm.py`: CLI for batch processing articles for a gameweek. `--model` picks the tier (default `sonnet`).

- **Validation**:
  - `validate.py`: reads the `extracted` layer, verifies player identity, and converts to `NewsFact` objects. `comparable_name` decides what counts as the same player; `_extracted_dir` also reads retired layer names (`gemini`) so stored seasons stay usable.
  - `list_saved_facts`: Helper to load validated facts for models.

# Data/Control Flow
1. **Fetch**: `src/fpl/loader/news/pl.py` downloads raw news to `.../raw/`.
2. **Extract**: `src/fpl/loader/news/llm.py` reads `raw`, queries the model via `claude -p`, saves to `.../extracted/`.
3. **Validate**: `src/fpl/loader/news/validate.py` reads `extracted`, validates identities, saves to `.../facts/`.
4. **Consume**: Models use `Query.news_facts_by_player` or `Query.news_facts_by_gameweek` to access validated facts.

# Public API

## CLI
- **Fetch**: `./run.sh -m src.fpl.loader.news.pl fpl_scout --last-gw M [--first-gw N ...]`
- **List Raw**: `./run.sh -m src.fpl.loader.news.pl fpl_scout --last-gw M --list-known`
- **Extract**: `./run.sh -m src.fpl.loader.news.llm fpl_scout --last-gw M [--first-gw N] [--model sonnet]`
- **Validate**: `./run.sh -m src.fpl.loader.news.validate fpl_scout --last-gw M [--first-gw N]`

All three run together as part of `./refresh.sh`; `SKIP_NEWS=1` skips them. Extraction calls `claude -p` once per unprocessed article, so it costs money and a few minutes; results are cached per article, so a re-run only pays for new ones.

## Programmatic Helpers
- `async fetch_news(...)`
- `async load_recent_news(...)`
- `list_saved_news(...)`
- `list_saved_facts(season, gameweek, collection) -> list[NewsFact]`

# Key Paths
- Implementations: `src/fpl/loader/news/`
- LLM transport: `src/fpl/client/claude_cli.py`
- Data root: `data/<season>/news/<gameweek>/<collection>/<layer>/<id>.json`
  - Layers: `raw`, `extracted`, `facts` (the extracted layer was `gemini` before the extractor changed; old data is still read)

# Related Docs
- News north star (data model, storage hierarchy) — `docs/news_ns.md`
- Loader overview (API snapshots + registries) — `src/fpl/loader/README.md`
