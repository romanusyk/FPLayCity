"""When a flagged player is actually back, rather than "not for the next ten gameweeks".

The bug this fixes
------------------
`status` is a single flag and `_availability` turned it into a single multiplier applied to every
fixture in the horizon. A two-match ban therefore cost a player the whole run. Measured on
2026-09-17, Foden was suspended for GW5 and GW6 and projected at `p_start` 0.00 and ~0.7 points for
all ten gameweeks of a GW5-14 horizon - roughly 7 points against a true figure in the low thirties,
for a fit Manchester City midfielder. A waiver board then recommended selling him, and the
recommendation was an artefact.

FPL publishes the duration, in plain text
-----------------------------------------
No language model is needed for this. `bootstrap-static` carries it in the `news` field:

    Suspended until 19 Oct          (4 players on 2026-10-08)
    Expected back 10 Oct            (25 players)
    Back injury - Unknown return date   (48 players)
    Groin injury - 75% chance of playing (41 players)

So a return *date* is available for 29 of the 118 flagged players today, and `Unknown return date`
is an explicit statement that FPL does not know - which is exactly the case where suppressing the
whole horizon is right. Nothing is inferred from silence: a player with no parseable date keeps the
old behaviour.

Resolving a bare date
---------------------
The text carries no year. A season runs August to May, so each month appears exactly once in it and
`19 Oct` is unambiguous once the season is known: months from July on belong to the opening year,
the rest to the closing one. The date is then mapped to the first gameweek whose *deadline* falls on
or after it, which is deliberately the conservative reading - a player back on the 19th has missed a
gameweek whose deadline was the 17th.

What this does not claim
------------------------
That a player returns to his previous role on the day he is available. For a suspension that is
close to certain; for an injury it is optimistic, and a returning player often starts on the bench.
`status_return_role_share` exists to discount that and is **not fitted** - one season carries too few
long absences to fit it - so the default is 1.0 and `v5-injury-duration` isolates the injury half
from the suspension half.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date, datetime


logger = logging.getLogger(__name__)


RETURN_PATTERNS = (
    re.compile(r'suspended until\s+(\d{1,2})\s+([A-Za-z]{3,9})', re.IGNORECASE),
    re.compile(r'expected back\s+(\d{1,2})\s+([A-Za-z]{3,9})', re.IGNORECASE),
)
"""The two phrasings FPL uses for a stated return. Anything else is treated as no date at all."""

SUSPENSION_MARKER = 'suspended until'
"""Distinguishes a ban from an injury in the same text, so the two can be controlled separately."""

MONTHS = {
    'jan': 1, 'feb': 2, 'mar': 3, 'apr': 4, 'may': 5, 'jun': 6,
    'jul': 7, 'aug': 8, 'sep': 9, 'oct': 10, 'nov': 11, 'dec': 12,
}

SEASON_OPENS_FROM_MONTH = 7
"""Months from July belong to the season's opening year; January to June to its closing year."""


@dataclass(frozen=True)
class ReturnWindow:
    """When a flagged player comes back, and whether the flag was a ban.

    `gameweek` is the first gameweek he is projected to be available for. None means no date was
    published, which keeps the whole-horizon suppression that was the only behaviour before.
    """

    gameweek: int | None
    on: date | None
    is_suspension: bool

    @property
    def known(self) -> bool:
        return self.gameweek is not None

    def as_dict(self) -> dict:
        return {
            'return_gameweek': self.gameweek,
            'return_on': self.on.isoformat() if self.on else None,
            'is_suspension': self.is_suspension,
        }


UNKNOWN = ReturnWindow(gameweek=None, on=None, is_suspension=False)
"""No published return. Not an assertion that the player is out forever - an absence of evidence,
and treated exactly as the model treated every flagged player before this module existed."""


def season_years(season: str) -> tuple[int, int]:
    """`'2026-2027'` -> `(2026, 2027)`.

    Raises:
    - ValueError: on a season string that is not two four-digit years, because guessing a year is
      how a return date lands twelve months out.
    """
    parts = season.split('-')
    if len(parts) != 2 or not all(part.isdigit() and len(part) == 4 for part in parts):
        raise ValueError(f"Season {season!r} is not of the form '2026-2027'.")
    return int(parts[0]), int(parts[1])


def parse_return_date(news: str, season: str) -> tuple[date | None, bool]:
    """Read a return date out of FPL's `news` text.

    Returns `(date, is_suspension)`. The date is None when the text carries no stated return,
    which includes the explicit `Unknown return date`.

    Raises nothing: unparseable text is a normal state, not an error. A month abbreviation FPL has
    never used would simply read as "no date", and the player keeps the conservative behaviour.
    """
    if not news:
        return None, False
    is_suspension = SUSPENSION_MARKER in news.lower()
    for pattern in RETURN_PATTERNS:
        match = pattern.search(news)
        if not match:
            continue
        day = int(match.group(1))
        month = MONTHS.get(match.group(2)[:3].lower())
        if month is None:
            logger.info("Unrecognised month in FPL news %r; treating as no stated return.", news)
            return None, is_suspension
        opening, closing = season_years(season)
        year = opening if month >= SEASON_OPENS_FROM_MONTH else closing
        try:
            return date(year, month, day), is_suspension
        except ValueError:
            logger.info("Impossible date in FPL news %r; treating as no stated return.", news)
            return None, is_suspension
    return None, is_suspension


def first_gameweek_from(return_on: date, deadlines: dict[int, datetime]) -> int | None:
    """The first gameweek whose deadline falls on or after `return_on`.

    Conservative on purpose: a player available on the 19th has missed a gameweek whose deadline
    was the 17th, even though some of its matches are played later.

    Returns None when every stored deadline is earlier, which means the return is past the end of
    the season as we know it - the caller then keeps the whole-horizon suppression.
    """
    candidates = [
        gameweek for gameweek, deadline in sorted(deadlines.items())
        if deadline.date() >= return_on
    ]
    return candidates[0] if candidates else None


def return_window(news: str, season: str, deadlines: dict[int, datetime]) -> ReturnWindow:
    """The full resolution: FPL's text to a gameweek this player is available from."""
    return_on, is_suspension = parse_return_date(news, season)
    if return_on is None:
        return ReturnWindow(gameweek=None, on=None, is_suspension=is_suspension)
    gameweek = first_gameweek_from(return_on, deadlines)
    return ReturnWindow(gameweek=gameweek, on=return_on, is_suspension=is_suspension)
