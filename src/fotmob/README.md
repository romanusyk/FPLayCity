# Overview
Unified FotMob module: data capture (loader), core models/metadata, and rotation analysis. Captures raw match details, validates into typed models, and provides FPL‑aligned rotation insights (squad roles and rival hints) keyed by FotMob/FPL identifiers.

# Key Concepts
- **Snapshot acquisition**: Save raw `/api/data/matchDetails` payloads per match under `data/<season>/lineups/<team>/<match_id>.json`.
- **Validated models**: Strict Pydantic types for `MatchDetails`, `FotmobTeam`, `FotmobPlayer`, and `Substitution`.
- **Deterministic mapping**: Per‑season FPL team ↔ FotMob team mapping and tokenized name matching for FotMob↔FPL players, with explicit overrides.
- **Season window**: A match belongs to the season whose 1 July–1 July window contains its kickoff. FotMob's team feed spans seasons and its match slugs are *not* season‑scoped, so without this filter last season's `brentford-vs-liverpool` slug silently saves *this* season's fixture. See `Season.window` in `src/fpl/loader/utils.py`.
- **Competitive vs friendly**: Pre‑season friendlies are captured and kept, tagged `MatchKind.FRIENDLY`, and down‑weighted downstream instead of discarded.
- **Data completeness**: Missing or ambiguous data raises exceptions; we never silently skip records.
- **GW timeline**: Map match `event_time` to GW‑effective using FPL deadlines.

# Components
- **Loader (capture + read)**: `FotMobClient`, `load_saved_match_details` in `src/fotmob/load.py`
- **Models/Metadata**: `MatchKind`, `MatchDetails` in `src/fotmob/models/fotmob.py`; `SEASON_TEAMS`, `teams_for_season`, `validate_against_fpl` in `src/fotmob/models/fotmob_metadata.py`
- **Rotation**: `RotationConfig` in `src/fotmob/rotation/rotation_config.py`; view types in `src/fotmob/rotation/rotation_view.py`; `FotmobAdapter` in `src/fotmob/rotation/fotmob_adapter.py`; `RotationAnalyzer` in `src/fpl/models/rotation.py`

# Data/Control Flow
1) Acquire: Loader navigates club pages, captures `/api/data/matchDetails`, and writes JSON snapshots.
2) Read: `load_saved_match_details` parses snapshots into `MatchDetails` lists per team, sorted by `event_time`, optionally filtered by `MatchKind`.
3) Map: Adapter converts team names → ids, builds FotMob↔FPL player mappings (team‑scoped then global), and applies overrides.
4) Analyze: Rotation analyzer filters matches by league, assigns GW‑effective, aggregates appearances/substitutions, and derives squad roles/rivals.
5) Query: FPL‑facing methods return `PlayerSquadRole` and `RivalStartHint` for a given player and GW cutoff.

# CLI

```bash
uv run -m src.fotmob.load                      # every club in the current season
uv run -m src.fotmob.load --team 'Coventry'    # a single club by name
uv run -m src.fotmob.load --season 2025-2026   # backfill an earlier season
```

Re-runs are idempotent: matches already on disk are skipped, out-of-window fixtures are
counted and reported, and stale slugs are warned about without aborting the remaining matches.

# Public API (entry points)
- Loader: `FotMobClient`, `load_saved_match_details(season=None, team_filter=None, limit_per_team=None, kinds=None)`
- Timeline: `build_gameweek_mapper(gameweeks) -> GwMapper`
- Adapter:
  - `get_fotmob_player_id(fpl_player_id) -> int`
  - `get_fpl_player_id_from_fotmob(fotmob_player_id) -> int`
  - `get_player_squad_role(fpl_player_id, max_gameweek) -> PlayerSquadRole`
  - `get_rival_start_hint(fpl_player_id, max_gameweek) -> RivalStartHint`

# Key Paths
- Loader: `src/fotmob/load.py`
- Types/Metadata: `src/fotmob/models/fotmob.py`, `src/fotmob/models/fotmob_metadata.py`
- Rotation: `src/fotmob/rotation/rotation_config.py`, `src/fotmob/rotation/rotation_view.py`, `src/fotmob/rotation/fotmob_adapter.py`, `src/fpl/models/rotation.py`

# Related Docs
- Loader details — capture and replay — `src/fotmob/README.md` (to be moved/merged)
- Models — data types and metadata — `src/fotmob/models/README.md`
- Rotation — concepts and API — `src/fotmob/rotation/README.md`


