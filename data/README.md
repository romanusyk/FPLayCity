# Data: 2025–2026 season — collections, formats, lifecycle

This folder stores season-scoped, timestamped snapshots captured from external sources (FPL API, FotMob) and small derived exports. Filenames follow `response_body_<ISO-8601>.json`, where new snapshots are appended over time; the latest file by lexicographic order is the freshest view. All loaders prefer data completeness and fail loudly on missing/invalid essentials.

## Key concepts
- **Season scope**: This document describes only `data/2025-2026/`.
- **Snapshots, not deltas**: We persist full responses on each pull to preserve history. Freshness logic decides when to add a new snapshot.
- **Compound vs simple resources**: Some endpoints (e.g., player summaries) are fetched per-entity into subdirectories, plus an aggregated parent snapshot.
- **Naming**: `response_body_YYYY-MM-DDTHH:MM:SS.json`. Lexicographic order = chronological order.

## Collections (2025–2026)

- **bootstrap/**: FPL bootstrap-static (season-wide metadata: teams, players, positions, events).
  - Path: `data/2025-2026/bootstrap/response_body_*.json`
  - Source: `https://fantasy.premierleague.com/api/bootstrap-static/`
  - Written by: `SimpleResource` via `bootstrap()`/`load()` in `src/fpl/loader/load.py`
  - Used by: player and team mappings, element IDs for `elements/`, event deadlines.

- **fixtures/**: FPL fixtures list (all matches with difficulties and scores).
  - Path: `data/2025-2026/fixtures/response_body_*.json`
  - Source: `https://fantasy.premierleague.com/api/fixtures/`
  - Written by: `SimpleResource` via `bootstrap()`/`load()` in `src/fpl/loader/load.py`
  - Used by: difficulty exports (`dumps/fdr.*`), forecasting and aggregation.

- **elements/**: FPL per-player summaries plus an aggregated snapshot.
  - Per-player snapshots: `data/2025-2026/elements/<element_id>/response_body_*.json` from `element-summary/{id}/`
  - Aggregated snapshot: `data/2025-2026/elements/response_body_*.json` (mapping of `<element_id> -> summary` for the same pull)
  - Written by: `CompoundResource` in `src/fpl/loader/load.py` (driven by bootstrap `elements` IDs)
  - Used by: historical and upcoming player fixtures, player metrics.

- **lineups/**: FotMob match details captured per club and match.
  - Path: `data/2025-2026/lineups/<Team Name>/<match_id>.json`
  - Source: FotMob web app `/api/data/matchDetails` (raw responses saved verbatim)
  - Collected by: `FotMobClient.collect_team_matches(...)` in `src/fpl/fotmob/load.py`
  - Read by: `load_saved_match_details(...)` in `src/fpl/fotmob/load.py` returning `MatchDetails` models (`src/fpl/models/fotmob.py`)
  - Related doc: high-level data flow and parsing — `src/fpl/fotmob/README.md`

- **news/**: Premier League “The Scout” articles for FPL.
  - Path: `data/2025-2026/news/<id>.json`
  - Source: PL content API (series `fantasy`, creator `The-Scout`)
  - Collected by: `src/fpl/loader/news/pl.py` (`load_recent_news`, `fetch_news`); idempotent by `<id>` and `date`
  - Related doc: purpose, flow, CLI — `src/fpl/loader/news/README.md`

- **dumps/**: Small, human-friendly exports generated from the latest snapshots.
  - Fixture Difficulty (FDR):
    - Files: `fdr.csv`, `fdr.json`, `fdr.txt`
    - Built by: `python -m src.fpl.dump.fdr` (auto-detects latest bootstrap+fixtures for 2025–2026)
  - Players snapshot:
    - Files: `players.csv`, `players.txt`
    - Built by: `python -m src.fpl.dump.players` (auto-detects latest bootstrap for 2025–2026)

## Lifecycle (typical run order)

1) **Season bootstrap** (one-time at start; safe to re-run):
   - Call `bootstrap(client)` in `src/fpl/loader/load.py`.
   - Writes fresh snapshots for `bootstrap/`, `fixtures/`, and initializes `elements/` (per-player + aggregated).
   - Also seeds in-memory models for teams, fixtures, players, and gameweeks if running the Python process.

2) **Incremental refresh** (idempotent, freshness-controlled):
   - Call `load(client, freshness=...)` in `src/fpl/loader/load.py`.
   - For each resource, if the latest file is older than `freshness` days, a new `response_body_<timestamp>.json` is written.

3) **FotMob lineups collection** (on-demand):
   - Run `FotMobClient.collect_team_matches(...)` in `src/fpl/fotmob/load.py` to capture raw match detail JSON per team+match.
   - Use `load_saved_match_details(...)` to parse into validated `MatchDetails` for downstream consumers.

4) **News ingestion** (idempotent pagination):
   - Run `python -m src.fpl.loader.news.pl` with desired flags to fetch recent articles and/or list saved ones.
   - New items are persisted to `data/2025-2026/news/<id>.json`; stops on encountering the first already-known item.

5) **Derived dumps** (optional exports):
   - FDR: `python -m src.fpl.dump.fdr [--first-gw N --last-gw M]` → writes `dumps/fdr.*`
   - Players: `python -m src.fpl.dump.players` → writes `dumps/players.*`

## Invariants and conventions

- Snapshot filenames are strictly ISO timestamps to guarantee lexicographic == chronological ordering.
- Aggregated elements snapshot in `elements/` corresponds to the same pull as per-player files.
- Loaders favor data completeness: missing or malformed essentials raise errors instead of silently skipping records.

## Key paths (entry points)

- FPL loader (bootstrap/refresh, elements fan-out): `src/fpl/loader/load.py`
- FotMob capture and reader: `src/fpl/fotmob/load.py` (models in `src/fpl/models/fotmob.py`)
- News loader and CLI: `src/fpl/loader/news/pl.py`
- Dumps (FDR, players): `src/fpl/dump/fdr.py`, `src/fpl/dump/players.py`

## Related docs

- FotMob adapter design and flow — `src/fpl/fotmob/README.md` (how match details are captured and consumed)
- News loader — `src/fpl/loader/news/README.md` (source, pagination, CLI examples)
- Documentation standards — `docs/metadoc.md` (top‑down structure, linking, symbol+path references)


