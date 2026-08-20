# Data: season snapshots — collections, formats, lifecycle

This folder stores season-scoped, timestamped snapshots captured from external sources (FPL API, FotMob) and small derived exports. Each resource maintains a single latest snapshot: `<prefix>_<ISO-8601>.json`. When a new snapshot is written, older snapshots are automatically deleted. All loaders prefer data completeness and fail loudly on missing/invalid essentials.

## Key concepts
- **Season scope**: One directory per season. `2024-2025`, `2025-2026`, `2026-2027`. Paths below use `2025-2026` as the worked example; every season has the same shape. The active season is declared once, by `Season.CURRENT` in `src/fpl/loader/utils.py`.
- **Season boundary**: A match belongs to the season whose 1 July–1 July window contains its kickoff (`Season.window`). Pre-season friendlies therefore file under the season they precede.
- **Cross-season identity**: element `id` and team `id` are reassigned by FPL every season — 16 of 20 team ids changed meaning between 2025/26 and 2026/27. Only element `code` and team `short_name` are stable, and all cross-season joins use those.
- **Single snapshot storage**: Each resource stores only the latest snapshot; old snapshots are automatically deleted when new ones are created via `JsonSnapshotStore` in `src/fpl/loader/store/json.py`.
- **Freshness (days)**: Skip refetching if the latest snapshot is newer than the configured freshness window.
- **Naming**: `<prefix>_YYYY-MM-DDTHH:MM:SS.json`. Lexicographic order = chronological order.
- **Snapshot store**: `JsonSnapshotStore` owns filename construction, freshness checks, and persistence with automatic cleanup.

## Collections (2025–2026)

- **bootstrap**: FPL bootstrap-static (season-wide metadata: teams, players, positions, events).
  - Path: `data/2025-2026/bootstrap_<timestamp>.json`
  - Source: `https://fantasy.premierleague.com/api/bootstrap-static/`
  - Written by: `bootstrap()`/`load()` in `src/fpl/loader/load.py` via `JsonSnapshotStore`
  - Used by: player and team mappings, element IDs for `elements/`, event deadlines.

- **fixtures**: FPL fixtures list (all matches with difficulties and scores).
  - Path: `data/2025-2026/fixtures_<timestamp>.json`
  - Source: `https://fantasy.premierleague.com/api/fixtures/`
  - Written by: `bootstrap()`/`load()` in `src/fpl/loader/load.py` via `JsonSnapshotStore`
  - Used by: difficulty exports (`dumps/fdr.*`), forecasting and aggregation.

- **elements**: FPL per-player summaries plus an aggregated snapshot.
  - Per-player snapshots: `data/2025-2026/elements/<element_id>_<timestamp>.json` from `element-summary/{id}/`
  - Aggregated snapshot: `data/2025-2026/elements_<timestamp>.json` (mapping of `<element_id> -> summary` for the same pull)
  - Written by: `fetch_player_summaries()` in `src/fpl/loader/load.py` (driven by bootstrap `elements` IDs) via `JsonSnapshotStore`
  - Used by: historical and upcoming player fixtures, player metrics.

- **lineups**: FotMob match details captured per club and match.
  - Path: `data/2025-2026/lineups/<Team Name>/<match_id>.json`
  - Source: FotMob web app `/api/data/matchDetails` (raw responses saved verbatim)
  - Collected by: `FotMobClient.collect_team_matches(...)` in `src/fotmob/load.py`; only fixtures inside the season window are saved, and each file is named for the `matchId` its own payload reports
  - Read by: `load_saved_match_details(...)` in `src/fotmob/load.py` returning `MatchDetails` models (`src/fotmob/models/fotmob.py`)
  - Contains both competitive fixtures and pre-season friendlies, distinguished by `MatchDetails.kind`
  - Related doc: high-level data flow and parsing — `src/fotmob/README.md`

