## Overview

This document describes the **north star vision** for news collection and fact extraction in the Fantasy Premier League assistant.  
It focuses on **what** the system should do (capabilities and data structures), not **how** it is implemented.  
For the overall product vision, see **"Product North Star"** at `docs/product_ns.md`.

The news system transforms raw news articles into structured, player-specific facts that inform predictions about player availability and form. Every fact must be traceable back to its source article, enabling users to verify claims and understand the evidence behind predictions.

---

## 1. Data Model

### NewsFact

A `NewsFact` represents a single extracted piece of information about a Premier League player from a news article:

- `player_id: int` — FPL player identifier
- `news_id: int` — Source article identifier (from the news provider)
- `next_gameweek: int` — The gameweek this fact is relevant for
- `fact: str` — Short extract from the article about this specific player
- `form: float` — Impact on player form, range `[-1, 1]`
  - `-1`: Certain evidence the player will score zero points next match
  - `+1`: Strong evidence the player will score many points next match
- `availability: float` — Impact on player availability, range `[-1, 1]`
  - `-1`: Certain evidence the player will not start next match
  - `+1`: Strong evidence the player will start next match with high confidence

**Purpose**: Facts inform predictions by providing qualitative context that complements statistical models. They help answer questions like "Why is this player's availability uncertain?" or "What recent news supports this form projection?"

### Raw News

Raw news articles are stored with full metadata:

- `id: int` — News provider identifier
- `url: str` — Source article URL
- `date: str` — Publication date
- `title: str` — Article title
- `summary: str` — Article summary/description
- `body: str` — Full article HTML/text content

**Purpose**: Enable users to verify facts by reading the original source. The system must support lookup from `news_id` to full raw article.

---

## 2. Data Storage Structure

News data is organized hierarchically by gameweek, collection source, processing layer, and article ID:

```
data/2025-2026/news/<gameweek_id>/<news_collection>/<layer>/<news_id>.json
```

**Components**:

- `gameweek_id`: ID of the next gameweek this news is relevant for (determined from publication date and gameweek deadlines).
- `news_collection`: Data source identifier (e.g., `"fpl_scout"`). Initially only `"fpl_scout"` is supported.
- `layer`: Processing stage
  - `"raw"`: Original fetched data from the news provider.
  - `"gemini"`: Raw, unvalidated structured output from the LLM (Gemini).
  - `"facts"`: Validated and parsed facts (NewsFact objects).
- `news_id`: Provider-assigned article identifier.

**Rationale**: This structure supports:
- Time-based organization (facts organized by target gameweek for efficient filtering)
- Multi-source aggregation (different collections can be merged)
- Processing pipeline visibility (raw → gemini → facts)
- Efficient lookups by gameweek, source, or article ID

**Note**: Facts are never expired or deleted. The primary use case is reading news and facts for the most recent available gameweek, but historical facts remain accessible for analysis.

---

## 3. News Collection

The news collection system fetches articles from external sources and persists them in the `raw` layer.

### Current Implementation

The collection infrastructure exists in `src/fpl/loader/news/pl.py`:

- Fetches articles from the Premier League Fantasy API (The Scout content)
- Paginates through recent articles until encountering known items
- Saves articles to `data/2025-2026/news/<news_id>.json` (current flat structure)
- Provides utilities to list and filter saved articles

**Future Extensions**: Additional data feeds can be added (e.g., `fpl_tactics`, team press conferences, injury reports) by implementing similar loaders that write to different `news_collection` subdirectories.

---

## 4. LLM Fact Extraction Pipeline

The fact extraction process is split into distinct stages to separate expensive LLM calls from validation logic.

### Stage 1: LLM Processing ("Gemini" Layer)

**Script**: Reads from `"raw"`, calls LLM, writes to `"gemini"`.

**Input**:
1. Raw news article (title, summary, body) and target gameweek.
2. **Player Context**: A full dump of all FPL players containing `player_id`, `web_name`, `team` (3-letter code), and `price`. This context helps the LLM ground its extraction in actual player data.

**Process**:
- The LLM (Gemini) is prompted to extract facts and map them to specific players from the provided context list.
- **Output Format**: Structured JSON where each item contains:
  - `player_id`: The ID from the provided list.
  - `web_name`: The name from the provided list (for validation).
  - `fact`: Short extract about the player.
  - `form`: Impact on player form, range `[-1, 1]`.
  - `availability`: Impact on player availability, range `[-1, 1]`.

**Storage**: The raw JSON response from Gemini is saved to the `"gemini"` layer using `src/fpl/loader/store/json.py`. This store handles timestamping and file management (checking if up-to-date, reading, writing), effectively serving as the cache.

### Stage 2: Validation and Parsing ("Facts" Layer)

**Script**: Reads from `"gemini"`, validates, writes to `"facts"`.

**Process**:
1. Read the latest entry from the `"gemini"` layer for a given article.
2. **Validation**: For each extracted fact, verify that the returned `web_name` matches the `web_name` associated with the returned `player_id` in our system.
   - **Failure Policy**: If there is a mismatch, the script **fails loudly**. We do not attempt fuzzy matching or guessing at this stage. Before failing, list all the names that don't match.
3. **Transformation**: Convert validated data into a list of `NewsFact` objects.
   - Append `news_id` and `next_gameweek`
4. **Storage**: Save the final list to the `"facts"` layer.

### Schema and Converters

Both the "gemini" (cache) and "facts" layers rely on shared schemas and converter functions defined in `src/fpl/loader/convert/news.py`. This ensures consistency between the raw LLM output and the final validated facts.

The final flow for the news page and processing consists of three scripts:
1. **Fetch**: Downloads raw news to `raw` layer.
2. **LLM**: Extracts structured data to `gemini` layer.
3. **Validate**: Validates and parses data to `facts` layer.

---

## 5. Integration with Core Models

### Query Facade Extension

The `Query` class in `src/fpl/models/immutable.py` is extended to provide access to news facts and raw articles:

**News Facts**:
- `news_facts_by_player(player_id: int, gameweek: int | None = None) -> list[NewsFact]` — Get all facts for a player, optionally filtered by gameweek
- `news_facts_by_gameweek(gameweek: int) -> list[NewsFact]` — Get all facts relevant to a gameweek

**Raw News**:
- `raw_news(news_id: int) -> dict` — Get full raw article by ID
- `raw_news_by_gameweek(gameweek: int) -> list[dict]` — Get all raw articles for a gameweek

**Rationale**: Following the confirmability principle — users can drill from facts → source articles to verify claims.

### PlayerTotalPrediction Extension

The `PlayerTotalPrediction` class in `src/fpl/models/prediction.py` is extended with:

- `news_facts: list[NewsFact]` property — Returns all available facts for this player relevant to the prediction horizon

**Rationale**: Predictions expose the evidence that supports them. Facts inform availability and form judgments, so they must be accessible alongside point projections.

### Fact Deduplication

Duplicate facts are allowed — multiple sources can reinforce a claim, and the same fact appearing in different articles provides additional evidence. No automatic merging or deduplication is performed.

---

## 7. Key Paths

- News loader: `src/fpl/loader/news/pl.py`
- Query facade: `src/fpl/models/immutable.py` (class `Query`)
- Prediction models: `src/fpl/models/prediction.py` (class `PlayerTotalPrediction`)
- Collection system: `src/fpl/collection.py`

---

## 8. Related Docs

- **"Product North Star"** at `docs/product_ns.md` — Overall product vision and confirmability principle
- **"Documentation Standards"** at `docs/metadoc.md` — Documentation style and structure guidelines

