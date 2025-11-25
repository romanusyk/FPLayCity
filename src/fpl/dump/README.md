# Overview
Utilities to materialize lightweight, analysis‑friendly dumps from saved Fantasy Premier League snapshots. This module converts raw API snapshots under `data/<season>/*` into simple CSV/TXT/JSON artifacts for quick inspection and downstream tools. It follows the project policy of failing loudly on missing inputs instead of silently skipping data.

# Key Concepts
- **Seasoned data roots**: Operates on snapshots in `data/<season>/bootstrap` and `data/<season>/fixtures` (named `response_body_<ISO8601>.json`).
- **Latest snapshot discovery**: Defaults to the most recent timestamped file; explicit paths can be provided to override.
- **Fail‑loudly**: Missing directories/files raise immediately; parsing assumes complete data (no best‑effort skipping).
- **Output formats**:
  - FDR: `fdr.csv` + duplicate `fdr.txt` and aggregated `fdr.json`
  - Players: `players.csv` + duplicate `players.txt`
- **Scope controls**: FDR export can be restricted to a gameweek range via `--first-gw`/`--last-gw`.
- **Normalization details**:
  - Team short names and positions are derived from `bootstrap`.
  - Player `now_cost` is rescaled from tenths to decimal price.

# Components
- `find_latest_file` in `src/fpl/dump/fdr.py` and `src/fpl/dump/players.py`: common helper to select the newest `response_body_*.json`.
- FDR exports (in `src/fpl/dump/fdr.py`):
  - `find_latest_season_files(season)`: resolves latest `fixtures` and `bootstrap` for a season.
  - `load_bootstrap_data(path)`: maps team id → short name from `bootstrap`.
  - `dump_fdr(fixtures_path, bootstrap_path, first_gw, last_gw)`: produces per‑team, per‑GW rows with difficulty and opponent.
  - `generate_json_format(fdr_data)`: aggregates per team with `average_fdr` and compact fixture strings.
  - `dump_fdr_csv(...)`: writes `fdr.csv`, copies to `fdr.txt`, and writes `fdr.json`.
  - `main()`: CLI entry point.
- Player exports (in `src/fpl/dump/players.py`):
  - `find_latest_bootstrap_file(season)`: resolves latest `bootstrap`.
  - `load_position_mapping(bootstrap_data)`, `load_team_mapping(bootstrap_data)`: id → label maps.
  - `get_numeric_fields(player)`: extracts numeric/boolean metrics (keeps `None`) while excluding ids and non‑metrics.
  - `dump_players(bootstrap_path)`: emits normalized player rows (name, position, team, price, availability + metrics).
  - `dump_players_csv(...)`: writes `players.csv`, copies to `players.txt`.
  - `main()`: CLI entry point.

# Data/Control Flow
1) Resolve inputs
   - FDR: latest `fixtures` + `bootstrap` for a season (or use provided paths).
   - Players: latest `bootstrap` for a season (or provided path).
2) Load supporting maps from `bootstrap` (teams, positions).
3) Transform
   - FDR: expand each fixture into two rows (home/away), optional GW filtering; prepare score if available.
   - Players: normalize core identity fields and merge numeric metrics.
4) Write outputs to `data/<season>/dumps/`
   - FDR: `fdr.csv`, `fdr.txt` (copy), `fdr.json` (aggregated format).
   - Players: `players.csv`, `players.txt` (copy).

# Public API
- FDR exports:
  - `dump_fdr(fixtures_path: str, bootstrap_path: str, first_gw: int | None = None, last_gw: int | None = None) -> list[dict]` in `src/fpl/dump/fdr.py`
  - `dump_fdr_csv(fixtures_path: str, bootstrap_path: str, first_gw: int | None = None, last_gw: int | None = None) -> None` in `src/fpl/dump/fdr.py`
- Player exports:
  - `dump_players(bootstrap_path: str) -> list[dict]` in `src/fpl/dump/players.py`
  - `dump_players_csv(bootstrap_path: str) -> None` in `src/fpl/dump/players.py`
- CLIs:
  - `python -m src.fpl.dump.fdr [fixtures_path] [bootstrap_path] [--first-gw N] [--last-gw M]`
  - `python -m src.fpl.dump.players [bootstrap_path]`

# Key Paths
- FDR: `src/fpl/dump/fdr.py`
- Players: `src/fpl/dump/players.py`
- Output directory: `data/<season>/dumps/`
- Input snapshots: `data/<season>/fixtures/response_body_*.json`, `data/<season>/bootstrap/response_body_*.json`

# Related Docs
- Loader overview — how snapshots are captured and organized on disk — `src/fpl/loader/README.md`.
- Documentation standards — top‑down style and linking rules — `docs/metadoc.md`.


