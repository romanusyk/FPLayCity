# News Fetching Refactor: Multi-Collection Support and Gameweek Assignment

## Summary

Refactor the news fetching system to support multiple news collections (initially only `fpl_scout`) with automatic gameweek assignment based on publication dates and gameweek deadlines. The system will organize news articles by gameweek and collection, enabling efficient filtering and querying through the `Query` facade. This change prepares the codebase for the north star vision where news is organized hierarchically by gameweek and collection source.

## Background and Context

**Current State**: The news loader in `src/fpl/loader/news/pl.py` is hardcoded to fetch articles from The Scout (Fantasy Premier League API). Articles are saved to a flat directory structure (`data/2025-2026/news/<news_id>.json`) without gameweek assignment or collection separation.

**Vision**: The news namespace vision (`docs/news_ns.md`) describes a hierarchical storage structure: `data/2025-2026/news/<gameweek_id>/<news_collection>/<layer>/<news_id>.json`. News articles must be automatically assigned to gameweeks based on their publication date relative to gameweek deadlines.

**Related Docs**:
- **News namespace vision** at `docs/news_ns.md` — Describes the target data model, storage structure, and integration points
- **Documentation standards** at `docs/metadoc.md` — Documentation style and structure guidelines
- **Collection system** at `src/fpl/collection.py` — Generic indexed collection implementation used throughout the codebase

## Goals

1. **Multi-collection support**: Refactor code to support multiple news collections (initially only `fpl_scout` is allowed)
2. **Gameweek assignment**: Automatically assign each news article to a gameweek based on `prev_gw_deadline < news.updated_at <= gw_deadline`
3. **Data model**: Convert news extraction logic to a dataclass model and integrate with the Collection system
4. **Query integration**: Extend `Query` facade to provide news lookups by gameweek and collection
5. **Bootstrap integration**: Load news collection during bootstrap alongside other collections
6. **CLI updates**: Update command-line interface to support collection selection and gameweek filtering

## Non-Goals

- LLM fact extraction (future work per `docs/news_ns.md`)
- Support for additional news collections beyond `fpl_scout` (prepared but not implemented)
- Migration of existing news files (assume clean state or manual migration)

## Scope and Assumptions

**Scope**:
- Refactor `src/fpl/loader/news/pl.py` to support collection abstraction
- Create news dataclass model in `src/fpl/models/immutable.py`
- Add news collection to `src/fpl/models/immutable.py`
- Extend `Query` class with news lookup methods
- Integrate news loading into `src/fpl/loader/load.py` bootstrap function
- Update CLI arguments and filtering logic

**Assumptions**:
- Gameweek deadline data is already loaded in bootstrap (via `Gameweeks` collection)
- News articles use ISO8601 date strings for `date` and `lastUpdated` fields
- Only `fpl_scout` collection will be supported initially (validation enforced)
- News storage directory structure follows: `data/<season>/news/<gameweek>/<collection>/raw/<news_id>.json`

## Approach

### High-Level Design

1. **Collection Abstraction**: Create a `NewsCollectionConfig` dataclass that encapsulates:
   - Collection identifier (e.g., `"fpl_scout"`)
   - API endpoint and request parameters
   - Record extraction function (converted to dataclass model)
   - Storage path construction logic

2. **News Model**: Create `News` dataclass in `src/fpl/models/immutable.py` with fields:
   - `id: int` — News provider identifier
   - `url: str` — Source article URL
   - `date: str` — Publication date (ISO8601)
   - `lastUpdated: str` — Last update timestamp (ISO8601)
   - `title: str` — Article title
   - `summary: str` — Article summary/description
   - `body: str` — Full article HTML/text content
   - `tags: list[Tag]` — Article tags (new field), where `Tag` is a dataclass with `id: int` and `label: str`
   - `gameweek: int` — Assigned gameweek (computed during loading)
   - `collection: str` — Source collection identifier

3. **Gameweek Assignment Logic**: For each news article:
   - Parse `lastUpdated` timestamp (fallback to `date` if missing)
   - Find the gameweek where `prev_gw_deadline < timestamp <= gw_deadline`
   - If timestamp is before first gameweek deadline, assign to gameweek 1
   - If timestamp is after last gameweek deadline, assign to the last gameweek

4. **Collection Integration**: Add `News` collection to `src/fpl/models/immutable.py`:
   ```python
   News = Collection[News](
       simple_indices=[SimpleIndex('id')],
       list_indices=[
           ListIndex('gameweek'),
           ListIndex('collection'),
           ListIndex('gameweek', 'collection'),
       ],
   )
   ```

5. **Query Extension**: Add methods to `Query` class:
   - `news(news_id: int) -> News` — Get news by ID
   - `news_by_gameweek(gameweek: int) -> list[News]` — Get all news for a gameweek
   - `news_by_collection(collection: str) -> list[News]` — Get all news from a collection
   - `news_by_gameweek_and_collection(gameweek: int, collection: str) -> list[News]` — Combined filter

6. **Bootstrap Integration**: Load news collection in `bootstrap()` function:
   - After gameweeks are loaded, read existing news files from disk
   - Parse and assign gameweeks (if not already assigned)
   - Add to `News` collection

### Data Flow

**Fetching Flow**:
1. CLI receives `news_collection` argument (e.g., `fpl_scout`)
2. Resolve `NewsCollectionConfig` for the collection
3. Fetch articles using collection-specific API parameters
4. For each article:
   - Extract record using collection-specific model
   - Assign gameweek based on `lastUpdated` timestamp
   - Filter by `first_gw` and `last_gw` if provided
   - Save to `data/<season>/news/<gameweek>/<collection>/raw/<news_id>.json`
   - Add to in-memory `News` collection

