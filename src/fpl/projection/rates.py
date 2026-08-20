"""Per-90 rates for the things a player does when he is on the pitch.

Division of labour
------------------
`PlayerSeason` totals are the source for rates - xG, xA, saves, bonus, cards - because they
cover the full 38 gameweeks and follow a transferred player to his new club. Per-match history
is the source for *distributions* - the defensive-contribution hit rate, minutes per start -
because a mean cannot answer those. `history.py` and `defensive.py` own that side.

Shrinkage
---------
A striker with 300 minutes and one lucky goal is not a 0.9 xG-per-90 player. Every rate is
pulled toward its position's league average with a weight in minutes, so small samples regress
and full seasons barely move. The position averages are measured from the prior season at load
time rather than hardcoded, so they follow the league rather than a comment written in 2025.

Absence, and why the league average is the wrong fallback
---------------------------------------------------------
A player with no prior Premier League season used to get the position average unchanged. That is
how Coventry's Ellis Simms - who has never played a Premier League minute - came out projected as
an average Premier League forward at 0.445 xG per 90, fifth on the draft board.

The league average is an average *over clubs*, and a promoted club is not an average club.
Measured over 2025/26, players in their first Premier League season at a promoted club, with
900+ minutes:

| position | xG/90, promoted debutants | xG/90, everyone else | ratio |
|---|---|---|---|
| FWD | 0.308 | 0.419 | **0.74** |
| MID | 0.104 | 0.159 | 0.65 |
| DEF | 0.048 | 0.058 | 0.83 |

So the fallback is scaled by the club's own attack rating, which for a promoted club is 0.73 -
almost exactly the measured forward ratio, and derived rather than hand-tuned. Attacking rates
and bonus scale; saves deliberately do not, because a weak club's keeper faces *more* shots.

`sample_minutes` of 0 still travels with the estimate, and the projection still carries a
`no_prior_season` flag. This is a better placeholder, not a prediction.

Worth keeping in view: only a third of those promoted-club debutants reached 900 minutes at all,
and the median was 38. The minutes model, not this one, is what has to catch that.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from src.fpl.models.immutable import PlayerSeason, PlayerType


logger = logging.getLogger(__name__)


SHRINKAGE_MINUTES = 450.0
"""Prior weight, in minutes, pulling a rate toward the position average. Five full matches."""

MIN_MINUTES_FOR_POSITION_AVERAGE = 900.0
"""Only players with a real season contribute to the position averages. Ten full matches."""

RATE_FIELDS = ('xg', 'xa', 'saves', 'bonus', 'yellow', 'red')

CLUB_SCALED_FIELDS = ('xg', 'xa', 'bonus')
"""Fallback rates that scale with the club's attacking strength.

