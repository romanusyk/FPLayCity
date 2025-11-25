# Overview
Rotation analysis over FotMob data, joined to FPL entities. Provides per-player squad roles (starts/bench/unavailable) and rival substitution hints, filtered by league and gameweek timeline.

# Key Concepts
- **Gameweek timeline**: Map match `event_time` to the number of FPL deadlines passed at kickoff (GW-effective).
- **First-team threshold**: A start ratio (default 80%) to classify first-team regulars.
- **Rival substitutions**: Track pairs of players swapping via subs to infer likely starters.
- **League filter**: Only process configured leagues (default: Premier League).
- **Deterministic mapping**: Optional FotMob↔FPL player overrides; hard failures for ambiguity or missing data.

# Components
- Configuration:
  - `RotationConfig` and `PlayerMappingOverride` in `src/fpl/models/rotation_config.py` (proposed: `src/fotmob/rotation/config.py`)
- Views (data structures for inspection and summaries):
  - `PlayerAppearance`, `PlayerSquadRole`, `RivalSubDetail`, `RivalStartHint` in `src/fpl/models/rotation_view.py` (proposed: `src/fotmob/rotation/types.py`)
- Core logic:
  - `FotmobAdapter` (bridges FotMob matches to FPL ids, performs name matching and applies overrides) in `src/fpl/models/fotmob_adapter.py` (proposed: `src/fotmob/rotation/adapter.py`)
  - `RotationAnalyzer` and `GwMapper` are referenced by the adapter and live with rotation code (current: `src/fpl/models/rotation.py`, proposed: `src/fotmob/rotation/analyzer.py`).

# Data/Control Flow
1) Build deadlines → `GwMapper`: `build_gameweek_mapper(gameweeks)` returns a callable to map `event_time` to GW-effective.
2) Adapter setup:
   - Validate FotMob team names → ids (using metadata).
   - Build FPL team id ↔ FotMob team id mapping; fail on gaps.
   - Construct name token indices (team-scoped then global) for deterministic FotMob↔FPL player matching; apply overrides and enforce uniqueness.
3) Analyze:
   - `RotationAnalyzer` filters matches by league, assigns GW-effective, captures player appearances and substitutions, and computes squad role metrics and rival histories.
4) Consume:
   - `get_player_squad_role(fpl_player_id, max_gameweek)` and `get_rival_start_hint(fpl_player_id, max_gameweek)` expose per-player views aligned with an FPL gameweek snapshot.

# Public API
- Timeline:
  - `build_gameweek_mapper(gameweeks) -> GwMapper` in `src/fpl/models/fotmob_adapter.py` (proposed: `src/fotmob/rotation/adapter.py`)
- Adapter (FPL-facing):
  - `get_fotmob_player_id(fpl_player_id) -> int`
  - `get_fpl_player_id_from_fotmob(fotmob_player_id) -> int`
  - `get_player_squad_role(fpl_player_id, max_gameweek) -> PlayerSquadRole`
  - `get_rival_start_hint(fpl_player_id, max_gameweek) -> RivalStartHint`
- Views:
  - `PlayerSquadRole`: `.starts`, `.benched`, `.unavailable`, `.total_matches`, `.start_ratio`, `.is_first_team`, `.appearances`
  - `RivalStartHint`: `.rivals_sorted`, `.rivals_unlikely_to_start`, `.rivals_likely_to_start`

# Key Paths
- Current:
  - Config: `src/fpl/models/rotation_config.py`
  - Views: `src/fpl/models/rotation_view.py`
  - Adapter: `src/fpl/models/fotmob_adapter.py`
  - Timeline doc: `src/fpl/models/README_rotation.md`
- After refactor (proposed defaults):
  - Config: `src/fotmob/rotation/config.py` (rename from `rotation_config.py`)
  - Views: `src/fotmob/rotation/types.py` (rename from `rotation_view.py`)
  - Adapter: `src/fotmob/rotation/adapter.py` (rename from `fotmob_adapter.py`)
  - Analyzer: `src/fotmob/rotation/analyzer.py` (rename from `rotation.py`)
  - This doc: `src/fotmob/rotation/README.md`

# Related Docs
- FotMob models — data types and team metadata — `src/fotmob/models/README.md`
- Loader overview — data capture and replay details — `src/fotmob/README.md` (source currently at `src/fpl/fotmob/README.md`)


