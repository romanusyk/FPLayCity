"""Process-wide state the request handlers share.

The app loads three things once at startup - the FPL collections, the per-match history join
and the stored feedback - because all three are read-only snapshots that take about a second to
build and would otherwise be rebuilt on every request.

Runs are deliberately *not* cached. They are small, they are read straight off disk, and a
generated run has to appear in the app without restarting it.
"""
from __future__ import annotations

import logging

from src.fpl.loader.load import load_from_snapshots
from src.fpl.loader.utils import Season, resolve_next_gameweek
from src.fpl.projection.feedback import FeedbackEntry, load_feedback
from src.fpl.projection.history import PlayerHistory, build_player_histories


logger = logging.getLogger(__name__)


class AppContext:
    """Everything loaded once, reachable from a handler.

    Held as a module-level singleton rather than passed through FastAPI dependencies because
    there is exactly one of it per process and the alternative is threading a parameter through
    every route for no benefit.
    """

    _current: 'AppContext | None' = None

    def __init__(self, season: str | None = None, next_gameweek: int | None = None):
        self.season = season or Season.CURRENT
        self.next_gameweek = next_gameweek or resolve_next_gameweek(self.season)
        logger.info("Loading %s collections...", self.season)
        load_from_snapshots(self.season)
        logger.info("Building per-match history...")
        self.histories: dict[int, PlayerHistory] = build_player_histories(self.season)
        self._feedback: list[FeedbackEntry] | None = None

    @classmethod
    def current(cls) -> 'AppContext':
        """Return the loaded context.

        Raises:
        - RuntimeError: if the app has not started up. A handler reaching for an unloaded
          context means the startup hook did not run, which must be loud.
        """
        if cls._current is None:
            raise RuntimeError(
                "AppContext has not been initialised. src.web.serve builds it on startup."
            )
        return cls._current

    @classmethod
    def initialise(cls, season: str | None = None, next_gameweek: int | None = None) -> 'AppContext':
        cls._current = cls(season, next_gameweek)
        return cls._current

    @property
    def feedback(self) -> list[FeedbackEntry]:
        if self._feedback is None:
            self._feedback = load_feedback(self.season)
        return self._feedback

    def feedback_for(self, player_id: int) -> list[FeedbackEntry]:
        return [entry for entry in self.feedback if entry.player_id == player_id]

    def invalidate_feedback(self) -> None:
        """Drop the cache after a write, so the next read sees the new entry."""
        self._feedback = None
