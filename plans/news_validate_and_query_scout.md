# Plan: News Validation and Query Access for FPL Scout

## Title
Implement News Validation and Query Access

## Summary
This plan covers the implementation of the news validation layer (`validate.py`) and the extension of the `Query` facade to access processed news facts. This completes the pipeline from raw news collection to actionable data access for models.

## Background and Context
- **News North Star**: `@docs/news_ns.md` describes the full architecture.
- **Collection**: `src/fpl/loader/news/pl.py` handles fetching raw news.
- **LLM Processing**: `src/fpl/loader/news/llm.py` (assumed) handles extraction to the "gemini" layer.
- **Validation**: This plan implements Stage 2 (Validation) defined in `@docs/news_ns.md` section 4.
- **Query Access**: This plan implements the `Query` extensions defined in `@docs/news_ns.md` section 5.

## Goals
- Implement `src/fpl/loader/news/validate.py` to move data from "gemini" layer to "facts" layer with strict validation.
- Update `src/fpl/models/immutable.py` to allow querying facts and raw news by player and gameweek.
- Ensure strict data integrity: fail loudly if extracted player names do not match IDs.

## Scope and Assumptions
- **Scope**:
    - New script `src/fpl/loader/news/validate.py`.
    - Updates to `src/fpl/models/immutable.py`.
    - Updates to related `README.md` files.
- **Assumptions**:
    - `src/fpl/loader/news/llm.py` exists or is being implemented and produces data in the "gemini" layer format.
    - `src/fpl/loader/convert/news.py` contains necessary schemas (or needs to be updated/verified).
    - "fpl_scout" is the only collection source for now.

## Approach

### 1. Implement `src/fpl/loader/news/validate.py`
- **CLI Interface**:
    - `python -m src.fpl.loader.news.validate fpl_scout --last-gw=15 [--first-gw=15] [--list-article-id=123]`
    - `--last-gw`: Required.
    - `--first-gw`: Optional, defaults to `--last-gw`.
    - `--list-article-id`: Filter processing by specific article IDs (replaces tag filter).
- **Logic**:
    1.  **Load Context**: Load bootstrap data (players) to validate `player_id` vs `web_name`.
    2.  **Iterate**: Loop through gameweeks and articles in the "gemini" layer.
    3.  **Validate**:
        - Read `gemini` JSON.
        - For each fact: check if `fact.web_name` matches the system's `web_name` for `fact.player_id`.
        - **FAIL LOUDLY** on mismatch.
    4.  **Transform**: Create `NewsFact` objects (add `news_id`, `next_gameweek`).
    5.  **Persist**: Save to "facts" layer using `JsonSnapshotStore`.

### 2. Update `src/fpl/models/immutable.py` (Query Facade)
- **New Methods**:
    - `news_facts_by_player(player_id, gameweek=None) -> list[NewsFact]`
    - `news_facts_by_gameweek(gameweek) -> list[NewsFact]`
    - `raw_news(news_id) -> dict`
    - `raw_news_by_gameweek(gameweek) -> list[dict]`
- **Implementation**:
    - Navigate the file structure: `data/{season}/news/{gameweek}/{collection}/{layer}/{id}.json`.
    - Load data efficiently (memoization or direct file reads as appropriate for the `Query` class usage pattern).

### 3. Completeness Check
- Verify against `@docs/news_ns.md`.
- **Potential Gap**: The prompt asks to implement `@docs/news_ns.md:142-145` (Query facade). The North Star also mentions extending `PlayerTotalPrediction` (lines 153-156). This plan *only* covers the Query facade. If `PlayerTotalPrediction` integration is required for "completeness", it should be noted.

## Milestones
1.  **Validation Script**: Create `validate.py` and ensure it processes "gemini" output correctly.
2.  **Query Extension**: Add news accessors to `Query` class.
3.  **Documentation**: Update `src/fpl/loader/news/README.md` and check `docs/news_ns.md` status.

## Risks / Open Questions
- **Data format**: Ensure `gemini` layer JSON structure exactly matches what `validate.py` expects.
- **Performance**: Reading many small JSON files for `news_facts_by_gameweek` might be slow if there are hundreds of articles. (Mitigation: `Query` class usually handles one gameweek/player context, or we can index if needed later).
