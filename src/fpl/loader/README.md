# Overview
Fetch and snapshot Fantasy Premier League data to disk with timestamped JSON snapshots, and provide entry points that populate the in‑memory immutable registries used by the computation/forecasting pipeline. This package also hosts focused loaders for external sources (FotMob match lineups and Premier League "The Scout" news).

# Key Concepts
- **Seasoned data roots**: snapshots live under `data/<season>/...` and are named `<prefix>_<ISO8601_timestamp>.json`.
- **Single snapshot storage**: each resource maintains only the latest snapshot; old snapshots are automatically deleted when new ones are created.
- **Freshness (days)**: skip refetching if the latest snapshot is newer than the configured freshness window.
- **Snapshot store + converters**: `JsonSnapshotStore` (in `loader/store`) owns filename construction, freshness, and persistence; `loader/convert` exposes pure JSON↔dataclass helpers so population logic stays explicit.
- **Fail‑loudly population**: when building registries, required fields are validated and missing/invalid data raises immediately (project policy).
- **Prior‑season baseline**: before a new season kicks off, `bootstrap-static` still carries last season's per‑player totals. We capture them so a fresh season does not start from an empty dataset. See `src/fpl/loader/baseline.py`.
- **Cross‑season identity**: element `id` and team `id` are both reassigned every season. Only element `code` and team `short_name` are stable, so all cross‑season joins use those.
- **Draft entry and league ids are re‑issued every season too**, and unlike element ids they identify *people*. Entry 52242 was ours all through 2025/26 and now serves a stranger's team. So they are never written into source: `data/<season>/draft_league.json` holds them, under the season they belong to, and `_draft_entries(season)` in `src/fpl/loader/load.py` is the only way manager picks find out who to fetch. With no league connected it fetches nobody and says so.
- **A permanent id is a *stable* wrong answer.** Classic‑FPL entry ids genuinely never change, and this README used to conclude that `FplManager` could therefore hardcode one. It did, from December 2025, and the entry belonged to somebody else — checked live on 2026‑08‑26, entry 2486591 is "Reddevil", not ours. A seasonal id breaks every July; a permanent one never breaks, so nobody checks it. Our classic entry now lives in `data/fpl_entry.json`, written by a command that fetches the entry and prints whose team it is first. See `src/fpl/loader/fpl_squad.py`.
- **A classic‑FPL squad is always one gameweek behind.** FPL publishes picks only after a deadline, so the freshest squad that exists anywhere is the last *started* gameweek's. `FplSquad` carries that gameweek so no screen can pass last week's fifteen off as this week's.

# Components
- `Season` in `src/fpl/loader/utils.py`: season directory names plus `Season.CURRENT`, `Season.previous()` and `Season.as_fpl_history_name()`. Rolling to a new season is a one-line change to `Season.CURRENT`; nothing else hardcodes a season.
- `build_prior_season_baseline` / `persist_prior_season_baseline` / `load_prior_season_baseline` in `src/fpl/loader/baseline.py`: reconcile the previous season's totals and populate `PlayerSeasons`.
- `JsonSnapshotStore` + `SnapshotSpec` in `src/fpl/loader/store/json.py`: file prefix construction, snapshot discovery, freshness checks, and `get_or_fetch(...)` with automatic cleanup.
- `fetch_json` in `src/fpl/loader/http.py` plus `BASE_FPL_URL` / `BASE_DRAFT_URL`: one throttled JSON getter for both games, shared by `load.py` and `draft_league.py`. Raises on any non‑2xx: a 404 read as an empty body would look like "this manager owns nobody".
- `fetch_player_summaries` in `src/fpl/loader/load.py`: persists both per-player and aggregate snapshots.
- `load_squad` / `load_config` / `fetch_picks` / `FplSquad` in `src/fpl/loader/fpl_squad.py`: our own classic‑FPL fifteen, read from the stored `entry/{id}/event/{gw}/picks/` snapshot. Validates fifteen players, slots 1‑15 and a 2/5/5/3 shape, because a board filtered by fourteen looks exactly like one filtered by fifteen. `FplSquad` also carries `bank` and `squad_value` from the payload's `entry_history`, which is what makes a transfer priceable; both are `None` when the payload omits them, and readers must not read that as £0.0 — zero is itself a budget, and the tightest one. `picks_store` is the single definition of the snapshot path, shared by the fetcher in `load.py` and the reader.
- `fetch_league` / `build_ownership` / `load_ownership` / `LeagueConfig` in `src/fpl/loader/draft_league.py`: who owns whom in our draft league, from `league/{id}/element-status` in one request. Validates that every entry holds exactly fifteen players and that one of them is ours, because a waiver suggestion built on half a squad is worse than none. Statuses matter: `a` claimable, `o` owned, `l` locked until the next deadline.
- `loader/convert`: pure helpers (e.g., `event_json_to_gameweek`, `fixture_json_to_fixture`, `element_json_to_player`) that translate JSON payloads to immutable dataclasses (and vice versa) before collections are populated.
- Populators in `bootstrap(...)` in `src/fpl/loader/load.py`: iterate through JSON blobs, call the convert helpers, and add the resulting dataclasses to `Gameweeks`, `Teams`, `Fixtures`, `Players`, `PlayerFixtures`, `News`.
- `load_from_snapshots(season)` in `src/fpl/loader/load.py`: the offline sibling of `bootstrap`. No HTTP client, no manager picks, no news — just Teams, Gameweeks, Fixtures, Players and the prior-season baseline, read from whatever is on disk. Used by the projector and the review app, which need reproducibility rather than freshness. It clears the collections first, because they are process-level singletons and a second load would otherwise collide on every key.

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
     - `PlayerSeasons` from the previous season's totals, reconciled against each element's `history_past`.
     - `PlayerFixtures` from each player's `history` and upcoming `fixtures`.
     - `News` from timestamped snapshot files: `data/<season>/news/<gameweek>/<collection>/raw/<id>_<timestamp>.json` (only "fpl_scout" collection for `NEXT_GAMEWEEK`), converted via `news_stored_json_to_model`.
  3. Any missing required field raises immediately (no silent skips).

