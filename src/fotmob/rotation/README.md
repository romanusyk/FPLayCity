# Overview
Rotation analysis over FotMob data, joined to FPL entities. Provides per-player squad roles (starts/bench/unavailable) and rival substitution hints, filtered by league and gameweek timeline.

# Key Concepts
- **Gameweek timeline**: Map match `event_time` to the number of FPL deadlines passed at kickoff (GW-effective).
- **First-team threshold**: A *weighted* start ratio (default 80%) to classify first-team regulars. Check `evidence_is_friendly_only` before trusting it in pre-season.
- **Rival substitutions**: Track pairs of players swapping via subs to infer likely starters.
- **Match-kind weighting**: Friendlies count towards squad role at `RotationConfig.match_kind_weights[FRIENDLY]` (default `0.35`); competitive matches count `1.0`. A full pre-season therefore informs without outweighing real fixtures.
- **Unweighted availability**: Being listed unavailable is never discounted. An injury keeps a player out of a friendly exactly as it keeps them out of a league game, so `PlayerSquadRole.unavailable` is a raw count.
- **League filter**: `RotationConfig.included_leagues` is an optional hard allow-list. It now defaults to empty, meaning *all* leagues; set it to `["Premier League"]` to restore league-only behaviour.
- **Season-scoped team mapping**: FPL team ids are derived from the live `Teams` collection via `short_name`, never hardcoded. `FotmobAdapter(season=...)` picks the club roster; passing the wrong season would map clubs to each other's lineups.
- **Deterministic mapping**: Optional FotMob↔FPL player overrides, keyed on the season-stable `fpl_player_code`; hard failures for ambiguity. An override whose code is absent from the loaded squad has aged out and is skipped, counted and logged.
- **Match-quality floors**: A candidate must score `MIN_MATCH_SCORE` (5) within a club, and `MIN_GLOBAL_MATCH_SCORE` (9) across the whole league. Sharing a first name scores 4, so the floors turn three academy players called Josh into an honest "no candidate" instead of an ambiguity error.
- **Unmatched players**: `allow_unmatched=True` records a FotMob player with no FPL counterpart and carries on instead of raising. Set it only for pre-season friendlies, where academy players who are not FPL elements appear in every lineup — 187 of them in 2026/27, all listed in `FotmobAdapter.unmatched_players` and logged. An *ambiguous* match still raises either way.

# Components
- Configuration:
  - `RotationConfig` and `PlayerMappingOverride` in `src/fotmob/rotation/rotation_config.py`
- Views (data structures for inspection and summaries):
  - `PlayerAppearance`, `PlayerSquadRole`, `RivalSubDetail`, `RivalStartHint` in `src/fotmob/rotation/rotation_view.py`
- Core logic:
  - `FotmobAdapter` (bridges FotMob matches to FPL ids, performs name matching and applies overrides) in `src/fotmob/rotation/fotmob_adapter.py`
  - `RotationAnalyzer` and `GwMapper` are referenced by the adapter and live with rotation code (`src/fpl/models/rotation.py`).

# Data/Control Flow
1) Build deadlines → `GwMapper`: `build_gameweek_mapper(gameweeks)` returns a callable to map `event_time` to GW-effective.
2) Adapter setup:
   - Validate FotMob team names → ids (using metadata).
   - Derive the FPL team id ↔ FotMob team id mapping for the season from `short_name`; fail on gaps in either direction.
   - Drop overrides whose `fpl_player_code` is not in the loaded squad, logging what was skipped.
   - Construct name token indices (team-scoped then global) for deterministic FotMob↔FPL player matching; apply overrides and enforce uniqueness.
3) Analyze:
   - `RotationAnalyzer` assigns GW-effective, captures player appearances tagged with their match-kind weight, records substitutions, and computes squad role metrics and rival histories.
4) Consume:
   - `get_player_squad_role(fpl_player_id, max_gameweek)` and `get_rival_start_hint(fpl_player_id, max_gameweek)` expose per-player views aligned with an FPL gameweek snapshot.

# Public API
- Timeline:
  - `build_gameweek_mapper(gameweeks) -> GwMapper` in `src/fotmob/rotation/fotmob_adapter.py`
- Team mapping:
  - `fpl_team_id_to_fotmob_name(season=None) -> dict[int, str]` in `src/fotmob/rotation/fotmob_adapter.py`
- Adapter (FPL-facing):
  - `FotmobAdapter(match_details_by_team_name, rotation_config, gw_mapper, overrides=None, season=None, allow_unmatched=False)`
  - `.unmatched_players -> list[tuple[int, str]]` — populated only when `allow_unmatched`
  - `get_fotmob_player_id(fpl_player_id) -> int`
  - `get_fpl_player_id_from_fotmob(fotmob_player_id) -> int`
  - `get_player_squad_role(fpl_player_id, max_gameweek) -> PlayerSquadRole`
  - `get_rival_start_hint(fpl_player_id, max_gameweek) -> RivalStartHint`
- Views:
  - `PlayerSquadRole`: `.starts`, `.competitive_starts`, `.friendly_starts`, `.benched`, `.unavailable`, `.total_matches`, `.weighted_starts`, `.weighted_matches`, `.start_ratio` (weighted), `.raw_start_ratio` (unweighted), `.is_first_team`, `.evidence_is_friendly_only`, `.appearances`
  - `PlayerAppearance`: `.status`, `.match`, `.weight`, `.kind`
  - `RivalStartHint`: `.rivals_sorted`, `.rivals_unlikely_to_start`, `.rivals_likely_to_start`

# Key Paths
- Config: `src/fotmob/rotation/rotation_config.py`
- Views: `src/fotmob/rotation/rotation_view.py`
- Adapter: `src/fotmob/rotation/fotmob_adapter.py`
- Analyzer: `src/fpl/models/rotation.py`
- This doc: `src/fotmob/rotation/README.md`

# Related Docs
- FotMob models — data types and team metadata — `src/fotmob/models/README.md`
- Loader overview — data capture and replay details — `src/fotmob/README.md`
- Pre-season rollup built on this adapter, and what friendlies are worth — `src/fpl/projection/README.md`