- **prior_season**: Derived per-player totals for the *previous* season, captured before the new season kicks off.
  - Path: `data/<season>/prior_season/<prior_season>_<timestamp>.json`
  - Source: this season's `bootstrap-static` cross-checked against each `element-summary/{id}/history_past`
  - Written by: `capture_prior_season()` in `src/fpl/loader/load.py`, via `persist_prior_season_baseline()` in `src/fpl/loader/baseline.py`
  - Read by: `load_prior_season_baseline()` → populates the `PlayerSeasons` collection
  - Why: a new season starts with every per-gameweek collection empty. Bootstrap carries last season's totals against each player's *new* club until GW1, so this is the last chance to capture them. Bootstrap is unreliable for players who changed club (zeroed or truncated), so `history_past` is authoritative.
  - Related doc: module docstring in `src/fpl/loader/baseline.py`

- **news**: Premier League "The Scout" articles for FPL.
  - Path: `data/2025-2026/news/<gameweek>/<collection>/raw/<id>_<timestamp>.json`
  - Source: PL content API (series `fantasy`, creator `The-Scout`)
  - Collected by: `load_recent_news()`/`fetch_news()` in `src/fpl/loader/news/pl.py` via `JsonSnapshotStore`; idempotent by `<id>` and `date`
  - Related doc: purpose, flow, CLI — `src/fpl/loader/news/README.md`

- **runs**: Immutable projection artifacts, one file per generated projection.
  - Path: `data/<season>/runs/<game>/<run_id>.json`, where `game` is `draft` or `fpl` and
    `run_id` is `<YYYY-MM-DDTHH-MM-SS>_<method>` (colons avoided so it is a safe filename)
  - Written by: `write_run()` in `src/fpl/projection/artifacts.py`, driven by
    `./run.sh -m src.fpl.project`
  - Read by: the review app (`src/web/api.py`); nothing else recomputes a projection
  - Contents: the method and its full parameter set, the snapshots it read, replacement level
    per position, the `valuation` block (league size and the slot table replacement level was
    priced against), and one row per player with components, inputs, sample sizes, per-fixture
    detail and flags
  - `valuation` exists because the app recomputes replacement level live during a draft. It reads
    the basis back from the run rather than keeping its own copy, which is what stops the live
    board from silently drifting away from the stored one. A run written before this field
    existed is a 409 rather than a guess.
  - **Not a single-snapshot resource.** Every run is kept so two can be compared. `prune_runs()`
    trims to the newest `--keep N` per game, and never deletes a run that stored feedback
    refers to.
  - Related doc: `src/fpl/projection/README.md`

- **feedback**: Recorded disagreements with a projection.
  - Path: `data/<season>/feedback/gw<NN>/<timestamp>_<player_id>.json`
  - Written by: `save_feedback()` in `src/fpl/projection/feedback.py`, from the app's player panel
  - Append-only: entries are never edited, because two contradictory opinions a week apart are
    data about how a view changed, not a conflict
  - Each entry stores the run id and the model's own `p_start` and points alongside yours, which
    is what lets both be scored against the result later

- **draft_state.json**: Who has been taken during a live draft.
  - Path: `data/<season>/draft_state.json` — a single mutable file, overwritten in place
  - Written by: `src/web/draft_state.py` from the draft board
  - Deliberately outside the run artifacts: marking a player taken changes replacement level and
    therefore VORP, but never a projection

- **dumps**: Small, human-friendly exports generated from the latest snapshots.
  - Fixture Difficulty (FDR):
    - Files: `fdr.csv`, `fdr.json`, `fdr.txt`
    - Built by: `python -m src.fpl.dump.fdr` (auto-detects latest bootstrap+fixtures for 2025–2026)
  - Players snapshot:
    - Files: `players.csv`, `players.txt`
    - Built by: `python -m src.fpl.dump.players` (auto-detects latest bootstrap for 2025–2026)

## Lifecycle (typical run order)

1) **Season bootstrap** (one-time at start; safe to re-run):
   - Call `bootstrap(client)` in `src/fpl/loader/load.py`.
   - Writes fresh snapshots for `bootstrap_<ts>.json`, `fixtures_<ts>.json`, and `elements/` (per-player + aggregated).
   - Also seeds in-memory models for teams, fixtures, players, and gameweeks if running the Python process.

