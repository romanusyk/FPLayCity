# Overview
Fetch and snapshot Fantasy Premier League data to disk with timestamped JSON snapshots, and provide entry points that populate the in‑memory immutable registries used by the computation/forecasting pipeline. This package also hosts focused loaders for external sources (FotMob match lineups and Premier League "The Scout" news).

# Key Concepts
- **Seasoned data roots**: snapshots live under `data/<season>/...` and are named `<prefix>_<ISO8601_timestamp>.json`.
- **Single snapshot storage**: each resource maintains only the latest snapshot; old snapshots are automatically deleted when new ones are created.
- **Freshness (days)**: skip refetching if the latest snapshot is newer than the configured freshness window.
- **Snapshot store + converters**: `JsonSnapshotStore` (in `loader/store`) owns filename construction, freshness, and persistence; `loader/convert` exposes pure JSON↔dataclass helpers so population logic stays explicit.
- **Fail‑loudly population**: when building registries, required fields are validated and missing/invalid data raises immediately (project policy).

# Components
- `Season` in `src/fpl/loader/load.py`: constants such as `Season.s2526` used to select `data/<season>/...`.
- `JsonSnapshotStore` + `SnapshotSpec` in `src/fpl/loader/store/json.py`: file prefix construction, snapshot discovery, freshness checks, and `get_or_fetch(...)` with automatic cleanup.
- `fetch_json` / `fetch_player_summaries` in `src/fpl/loader/load.py`: explicit HTTP helpers that respect rate limits and persist both per-player and aggregate snapshots.
- `loader/convert`: pure helpers (e.g., `event_json_to_gameweek`, `fixture_json_to_fixture`, `element_json_to_player`) that translate JSON payloads to immutable dataclasses (and vice versa) before collections are populated.
- Populators in `bootstrap(...)` in `src/fpl/loader/load.py`: iterate through JSON blobs, call the convert helpers, and add the resulting dataclasses to `Gameweeks`, `Teams`, `Fixtures`, `Players`, `PlayerFixtures`, `News`.
- Migration script `src/fpl/loader/migrate_single_snapshot.py`: one-time tool to collapse historical directory-based snapshots into single snapshot files.

# Data/Control Flow
- Incremental refresh `load(client, freshness)`:
  1. Check freshness of existing snapshots through `JsonSnapshotStore`: `data/<season>/bootstrap_<ts>.json`, `data/<season>/fixtures_<ts>.json`.
  2. If stale or missing, `fetch_json(...)` retrieves the payload and the store writes it, deleting the previous snapshot.
  3. Sequentially call `fetch_player_summaries(...)` to read/fetch every `element-summary/{id}` (per-player snapshots under `data/<season>/elements/<id>_<ts>.json` plus an aggregate `data/<season>/elements_<ts>.json`).
- Full bootstrap `bootstrap(client)`:
  1. Fetch `bootstrap-static` and `fixtures` with high freshness to ensure complete on‑disk state.
  2. Build registries from snapshots using the convert helpers:
     - `Gameweeks` from `events` (requires `deadline_time`).
     - `Teams` from `teams`.
     - `Fixtures` from fixtures list (home/away `TeamFixture` pairs).
     - `Players` from `elements`.
     - `PlayerFixtures` from each player's `history` and upcoming `fixtures`.
     - `News` from timestamped snapshot files: `data/<season>/news/<gameweek>/<collection>/raw/<id>_<timestamp>.json` (only "fpl_scout" collection for `NEXT_GAMEWEEK`), converted via `news_stored_json_to_model`.
  3. Any missing required field raises immediately (no silent skips).

# Public API
- `async def load(client: httpx.AsyncClient, freshness: int = 1) -> None`  
  Side‑effects: upserts snapshots under `data/<season>/*`; does not populate in‑memory registries.
- `async def bootstrap(client: httpx.AsyncClient) -> None`  
  Side‑effects: populates `src/fpl/models/immutable.py` registries (`Gameweeks`, `Teams`, `Fixtures`, `Players`, `PlayerFixtures`, `News`) from on‑disk snapshots.

Minimal usage:

```python
from httpx import AsyncClient
from src.fpl.loader.load import bootstrap

async with AsyncClient() as client:
    await bootstrap(client)
```

# Key Paths
- Module: `src/fpl/loader/load.py`
- Migration script: `src/fpl/loader/migrate_single_snapshot.py`
- Utils: `src/fpl/loader/utils.py`
- News loader: `src/fpl/loader/news/`
- FotMob loader: `src/fpl/fotmob/load.py`
- Data roots (single snapshot files):  
  `data/<season>/bootstrap_<ts>.json`, `data/<season>/fixtures_<ts>.json`,  
  `data/<season>/elements_<ts>.json` (aggregate), `data/<season>/elements/<id>_<ts>.json` (per-player),  
  `data/<season>/news/<gameweek>/<collection>/raw/<id>_<ts>.json`, `data/<season>/lineups/<team>/`

# Related Docs
- News loader — fetch and persist PL “The Scout” articles — `src/fpl/loader/news/README.md`.
- FotMob capture/reader — browser‑driven capture of lineups and saved match details — `src/fpl/fotmob/README.md`.
- Documentation standards — top‑down style and linking rules — `docs/metadoc.md`.