# Public API
- `async def load(client, next_gameweek, freshness=1, season=None) -> None`  
  Side‑effects: upserts snapshots under `data/<season>/*`; does not populate in‑memory registries.
- `async def bootstrap(client, next_gameweek, season=None) -> None`  
  Side‑effects: populates `src/fpl/models/immutable.py` registries (`Gameweeks`, `Teams`, `Fixtures`, `Players`, `PlayerSeasons`, `PlayerFixtures`, `News`) from on‑disk snapshots. Before GW1 there are no manager picks, so presence loading is skipped; draft presences are also skipped, with a warning, when no draft league is connected, and classic‑FPL presences when no team is connected. Nothing is ever filed under `is_mine=True` on a guess.
- `async def capture_prior_season(client, season=None, freshness=1) -> str`  
  Side‑effects: writes `data/<season>/prior_season/<prior_season>_<ts>.json`. Returns its path.
- `def load_from_snapshots(season=None) -> None`  
  Populates `Gameweeks`, `Teams`, `Fixtures`, `TeamFixtures`, `Players` and `PlayerSeasons` from stored snapshots. No network, no client, idempotent. Raises `FileNotFoundError` if the bootstrap or fixtures snapshot is missing.

CLI:

```bash
./run.sh -m src.fpl.fetch                 # refresh current-season snapshots
./run.sh -m src.fpl.fetch --baseline      # also capture last season's player totals
./run.sh -m src.fpl.fetch --baseline-only # capture the baseline and nothing else

./run.sh -m src.fpl.league --entry 12345  # connect our draft league (once per season)
./run.sh -m src.fpl.league --refresh      # re-read who owns whom, before a waiver deadline
./run.sh -m src.fpl.league --show         # print every squad, offline

./run.sh -m src.fpl.squad --entry 12345   # connect our classic-FPL team (once, ever)
./run.sh -m src.fpl.squad --refresh       # re-fetch the latest published picks
./run.sh -m src.fpl.squad --show          # print our fifteen, offline
```

`--entry` takes the number from the address of our team page on `draft.premierleague.com`. It
looks the entry up first and logs the team and manager name it found, so a mistyped id shows as
somebody else's team rather than being written to disk.

Minimal usage:

```python
from httpx import AsyncClient
from src.fpl.loader.load import bootstrap

async with AsyncClient() as client:
    await bootstrap(client, next_gameweek=1)
```

Offline, from what is already on disk:

```python
from src.fpl.loader.load import load_from_snapshots

load_from_snapshots()  # Season.CURRENT
```

# Key Paths
- Module: `src/fpl/loader/load.py`
- Shared HTTP: `src/fpl/loader/http.py`
- Draft league ownership: `src/fpl/loader/draft_league.py`, CLI in `src/fpl/league.py`
- Our classic-FPL team: `src/fpl/loader/fpl_squad.py`, CLI in `src/fpl/squad.py`. Config at
  `data/fpl_entry.json` — deliberately *not* under a season, because classic ids are permanent
- Utils (`Season`): `src/fpl/loader/utils.py`
- Prior-season baseline: `src/fpl/loader/baseline.py`
- News loader: `src/fpl/loader/news/`
- FotMob loader: `src/fotmob/load.py`
- Data roots (single snapshot files):  
  `data/<season>/bootstrap_<ts>.json`, `data/<season>/fixtures_<ts>.json`,  
  `data/<season>/elements_<ts>.json` (aggregate), `data/<season>/elements/<id>_<ts>.json` (per-player),  
  `data/<season>/news/<gameweek>/<collection>/raw/<id>_<ts>.json`, `data/<season>/lineups/<team>/`,
  `data/<season>/prior_season/<prior_season>_<ts>.json`,
  `data/<season>/draft_league/<league_id>/{details,element_status}_<ts>.json`
- Per-machine, per-season configuration: `data/<season>/draft_league.json` (which league and
  which team in it are ours). Not committed - the ids in it are re-issued every season.
- Derived, *not* single-snapshot: `data/<season>/runs/<game>/<run_id>.json` and
  `data/<season>/feedback/gw<NN>/` are histories, written by `src/fpl/projection/`.

# Related Docs
- News loader — fetch and persist PL “The Scout” articles — `src/fpl/loader/news/README.md`.
- Projection engine — what consumes `load_from_snapshots` and writes run artifacts — `src/fpl/projection/README.md`.
- Review app — what reads ownership, on the waivers screen — `src/web/README.md`.
- Data collections and lifecycle — every directory under `data/` and who writes it — `data/README.md`.
- FotMob capture/reader — browser‑driven capture of lineups and saved match details — `src/fotmob/README.md`.
- Documentation standards — top‑down style and linking rules — `docs/metadoc.md`.


