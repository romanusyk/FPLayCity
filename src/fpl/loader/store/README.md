# Overview
Manages timestamped JSON snapshots for FPL data resources. Provides a single-snapshot storage pattern where each resource maintains only the latest snapshot file, automatically cleaning up older versions. Supports freshness checks and async fetch-or-load workflows.

# Key Concepts
- **Snapshot naming**: Files follow `<base_path>_<ISO8601_timestamp>.json` format (e.g., `bootstrap_2025-12-04T16:53:35.json`).
- **Single snapshot**: Only the latest snapshot is retained; older snapshots are deleted when new ones are written.
- **Freshness window**: Days-based freshness check determines if existing snapshots need refresh.
- **News loader reuse**: The Premier League news fetcher (`src/fpl/loader/news/pl.py`) loads gameweek metadata by calling `JsonSnapshotStore(SnapshotSpec(base_path="data/<season>/bootstrap"))` (no HTTP call).
- **SnapshotSpec**: Immutable configuration specifying the base path (without timestamp) for a resource.

# Components
- `SnapshotSpec` in `src/fpl/loader/store/json.py`: Dataclass holding the base path for snapshot files.
- `JsonSnapshotStore` in `src/fpl/loader/store/json.py`: Main class managing snapshot lifecycle:
  - `build_filename(dt)`: Constructs timestamped filename from base path.
  - `list_all()`: Discovers all snapshots matching the base path pattern.
  - `find_latest()`: Returns the most recent snapshot (by timestamp).
  - `is_up_to_date(dt, days)`: Checks if a snapshot is within freshness window.
  - `load_latest()`: Loads the latest snapshot JSON from disk.
  - `write(body, dt, delete_older)`: Writes new snapshot and optionally removes older ones.
  - `get_or_fetch(freshness, fetch_fn)`: Async helper that loads from cache if fresh, otherwise fetches and persists.

# Data/Control Flow
1. **Snapshot discovery**: `list_all()` scans the directory for files matching `<base_name>_<timestamp>.json`, parses timestamps, and returns sorted list.
2. **Freshness check**: `is_up_to_date()` compares snapshot age against freshness window (in days).
3. **Write with cleanup**: `write()` creates new timestamped file and removes all older snapshots if `delete_older=True`.
4. **Fetch-or-load**: `get_or_fetch()` checks latest snapshot freshness; if stale or missing, calls `fetch_fn()` and persists result.
5. **News flow**: `load_recent_news` calls `JsonSnapshotStore(...bootstrap).load_latest()` to hydrate `Gameweek` metadata, then fetches and stores raw articles in the hierarchical structure.

# Public API
- `SnapshotSpec(base_path: str)`: Configuration for snapshot base path.
- `JsonSnapshotStore(spec: SnapshotSpec)`: Store instance for a single resource.
- `store.get_or_fetch(freshness: int, fetch_fn: Callable[[], Awaitable[dict]]) -> dict`: Main entry point for cached fetch workflow.

# Key Paths
- Implementation: `src/fpl/loader/store/json.py`
- Public exports: `src/fpl/loader/store/__init__.py`

# Related Docs
- Loader overview — data fetching and snapshot coordination — `src/fpl/loader/README.md`.
- Premier League news loader — paging, storage layout, CLI usage — `src/fpl/loader/news/README.md`.
- Documentation standards — top‑down style and linking rules — `docs/metadoc.md`.

