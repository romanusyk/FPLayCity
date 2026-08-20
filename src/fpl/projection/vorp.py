"""Value over replacement: the number that actually orders a draft board.

Why projected points is the wrong sort key
------------------------------------------
In a 4-manager league exactly 8 goalkeepers, 20 defenders, 20 midfielders and 12 forwards get
drafted. What a pick costs you is not the player's points - it is the points you give up by
not taking him, measured against whoever will still be sitting there when you come back. For
2026/27 GW1-10 the first undrafted player at each position projects at roughly 32 (GKP), 33
(DEF), 37 (MID) and 26 (FWD).

Replacement midfielder is eleven points better than replacement forward over ten gameweeks, so
a 40-point forward is worth far more than a 40-point midfielder. FPL classifies most attackers
as midfielders, which is why real number nines are the scarce resource and why the board must
be sorted by the gap, not the total.

Live drafting
-------------
Replacement level moves as the board empties, but not the way it first looks. Taking the four
best forwards shrinks the pool and the picks still to come by four apiece, so the player who
will be taken last is unchanged and replacement level holds. What moves it is a *reach*: every
time someone spends a pick below the line, one fewer pick is left to reach down that far, and
replacement level - along with every surviving forward's VORP - goes up.

`replacement_levels` takes the already-drafted set so the board can be recomputed mid-draft
rather than showing pre-draft numbers all night.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from src.fpl.models.immutable import PlayerType
from src.fpl.projection.engine import PlayerProjection


logger = logging.getLogger(__name__)


DRAFT_SQUAD_SLOTS: dict[PlayerType, int] = {
    PlayerType.GKP: 2,
    PlayerType.DEF: 5,
    PlayerType.MID: 5,
    PlayerType.FWD: 3,
}
"""Roster slots per manager in FPL Draft. Fifteen players, no per-club limit."""

DRAFT_STARTING_SLOTS: dict[PlayerType, int] = {
    PlayerType.GKP: 1,
    PlayerType.DEF: 4,
    PlayerType.MID: 4,
    PlayerType.FWD: 2,
}
"""Slots that actually score in a typical eleven, within the 1 GK / 3 DEF / 1 FWD minimum.

Which of the two tables to price against is not a detail, and roster slots give the wrong
answer. Eight keepers get drafted in a four-manager league, so pricing against roster slots
measures a starting keeper against the *ninth*-best keeper - a backup who will not play, worth
about 22 points. That made three goalkeepers top-ten picks on VORP, which is nonsense: your
second keeper scores you nothing, so a keeper's real value is the gap to the best keeper you
would otherwise start.

Measured on the 2026/27 board, switching to starting slots moves goalkeeper replacement level
from 21.9 to 29.8 and barely moves the outfield positions, which is exactly the distortion it was
meant to remove: no keeper now appears in the top twenty by VORP, where three did before.
"""

DEFAULT_MANAGERS = 4


@dataclass(frozen=True)
class ReplacementLevel:
    """What a position's replacement-level player is worth, and who they are.

    `remaining_picks` is the honest denominator: it is how many players at this position are
    still expected to come off the board, and it shrinks as the draft runs.
    """

    position: PlayerType
    points: float
    player_id: int | None
    web_name: str | None
    remaining_picks: int
    pool_size: int

    @property
    def is_exhausted(self) -> bool:
        """True when the undrafted pool is shallower than the picks still to be made.

        Replacement then falls to the worst player available rather than an interpolated one,
        and the VORP figures are correspondingly less meaningful.
        """
        return self.pool_size <= self.remaining_picks

    def as_dict(self) -> dict:
        return {
            'position': self.position.name,
            'points': round(self.points, 2),
            'player_id': self.player_id,
            'web_name': self.web_name,
            'remaining_picks': self.remaining_picks,
            'pool_size': self.pool_size,
            'is_exhausted': self.is_exhausted,
        }


def replacement_levels(
    projections: list[PlayerProjection],
    managers: int = DEFAULT_MANAGERS,
    drafted: set[int] | None = None,
    slots: dict[PlayerType, int] | None = None,
) -> dict[PlayerType, ReplacementLevel]:
    """Compute replacement level per position over the undrafted pool.

    Parameters:
    - projections: every projected player, drafted or not.
    - managers: managers in the league. Sets how many players get taken.
    - drafted: player ids already off the board. None means a pre-draft board.
    - slots: slots per manager per position. Defaults to `DRAFT_STARTING_SLOTS` (1/4/4/2), not
      the roster shape - see that constant for why.

    Returns:
    - Position -> `ReplacementLevel`.

    Raises:
    - ValueError: if a position has no projected players at all, which would make its
      replacement level undefined rather than merely low.
    """
    drafted = drafted or set()
    slots = slots or DRAFT_STARTING_SLOTS

    levels: dict[PlayerType, ReplacementLevel] = {}
    for position, per_manager in slots.items():
        pool = sorted(
            (p for p in projections if p.position is position),
            key=lambda p: -p.points,
        )
        if not pool:
            raise ValueError(
                f"No projected players at {position.name}. Replacement level is undefined; the "
                f"projection is incomplete."
            )
        available = [p for p in pool if p.player_id not in drafted]
        taken_here = sum(1 for p in pool if p.player_id in drafted)
        remaining = max(0, per_manager * managers - taken_here)

        if not available:
            raise ValueError(
                f"Every projected {position.name} is already drafted. Replacement level is "
                f"undefined."
            )
        # The player who will still be free after the remaining picks at this position.
        index = min(remaining, len(available) - 1)
        replacement = available[index]
        levels[position] = ReplacementLevel(
            position=position,
            points=replacement.points,
            player_id=replacement.player_id,
            web_name=replacement.web_name,
            remaining_picks=remaining,
            pool_size=len(available),
        )
        if levels[position].is_exhausted:
            logger.warning(
                "%s pool (%d available) is shallower than the %d picks still expected; "
                "replacement level has bottomed out.",
                position.name, len(available), remaining,
            )
    return levels


def value_over_replacement(
    projection: PlayerProjection,
    levels: dict[PlayerType, ReplacementLevel],
) -> float:
    """Projected points minus this position's replacement level.

    Raises:
    - KeyError: for a position with no computed level, so a manager slot cannot be silently
      priced against zero.
    """
    if projection.position not in levels:
        raise KeyError(
            f"No replacement level for {projection.position.name}. Positions computed: "
            f"{[p.name for p in levels]}"
        )
    return projection.points - levels[projection.position].points


def tier_breaks(sorted_vorp: list[float], max_tiers: int = 6) -> list[int]:
    """Indices where the board steps down, for grouping picks into tiers.

    Tiers are the practical output of a draft board: inside one, take whoever you like; between
    two, never reach. The breaks are the largest gaps between consecutive VORP values, which is
    a one-dimensional clustering by the only feature that matters.

    Returns:
    - Sorted start indices of each tier after the first. Empty for a list of fewer than two.
    """
    if len(sorted_vorp) < 2:
        return []
    gaps = sorted(
        ((sorted_vorp[i] - sorted_vorp[i + 1], i + 1) for i in range(len(sorted_vorp) - 1)),
        key=lambda pair: -pair[0],
    )
    return sorted(index for _, index in gaps[: max_tiers - 1])
