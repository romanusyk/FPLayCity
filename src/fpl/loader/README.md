# Overview
Fetch and snapshot Fantasy Premier League data to disk with versioned JSON, and provide entry points that populate the in‑memory immutable registries used by the computation/forecasting pipeline. This package also hosts focused loaders for external sources (FotMob match lineups and Premier League “The Scout” news).

# Key Concepts
- **Seasoned data roots**: snapshots live under `data/<season>/...` and are named `response_body_<ISO8601>.json`.
- **Freshness (days)**: skip refetching if the latest snapshot is newer than the configured freshness window.
- **Resource types**: `BaseResource` (versioned store), `SimpleResource` (single endpoint), `CompoundResource` (fan‑out across many ids).
- **Fail‑loudly population**: when building registries, required fields are validated and missing/invalid data raises immediately (project policy).

# Components
- `Season` in `src/fpl/loader/load.py`: constants such as `Season.s2526` used to select `data/<season>/...`.
- `BaseResource` in `src/fpl/loader/load.py`: directory/filename construction, listing states, freshness checks, `get_latest_state(...)`.
- `SimpleResource` in `src/fpl/loader/load.py`: loads one API endpoint (e.g., `bootstrap-static/`, `fixtures/`) and persists the response.
- `CompoundResource` in `src/fpl/loader/load.py`: orchestrates multiple `SimpleResource`s (e.g., `element-summary/{id}/` for all players) and writes a combined snapshot.
- `ensure_dir_exists` in `src/fpl/loader/utils.py`: creates parent directories before writes.
- Populators in `bootstrap(...)` in `src/fpl/loader/load.py`: construct `Gameweeks`, `Teams`, `Fixtures`, `Players`, `PlayerFixtures` from saved snapshots.

# Data/Control Flow
- Incremental refresh `load(client, freshness)`:
  1. Read or fetch latest `bootstrap-static` and `fixtures` into `data/<season>/*`.
  2. Read or fetch all `element-summary/{id}` via `CompoundResource` and persist the combined payload.
- Full bootstrap `bootstrap(client)`:
  1. Fetch `bootstrap-static` and `fixtures` with high freshness to ensure complete on‑disk state.
  2. Build registries from snapshots:
     - `Gameweeks` from `events` (requires `deadline_time`).
     - `Teams` from `teams`.
     - `Fixtures` from fixtures list (home/away `TeamFixture`).
     - `Players` from `elements`.
     - `PlayerFixtures` from each player’s `history` and upcoming `fixtures`.
  3. Any missing required field raises immediately (no silent skips).

# Public API
- `async def load(client: httpx.AsyncClient, freshness: int = 1) -> None`  
  Side‑effects: upserts snapshots under `data/<season>/*`; does not populate in‑memory registries.
- `async def bootstrap(client: httpx.AsyncClient) -> None`  
  Side‑effects: populates `src/fpl/models/immutable.py` registries (`Gameweeks`, `Teams`, `Fixtures`, `Players`, `PlayerFixtures`) from on‑disk snapshots.

Minimal usage:

```python
from httpx import AsyncClient
from src.fpl.loader.load import bootstrap

async with AsyncClient() as client:
    await bootstrap(client)
```

# Key Paths
- Module: `src/fpl/loader/load.py`
- Utils: `src/fpl/loader/utils.py`
- News loader: `src/fpl/loader/news/`
- FotMob loader: `src/fpl/fotmob/load.py`
- Data roots:  
  `data/<season>/bootstrap`, `data/<season>/fixtures`, `data/<season>/elements/<player_id>/`,  
  `data/<season>/news/`, `data/<season>/lineups/<team>/`

# Related Docs
- News loader — fetch and persist PL “The Scout” articles — `src/fpl/loader/news/README.md`.
- FotMob capture/reader — browser‑driven capture of lineups and saved match details — `src/fpl/fotmob/README.md`.
- Documentation standards — top‑down style and linking rules — `docs/metadoc.md`.


