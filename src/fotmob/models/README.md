# Overview
Core data models and metadata for FotMob entities used across data capture (loader) and rotation analysis. These Pydantic models standardize match details and team/player identifiers, ensuring downstream components fail fast on incomplete or inconsistent data.

# Key Concepts
- **Pydantic models**: Typed, validated DTOs for teams, players, substitutions, and match details.
- **Event time as datetime**: `MatchDetails.event_time` is a timezone-aware `datetime` used for gameweek mapping.
- **Team metadata**: Canonical FotMob team ids and names live in metadata to keep loaders/consumers deterministic.

# Components
- **Types (Pydantic models)**:
  - `FotmobTeam`, `FotmobPlayer`, `Substitution`, `MatchDetails` in `src/fpl/models/fotmob.py` (proposed: `src/fotmob/models/types.py`)
- **Metadata**:
  - `TEAMS` and `TEAM_NAME_TO_ID` in `src/fpl/models/fotmob_metadata.py` (proposed: `src/fotmob/models/metadata.py`)

# Data/Control Flow
- Data is captured by the loader (`src/fpl/fotmob/load.py`, proposed: `src/fotmob/load.py`), serialized per match, and reloaded into `MatchDetails`.
- Rotation components (adapter/analyzer) consume `MatchDetails` and team ids to compute squad roles and rival hints.

# Public API
- Data classes:
  - `FotmobTeam(id: int, name: str)`
  - `FotmobPlayer(id: int, name: str)`
  - `Substitution(time: int, player_out_injured: bool, player_out: FotmobPlayer, player_in: FotmobPlayer)`
  - `MatchDetails(match_id: int, event_time: datetime, opponent_team: FotmobTeam, starters: list[FotmobPlayer], benched: list[FotmobPlayer], unavailable: list[FotmobPlayer], subs_log: list[Substitution], league_name: str)`
- Metadata:
  - `TEAMS: dict[int, str]`, `TEAM_NAME_TO_ID: dict[str, int]`

# Key Paths
- Current:
  - Types: `src/fpl/models/fotmob.py`
  - Metadata: `src/fpl/models/fotmob_metadata.py`
- After refactor (proposed defaults):
  - Types: `src/fotmob/models/types.py` (rename from `fotmob.py`)
  - Metadata: `src/fotmob/models/metadata.py` (rename from `fotmob_metadata.py`)

# Related Docs
- Loader overview — acquisition and reading pipeline — `src/fotmob/README.md` (source currently at `src/fpl/fotmob/README.md`)
- Rotation module — squad roles, rivals, and API — `src/fotmob/rotation/README.md`


