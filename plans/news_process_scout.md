# News Processing - The Scout

## Summary
Implement the 3-stage processing pipeline for "The Scout" news articles, transforming raw fetched articles into validated player facts using Gemini. This covers the "LLM" and "Validate" stages defined in the North Star vision.

## Background and Context
- **North Star**: `docs/news_ns.md` - Defines the hierarchical storage, 3-stage pipeline, and validation rules.
- **Storage Mechanism**: `src/fpl/loader/store/json.py` - Used for caching and versioning via `JsonSnapshotStore`.
- **Models**: `src/fpl/models/immutable.py` - Defines core data structures (NewsFact).

## Goals
- Implement **Stage 1 (LLM)**: Script to read raw news, prompt Gemini with player context, and save raw structured output to the `gemini` layer.
- Implement **Stage 2 (Validate)**: Script to read from `gemini` layer, validate player identity, and save `NewsFact` objects to the `facts` layer.
- Define shared schemas and converters in `src/fpl/loader/convert/news.py`.
- Ensure strict validation: Fail loudly if LLM-returned `web_name` does not match the `player_id` in our database.

## Scope and Assumptions
- **Scope**: Only handles "The Scout" (fpl_fantasy) collection initially.
- **Assumption**: `bootstrap-static` data is available for player context.
- **Assumption**: Google Gemini API key is available in environment.

## Approach

### 1. Shared Schemas (`src/fpl/loader/convert/news.py`)
Define intermediate and final schemas:
- `GeminiFact`: `player_id`, `web_name`, `fact`, `form`, `availability`.
- Update `NewsModel` or create `NewsFactModel` as needed for the final layer.

### 2. Stage 1: LLM Processing (`src/fpl/loader/news/process_llm.py`)
- **Input**:
  - Raw news from `data/.../raw/`.
  - Player context (ID, name, team, price) from `bootstrap-static`.
- **Action**:
  - Construct prompt with player context.
  - Call Gemini API (structured output).
- **Output**:
  - Save result to `data/.../gemini/` using `JsonSnapshotStore` (timestamped).

### 3. Stage 2: Validation (`src/fpl/loader/news/process_validate.py`)
- **Input**: Latest snapshot from `data/.../gemini/`.
- **Action**:
  - Verify `gemini_fact.web_name == system_player.web_name` for `gemini_fact.player_id`.
  - Raise exception on mismatch.
  - Map to `NewsFact` domain object.
- **Output**:
  - Save result to `data/.../facts/` using `JsonSnapshotStore`.

## Milestones

- [ ] **Update Schemas**: Add `GeminiFact` and `NewsFact` schemas to `src/fpl/loader/convert/news.py`.
- [ ] **LLM Script**: Create `src/fpl/loader/news/process_llm.py` to handle Gemini interaction and caching.
- [ ] **Validation Script**: Create `src/fpl/loader/news/process_validate.py` to handle strict validation and final storage.
- [ ] **Integration Check**: Verify the full pipeline (Fetch -> LLM -> Validate) produces correct JSON output.

## Risks / Open Questions
- **Risk**: Context window size if player list is too large (approx 600-800 players). *Mitigation: Send only essential fields (ID, Name, Team, Price).*
- **Risk**: LLM formatting issues. *Mitigation: Use strict JSON schema enforcement in Gemini API call.*
