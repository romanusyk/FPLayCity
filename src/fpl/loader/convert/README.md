# Overview
Pure conversion helpers that translate raw JSON payloads into immutable dataclasses (and back). Consolidating this logic keeps `load.py` and the news loader declarative: fetch a blob, call the converter, then persist or populate collections without sprinkling parsing code everywhere.

# Key Concepts
- **Stateless functions**: Every helper is a pure function with explicit inputs/outputs; no hidden global state or filesystem dependence.
- **Fail-loud mapping**: Required fields raise immediately (e.g., `news_json_to_model` verifies `id`), matching the repository’s “data completeness over graceful degradation” rule.
- **Bidirectional conversions**: Most helpers have JSON→dataclass and dataclass→JSON pairs so loaders can serialize snapshots in a consistent shape.
- **News conversion contract**: `news_json_to_model` and `news_model_to_json` hide the PL content quirks (URL construction, millisecond timestamps, optional summaries) so `src/fpl/loader/news/pl.py` can focus on storage, paging, and CLI concerns.

# Components
- `fpl_api.py`
  - `event_json_to_gameweek` / `gameweek_to_json`
  - `team_json_to_team` / `team_to_json`
  - `fixture_json_to_fixture` / `fixture_to_json`
  - `element_json_to_player` / `player_to_json`
  - `history_entry_to_player_fixture`, `future_fixture_to_player_fixture`, plus their reverse helpers
- `news.py`
  - `news_json_to_model`: Maps PL content API records to `News` dataclasses (URL construction, summary/body fallback, tag extraction, millisecond timestamps).
  - `news_stored_json_to_model`: Converts stored JSON (from `news_model_to_json`) back to `News` dataclasses, used when loading persisted articles.
  - `tags_json_to_tags`: Shared helper for the tags sub-objects.
  - `news_model_to_json`: Serializes a `News` dataclass for both raw article storage and CLI listing output.

# Data/Control Flow
1. Loaders fetch JSON (FPL API, per-player summaries, or Premier League news).
2. Convert raw dicts via the relevant helper (e.g., `event_json_to_gameweek` or `news_json_to_model`).
3. Persist snapshots or populate registries with the resulting dataclasses.
4. When writing back to disk (e.g., news `raw` files or CLI listings), call the reverse helper (`news_model_to_json`) to ensure storage stays canonical.
5. When loading persisted news articles (e.g., in `bootstrap`), use `news_stored_json_to_model` to convert stored JSON back to dataclasses.

# Public API
- From `src/fpl/loader/convert/__init__.py`:
  - `event_json_to_gameweek`, `gameweek_to_json`
  - `team_json_to_team`, `team_to_json`
  - `fixture_json_to_fixture`, `fixture_to_json`
  - `element_json_to_player`, `player_to_json`
  - `history_entry_to_player_fixture`, `future_fixture_to_player_fixture`, `player_fixture_to_history_json`, `player_fixture_to_future_json`
  - `news_json_to_model`, `news_model_to_json`, `news_stored_json_to_model`, `tags_json_to_tags`

# Key Paths
- `src/fpl/loader/convert/__init__.py`
- `src/fpl/loader/convert/fpl_api.py`
- `src/fpl/loader/convert/news.py`

# Related Docs
- Loader overview — `src/fpl/loader/README.md` (explains how fetch/store/convert fit together)
- Premier League news loader — `src/fpl/loader/news/README.md` (details how the new converters are used)
- News north star / storage contract — `docs/news_ns.md`

