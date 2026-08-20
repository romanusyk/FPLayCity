"""Season identifiers and filesystem helpers shared by the loaders.

`Season` is the single source of truth for which season the loaders read and write.
Rolling the project over to a new season is a one-line change to `Season.CURRENT`;
nothing else should hardcode a season string.
"""
import logging
import os
from datetime import datetime, timezone


logger = logging.getLogger(__name__)


class Season:
    """Season directory names under `data/`, ordered oldest to newest.

    Key invariants:
    - Values match the directory names in `data/` exactly.
    - `ORDERED` is chronological; `previous()` relies on that ordering.
    - `CURRENT` is the only place the active season is declared.
    """

    s2425 = '2024-2025'
    s2526 = '2025-2026'
    s2627 = '2026-2027'

    ORDERED: tuple[str, ...] = (s2425, s2526, s2627)

    CURRENT: str = s2627

    @classmethod
    def previous(cls, season: str | None = None) -> str:
        """Return the season preceding `season` (defaults to `CURRENT`).

        Raises:
        - ValueError: if `season` is unknown, or is the earliest season we hold.
        """
        season = season or cls.CURRENT
        if season not in cls.ORDERED:
            raise ValueError(
                f"Unknown season '{season}'. Known seasons: {', '.join(cls.ORDERED)}. "
                f"Add it to Season.ORDERED before using it."
            )
        index = cls.ORDERED.index(season)
        if index == 0:
            raise ValueError(f"No season precedes '{season}' - it is the earliest season we hold.")
        return cls.ORDERED[index - 1]

    @classmethod
    def window(cls, season: str | None = None) -> tuple[datetime, datetime]:
        """Return the UTC [start, end) window a season's matches fall into.

        Runs 1 July to 1 July so that pre-season friendlies, which begin in early July, belong
        to the season they precede. Used to stop a provider's multi-season fixture feed from
        writing last season's matches into this season's directory.
        """
        season = season or cls.CURRENT
        if season not in cls.ORDERED:
            raise ValueError(f"Unknown season '{season}'. Known seasons: {', '.join(cls.ORDERED)}.")
        start_year = int(season.split('-')[0])
        return (
            datetime(start_year, 7, 1, tzinfo=timezone.utc),
            datetime(start_year + 1, 7, 1, tzinfo=timezone.utc),
        )

    @classmethod
    def as_fpl_history_name(cls, season: str) -> str:
        """Convert a directory name into the FPL API's `history_past.season_name` format.

        The FPL element-summary payload labels seasons `2025/26`, not `2025-2026`.
        """
        if season not in cls.ORDERED:
            raise ValueError(f"Unknown season '{season}'. Known seasons: {', '.join(cls.ORDERED)}.")
        start, end = season.split('-')
        return f"{start}/{end[2:]}"


def ensure_dir_exists(filepath: str) -> None:
    """Ensure the directory for the provided filepath exists."""
    directory = os.path.dirname(filepath)
    if directory and not os.path.exists(directory):
        os.makedirs(directory, exist_ok=True)


def resolve_next_gameweek(season: str | None = None) -> int:
    """Work out which gameweek is next, preferring `NEXT_GAMEWEEK` when it is set.

    Every entry point used to require `NEXT_GAMEWEEK` in `.env` and fail outright without it,
    which meant a fresh checkout could not fetch anything and the value had to be bumped by hand
    every week. The fixtures snapshot already knows the answer: the next gameweek is the earliest
    one with an unfinished fixture.

    Order of preference:
    1. `NEXT_GAMEWEEK` from the environment, so a deliberate override still wins.
    2. The earliest gameweek with an unfinished fixture in the stored fixtures snapshot.
    3. Gameweek 1, with a warning - the only case is a cold checkout with nothing on disk.

    Parameters:
    - season: season whose fixtures to read. Defaults to `Season.CURRENT`.

    Raises:
    - ValueError: if `NEXT_GAMEWEEK` is set to something that is not a positive integer. A typo
      there would otherwise silently fall through to the derived value.
    """
    raw = os.getenv('NEXT_GAMEWEEK')
    if raw:
        try:
            gameweek = int(raw)
        except ValueError as exc:
            raise ValueError(f"NEXT_GAMEWEEK is set to '{raw}', which is not an integer.") from exc
        if gameweek < 1:
            raise ValueError(f"NEXT_GAMEWEEK is set to {gameweek}; gameweeks start at 1.")
        return gameweek

    derived = _next_gameweek_from_fixtures(season or Season.CURRENT)
    if derived is not None:
        logger.info("NEXT_GAMEWEEK is not set; derived GW%d from the fixtures snapshot.", derived)
        return derived

    logger.warning(
        "NEXT_GAMEWEEK is not set and no fixtures snapshot is stored; assuming GW1. "
        "Fetch fixtures first, or set NEXT_GAMEWEEK in .env to override."
    )
    return 1


def _next_gameweek_from_fixtures(season: str) -> int | None:
    """Earliest gameweek with an unfinished fixture, or None if nothing is stored.

    Imported lazily because `JsonSnapshotStore` imports this module.
    """
    from src.fpl.loader.store import JsonSnapshotStore, SnapshotSpec

    store = JsonSnapshotStore(SnapshotSpec(base_path=f"data/{season}/fixtures"))
    if store.find_latest() is None:
        return None
    unfinished = [
        fixture['event'] for fixture in store.load_latest()
        if not fixture['finished'] and fixture['event'] is not None
    ]
    if not unfinished:
        # Every fixture is played. The season is over; there is no "next" gameweek to fetch for.
        return None
    return min(unfinished)
