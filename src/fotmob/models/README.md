# Overview
Core data models and metadata for FotMob entities used across data capture (loader) and rotation analysis. These Pydantic models standardize match details and team/player identifiers, ensuring downstream components fail fast on incomplete or inconsistent data.

# Key Concepts
- **Pydantic models**: Typed, validated DTOs for teams, players, substitutions, and match details.
- **Event time as datetime**: `MatchDetails.event_time` is a timezone-aware `datetime` used for gameweek mapping.
- **Match kind**: `MatchKind` separates competitive fixtures from friendlies. Friendlies are noisy evidence of a manager's first-choice XI but are the *only* evidence in pre-season, so they are kept and down-weighted rather than filtered out.
- **Lineup-less friendlies**: FotMob regularly publishes friendly results with no lineup. `MatchDetails.lineup_available` models that as a state instead of an exception. A *competitive* match with no lineup still raises.
- **Per-season rosters**: FotMob team ids are stable per club; Premier League membership is not. `SEASON_TEAMS` records which clubs played in which season, so historic `lineups/` directories keep resolving after relegation.

# Components
- **Types**: `MatchKind`, `FotmobTeam`, `FotmobPlayer`, `Substitution`, `MatchDetails`, and `classify_match_kind` in `src/fotmob/models/fotmob.py`
- **Metadata**: `FOTMOB_TEAM_IDS`, `FPL_SHORT_NAMES`, `SEASON_TEAMS`, `teams_for_season`, `team_name_to_id_for_season`, `validate_against_fpl` in `src/fotmob/models/fotmob_metadata.py`

# Data/Control Flow
- The loader (`src/fotmob/load.py`) captures raw payloads per match, then `_build_match_details` classifies the competition and validates into `MatchDetails`.
- Rotation components consume `MatchDetails` plus `MatchKind` to weight appearance evidence.
- `validate_against_fpl(bootstrap["teams"], season)` reconciles our roster against the FPL bootstrap. A missed promotion surfaces as an error rather than as silently absent lineups.

# Public API
- `MatchKind`: `COMPETITIVE` | `FRIENDLY`
- `classify_match_kind(league_name: str) -> MatchKind` — anything not in `FRIENDLY_LEAGUE_NAMES` is competitive
- `MatchDetails(match_id, event_time, opponent_team, starters, benched, unavailable, subs_log, league_name, kind, lineup_available=True)`
  - `.is_friendly -> bool`
- `teams_for_season(season=None) -> dict[int, str]` — `{fotmob_team_id: club_name}`
- `team_name_to_id_for_season(season=None) -> dict[str, int]`
- `validate_against_fpl(fpl_teams: list[dict], season=None) -> None` — raises `ValueError` on mismatch
- `TEAMS` (current season, id→name) and `TEAM_NAME_TO_ID` (all seasons, name→id) remain available

# Key Paths
- Types: `src/fotmob/models/fotmob.py`
- Metadata: `src/fotmob/models/fotmob_metadata.py`

# Related Docs
- Loader overview — acquisition and reading pipeline — `src/fotmob/README.md`
- Rotation module — weighted squad roles, rivals, and API — `src/fotmob/rotation/README.md`