2) **Incremental refresh** (idempotent, freshness-controlled):
   - Call `load(client, freshness=...)` in `src/fpl/loader/load.py`.
   - For each resource, `JsonSnapshotStore` checks if the latest file is older than `freshness` days; if stale, fetches and writes a new `<prefix>_<timestamp>.json`, deleting the previous snapshot.

3) **Prior-season baseline** (once, before the new season's GW1):
   - `./run.sh -m src.fpl.fetch --baseline-only` → writes `data/<season>/prior_season/<prior_season>_<ts>.json`.

4) **FotMob lineups collection** (on-demand):
   - `./run.sh -m src.fotmob.load [--team NAME] [--season YYYY-YYYY]` to capture raw match detail JSON per team+match.
   - Use `load_saved_match_details(...)` to parse into validated `MatchDetails` for downstream consumers.

5) **News ingestion** (idempotent pagination):
   - Run `python -m src.fpl.loader.news.pl` with desired flags to fetch recent articles and/or list saved ones.
   - New items are persisted to `data/2025-2026/news/<gameweek>/<collection>/raw/<id>_<timestamp>.json` via `JsonSnapshotStore`; stops on encountering the first already-known item.

6) **Projection runs** (after every refresh, and after every model change):
   - `./run.sh -m src.fpl.project [--game draft|fpl] [--method NAME] [--keep N]` → writes
     `data/<season>/runs/<game>/<run_id>.json` and prunes older runs.
   - `./refresh.sh` chains steps 2, 4 and 6 in one command.
   - Serve them with `./run.sh -m src.web.serve`.

7) **Derived dumps** (optional exports):
   - FDR: `python -m src.fpl.dump.fdr [--first-gw N --last-gw M]` → writes `dumps/fdr.*`
   - Players: `python -m src.fpl.dump.players` → writes `dumps/players.*`

## Invariants and conventions

- Snapshot filenames are strictly ISO timestamps to guarantee lexicographic == chronological ordering.
- Aggregated elements snapshot in `elements_<ts>.json` corresponds to the same pull as per-player files in `elements/<id>_<ts>.json`.
- Loaders favor data completeness: missing or malformed essentials raise errors instead of silently skipping records. Skips that *are* legitimate (out-of-season fixtures, stale FotMob slugs) are counted and logged, never silent.
- A `lineups/<team>/<id>.json` filename always equals the `general.matchId` inside the file.
- Single snapshot per resource: `JsonSnapshotStore.write(..., delete_older=True)` ensures only the latest snapshot exists. `runs/` and `feedback/` are the deliberate exceptions — both are histories, not snapshots.
- Runs are immutable. `write_run()` refuses to overwrite an existing run id, because a comparison or a piece of feedback pointing at a changed run is worse than a missing one.

## Key paths (entry points)

- FPL loader (bootstrap/refresh, elements fan-out): `src/fpl/loader/load.py`
- Offline load from stored snapshots (no network): `load_from_snapshots()` in `src/fpl/loader/load.py`
- Prior-season baseline: `src/fpl/loader/baseline.py`
- Projection runs, feedback: `src/fpl/projection/artifacts.py`, `src/fpl/projection/feedback.py`
- Draft state: `src/web/draft_state.py`
- Season names and windows: `src/fpl/loader/utils.py`
- Snapshot store (filename construction, freshness, persistence): `src/fpl/loader/store/json.py`
- FotMob capture and reader: `src/fotmob/load.py` (models in `src/fotmob/models/fotmob.py`)
- News loader and CLI: `src/fpl/loader/news/pl.py`
- Dumps (FDR, players): `src/fpl/dump/fdr.py`, `src/fpl/dump/players.py`

## Related docs

- Loader overview (API snapshots + registries) — `src/fpl/loader/README.md` (how snapshots are fetched, stored, and converted to in-memory models)
- FotMob adapter design and flow — `src/fotmob/README.md` (how match details are captured and consumed)
- News loader — `src/fpl/loader/news/README.md` (source, pagination, CLI examples)
- Projection engine and run artifacts — `src/fpl/projection/README.md` (how a run is produced and what is in it)
- Review app — `src/web/README.md` (what reads the runs, and how feedback is recorded)
- Documentation standards — `docs/metadoc.md` (top‑down structure, linking, symbol+path references)