`saves` is excluded on purpose - a weaker club concedes more shots, so its keeper makes more
saves, and scaling that down would be backwards. Cards do not depend on club quality either.
"""


@dataclass(frozen=True)
class PlayerRates:
    """Per-90 rates for one player, already shrunk.

    `bonus` is per 90 rather than per start: it is a smooth quantity and treating it that way
    keeps it consistent with the other rates. The projection multiplies every rate by expected
    minutes, so the units line up.
    """

    position: PlayerType
    xg: float
    xa: float
    saves: float
    bonus: float
    yellow: float
    red: float
    sample_minutes: float
    shrinkage_weight: float

    @property
    def xgi(self) -> float:
        return self.xg + self.xa

    def as_dict(self) -> dict:
        return {
            'xg_per_90': round(self.xg, 3),
            'xa_per_90': round(self.xa, 3),
            'saves_per_90': round(self.saves, 2),
            'bonus_per_90': round(self.bonus, 3),
            'yellow_per_90': round(self.yellow, 3),
            'red_per_90': round(self.red, 4),
            'sample_minutes': int(self.sample_minutes),
            'shrinkage_weight': round(self.shrinkage_weight, 3),
        }


class RateModel:
    """Shrunk per-90 rates, calibrated against one completed season.

    Parameters:
    - prior_seasons: every `PlayerSeason` row for the season being used as evidence.
    - shrinkage_minutes: prior weight in minutes.

    Raises:
    - ValueError: if a position has no players above the minutes floor, which would leave its
      average undefined and silently zero every rate for that position.
    """

    def __init__(self, prior_seasons: list[PlayerSeason], shrinkage_minutes: float = SHRINKAGE_MINUTES):
        self._shrinkage = shrinkage_minutes
        self.position_averages = self._build_position_averages(prior_seasons)

    @staticmethod
    def _raw_rates(season: PlayerSeason) -> dict[str, float]:
        """Per-90 rates straight from one season's totals, unshrunk."""
        nineties = season.minutes / 90.0
        if nineties <= 0:
            return {name: 0.0 for name in RATE_FIELDS}
        return {
            'xg': season.expected_goals / nineties,
            'xa': season.expected_assists / nineties,
            'saves': season.saves / nineties,
            'bonus': season.bonus / nineties,
            'yellow': season.yellow_cards / nineties,
            'red': season.red_cards / nineties,
        }

    def _build_position_averages(self, prior_seasons: list[PlayerSeason]) -> dict[PlayerType, dict[str, float]]:
        """Minutes-weighted league average rate per position, from established players only."""
        totals: dict[PlayerType, dict[str, float]] = {}
        minutes: dict[PlayerType, float] = {}
        for season in prior_seasons:
            if season.minutes < MIN_MINUTES_FOR_POSITION_AVERAGE:
                continue
            player = season.player
            position = player.player_type
            if position is PlayerType.MNG:
                continue
            bucket = totals.setdefault(position, {name: 0.0 for name in RATE_FIELDS})
            nineties = season.minutes / 90.0
            for name, rate in self._raw_rates(season).items():
                bucket[name] += rate * nineties
            minutes[position] = minutes.get(position, 0.0) + nineties

        averages: dict[PlayerType, dict[str, float]] = {}
        for position in (PlayerType.GKP, PlayerType.DEF, PlayerType.MID, PlayerType.FWD):
            if not minutes.get(position):
                raise ValueError(
                    f"No {position.name} played {MIN_MINUTES_FOR_POSITION_AVERAGE:.0f}+ minutes in "
                    f"the evidence season, so its average rates are undefined. The prior-season "
                    f"baseline is probably empty - run: uv run -m src.fpl.fetch --baseline-only"
                )
            averages[position] = {
                name: value / minutes[position] for name, value in totals[position].items()
            }
        logger.info(
            "Position rate averages (per 90): %s",
            {
                position.name: f"xg={rates['xg']:.3f} xa={rates['xa']:.3f} bonus={rates['bonus']:.3f}"
                for position, rates in averages.items()
            },
        )
        return averages

    def estimate(
        self,
        position: PlayerType,
        prior_season: PlayerSeason | None,
        club_attack: float = 1.0,
    ) -> PlayerRates:
        """Shrink one player's rates toward their position's average.

        Parameters:
        - prior_season: None for a player with no Premier League record. A club-scaled position
          average is returned with `sample_minutes=0` so the caller can flag it.
        - club_attack: the club's attack rating from `TeamStrength`, 1.0 being league average.
          Applied *only* to the no-evidence fallback: a player with real minutes already has his
          club's quality baked into his own rate, and scaling again would double-count.
        """
        average = self.position_averages[position]
        if prior_season is None or prior_season.minutes <= 0:
            return PlayerRates(
                position=position,
                sample_minutes=0.0,
                shrinkage_weight=0.0,
                **{
                    name: average[name] * (club_attack if name in CLUB_SCALED_FIELDS else 1.0)
                    for name in RATE_FIELDS
                },
            )

        weight = prior_season.minutes / (prior_season.minutes + self._shrinkage)
        raw = self._raw_rates(prior_season)
        return PlayerRates(
            position=position,
            sample_minutes=float(prior_season.minutes),
            shrinkage_weight=weight,
            **{
                name: weight * raw[name] + (1.0 - weight) * average[name]
                for name in RATE_FIELDS
            },
        )
