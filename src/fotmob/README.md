# Overview
Unified FotMob module: data capture (loader), core models/metadata, and rotation analysis. Captures raw match details, validates into typed models, and provides FPL‑aligned rotation insights (squad roles and rival hints) keyed by FotMob/FPL identifiers.

# Key Concepts
- **Snapshot acquisition**: Save raw `/api/data/matchDetails` payloads per match under `data/<season>/lineups/<team>/<match_id>.json`.
- **Validated models**: Strict Pydantic types for `MatchDetails`, `FotmobTeam`, `FotmobPlayer`, and `Substitution`.
- **Deterministic mapping**: Hard‑coded FPL team ↔ FotMob team mapping and tokenized name matching for FotMob↔FPL players, with explicit overrides.
- **Data completeness**: Missing or ambiguous data raises exceptions; we never silently skip records.
- **GW timeline**: Map match `event_time` to GW‑effective using FPL deadlines.

# Components
- **Loader (capture + read)**:
  - `FotMobClient` and `load_saved_match_details` in `src/fpl/fotmob/load.py` (proposed move: `src/fotmob/load.py`)
- **Models/Metadata**:
  - Types in `src/fpl/models/fotmob.py` (proposed: `src/fotmob/models/types.py`)
  - Metadata `TEAMS` / `TEAM_NAME_TO_ID` in `src/fpl/models/fotmob_metadata.py` (proposed: `src/fotmob/models/metadata.py`)
- **Rotation**:
  - Config and overrides in `src/fpl/models/rotation_config.py` (proposed: `src/fotmob/rotation/config.py`)
  - View types in `src/fpl/models/rotation_view.py` (proposed: `src/fotmob/rotation/types.py`)
  - Adapter in `src/fpl/models/fotmob_adapter.py` (proposed: `src/fotmob/rotation/adapter.py`)
  - Analyzer and GW mapper (current: `src/fpl/models/rotation.py`, proposed: `src/fotmob/rotation/analyzer.py`)

# Data/Control Flow
1) Acquire: Loader navigates club pages, captures `/api/data/matchDetails`, and writes JSON snapshots.
2) Read: `load_saved_match_details` parses snapshots into `MatchDetails` lists per team, sorted by `event_time`.
3) Map: Adapter converts team names → ids, builds FotMob↔FPL player mappings (team‑scoped then global), and applies overrides.
4) Analyze: Rotation analyzer filters matches by league, assigns GW‑effective, aggregates appearances/substitutions, and derives squad roles/rivals.
5) Query: FPL‑facing methods return `PlayerSquadRole` and `RivalStartHint` for a given player and GW cutoff.

# Public API (entry points)
- Loader: `FotMobClient`, `load_saved_match_details(...)`
- Timeline: `build_gameweek_mapper(gameweeks) -> GwMapper`
- Adapter:
  - `get_fotmob_player_id(fpl_player_id) -> int`
  - `get_fpl_player_id_from_fotmob(fotmob_player_id) -> int`
  - `get_player_squad_role(fpl_player_id, max_gameweek) -> PlayerSquadRole`
  - `get_rival_start_hint(fpl_player_id, max_gameweek) -> RivalStartHint`

# Key Paths
- Current:
  - Loader: `src/fpl/fotmob/load.py`
  - Types/Metadata: `src/fpl/models/fotmob.py`, `src/fpl/models/fotmob_metadata.py`
  - Rotation: `src/fpl/models/rotation_config.py`, `src/fpl/models/rotation_view.py`, `src/fpl/models/fotmob_adapter.py`, `src/fpl/models/rotation.py`
- After refactor (proposed defaults):
  - Loader: `src/fotmob/load.py`
  - Models: `src/fotmob/models/types.py`, `src/fotmob/models/metadata.py`
  - Rotation: `src/fotmob/rotation/config.py`, `src/fotmob/rotation/types.py`, `src/fotmob/rotation/adapter.py`, `src/fotmob/rotation/analyzer.py`

# Related Docs
- Loader details — capture and replay — `src/fpl/fotmob/README.md` (to be moved/merged)
- Models — data types and metadata — `src/fotmob/models/README.md`
- Rotation — concepts and API — `src/fotmob/rotation/README.md`