**Listing Flow**:
1. CLI receives `news_collection` argument and optional `first_gw`/`last_gw`
2. Query `News` collection filtered by collection and gameweek range
3. Display results (with or without body based on `--list-known-content`)

### Key Code Changes

**`src/fpl/loader/news/pl.py`**:
- Create `NewsCollectionConfig` dataclass
- Create `fpl_scout_config` instance with current API parameters
- Refactor `_extract_record()` to return `News` dataclass instance (extract tags as objects with `id` and `label` from API response)
- Add `_assign_gameweek()` function using `Gameweeks` collection
- Update `load_recent_news()` to accept collection config and gameweek filters
- Update `read_known_news()` functions to query `News` collection instead of filesystem
- Update CLI parser to accept `news_collection`, `--first-gw`, `--last-gw` arguments
- Update file path construction to use hierarchical structure

**`src/fpl/models/immutable.py`**:
- Add `Tag` dataclass with `id: int` and `label: str` fields
- Add `News` dataclass with all required fields (including `tags: list[Tag]`)
- Add `News` collection with appropriate indices
- Extend `Query` class with news lookup methods

**`src/fpl/loader/load.py`**:
- Add news loading to `bootstrap()` function after gameweeks are loaded
- Read existing news files and populate `News` collection

## Milestones / Tasks

1. **Create News Model and Collection**
   - Add `Tag` dataclass with `id: int` and `label: str` to `src/fpl/models/immutable.py`
   - Add `News` dataclass to `src/fpl/models/immutable.py` (including `tags: list[Tag]`)
   - Add `News` collection with indices for `id`, `gameweek`, `collection`, and `(gameweek, collection)`
   - Extend `Query` class with news lookup methods

2. **Create Collection Abstraction**
   - Create `NewsCollectionConfig` dataclass in `src/fpl/loader/news/pl.py`
   - Define `fpl_scout_config` with current API parameters
   - Convert `_extract_record()` to return `News` instance (extract tags as `Tag` objects with `id` and `label` from API response)

3. **Implement Gameweek Assignment**
   - Add `_assign_gameweek()` function that uses `Gameweeks` collection
   - Handle edge cases (before first GW, after last GW)
   - Integrate assignment into loading flow

4. **Refactor Storage and Loading**
   - Update file path construction to hierarchical structure
   - Update `load_recent_news()` to use collection config and save with gameweek assignment
   - Update `read_known_news()` functions to query `News` collection

5. **Update CLI Interface**
   - Add positional `news_collection` argument (validate against allowed collections)
   - Add `--first-gw` and `--last-gw` optional arguments
   - Update filtering logic to use gameweek range
   - Update help text and examples

6. **Integrate with Bootstrap**
   - Add news loading to `bootstrap()` function
   - Read existing news files and populate `News` collection
   - Ensure gameweek assignment happens during bootstrap

7. **Testing and Validation**
   - Test fetching with gameweek filters
   - Test listing with gameweek filters
   - Verify gameweek assignment correctness
   - Verify file structure matches expected hierarchy

## Risks / Open Questions

**Risks**:
- **Existing news files**: Current flat structure needs migration or manual cleanup. Decision: Assume clean state or manual migration (out of scope).
- **Gameweek assignment edge cases**: Articles published exactly at deadline boundaries. Decision: Use `prev_gw_deadline < timestamp <= gw_deadline` (inclusive upper bound).
- **Performance**: Loading all news files during bootstrap may be slow with many articles. Mitigation: Consider lazy loading or caching if needed.

**Open Questions**:
- Should tags be extracted from a specific API field, or computed from other fields? **Answer**: Extract from API response `tags` field (array of objects with `id` and `label`) if available, otherwise empty list.
- How to handle news articles that span multiple gameweeks? **Answer**: Assign to the gameweek based on `lastUpdated` timestamp (single assignment per article).

## Acceptance Criteria / Validation

1. **CLI Usage**: All three example commands work as specified:
   - `uv run -m src.fpl.loader.news.pl fpl_scout` — Loads news for next gameweek
   - `uv run -m src.fpl.loader.news.pl fpl_scout --first-gw=14` — Loads news for recent gameweeks
   - `uv run -m src.fpl.loader.news.pl fpl_scout --list-known-content` — Lists news for next gameweek

2. **Gameweek Assignment**: News articles are correctly assigned to gameweeks based on `lastUpdated` timestamp relative to gameweek deadlines.

3. **File Structure**: News files are saved to `data/<season>/news/<gameweek>/<collection>/raw/<news_id>.json`.

4. **Query Integration**: `Query.news_by_gameweek()`, `Query.news_by_collection()`, and `Query.news_by_gameweek_and_collection()` methods work correctly.

5. **Bootstrap Integration**: News collection is loaded during bootstrap and accessible via `Query` facade.

6. **Tags Storage**: News articles include `tags` field as a list of `Tag` objects (with `id` and `label`) extracted from API response.

7. **Collection Validation**: Only `fpl_scout` collection is accepted (validation error for others).

8. **Filtering**: `--first-gw` and `--last-gw` arguments correctly filter news articles by assigned gameweek.

## Key Paths

- News loader: `src/fpl/loader/news/pl.py`
- News model and collection: `src/fpl/models/immutable.py`
- Bootstrap loader: `src/fpl/loader/load.py`
- Collection system: `src/fpl/collection.py`
- Query facade: `src/fpl/models/immutable.py` (class `Query`)

## Related Docs

- **News namespace vision** at `docs/news_ns.md` — Target architecture and data model
- **Documentation standards** at `docs/metadoc.md` — Documentation style guidelines

