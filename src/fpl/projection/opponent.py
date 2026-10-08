"""How much of a player's record was the opponent, and how much was the player.

The asymmetry this fixes
------------------------
Before this module the projection was opponent-aware in one direction only. Future fixtures were
adjusted per opponent and venue (`attack_multiplier` in `src/fpl/projection/strength.py`), while
the *rates being multiplied* were raw per-90 numbers taken at face value regardless of who the
player had faced. A defender who posted 12.5 defensive actions a match against the league's most
dangerous attacks carried that number forward unchanged into an average fixture.

So each rate is now divided by the opponent strength it was earned against, leaving a rate against
a neutral opponent, which the fixture multiplier then scales back up. Past and future finally use
the same units.

The exponent is not 1.0, and that is the important part
-------------------------------------------------------
The engine multiplied xG by the full opponent multiplier, which assumes a player's output is
exactly proportional to how weak the defence is. Measured over 5,087 starts across 2025/26 and
2026/27 (2026-09-17), it is not:

    metric   position   ratio weak/strong D   elasticity
    xg       DEF        1.19                  0.58
    xg       MID        1.16                  0.51
    xg       FWD        0.97                 -0.11
    xa       DEF        1.10                  0.31
    xa       MID        1.08                  0.28
    xa       FWD        1.04                  0.13
    bonus    DEF        1.28                  0.83
    bonus    MID        1.16                  0.51
    bonus    FWD        1.32                  0.93
    dc       DEF        1.04                  0.10
    dc       MID        1.08                  0.23
    dc       FWD        1.04                  0.11
    saves    GKP        1.11                  0.28

`elasticity` is `ln(metric ratio) / ln(multiplier ratio)`: 1.0 means the metric moves exactly in
proportion to opponent strength, 0.0 means opponent strength does not move it at all. Two readings
matter. A midfielder's xG moves at about *half* the rate the engine assumed, so the old model
over-reacted to fixtures. And a forward's xG does not move with opponent defence at all - a
striker's chances come from his own team creating them - so adjusting a forward's xG would be
adding noise, and the elasticity is clamped to zero.

Defensive actions barely move (0.10 for defenders). That deserves a caveat, because a coarser cut
of the same data looks much stronger: the *hit rate* against the 10-action threshold goes from 20%
to 31% between weak and strong attacks. Both are true. A threshold crossing amplifies a small shift
in the mean, which is exactly why the adjustment is applied to `actions_per_90` and the threshold
is re-applied afterwards through `implied_hit_rate`, rather than being applied to the hit rate
directly.

What this is not
----------------
Fitted on one and a bit seasons, on a median split rather than a regression, with venue folded into
the multiplier but not into the split. The numbers are stated judgements with a measurement behind
them, not settled constants - which is why `v5-opponent-history` and `v5-opponent-forward` exist as
single-lever controls against `v4-current-form`.

One known mismatch: a player's rate comes from season *totals* while his exposure comes from stored
per-match rows, and 2025/26's rows stop at GW30 (see `CLAUDE.md`). The exposure is therefore a mean
over 30 matches standing in for a mean over 38. For an average-opponent estimate that is harmless;
it would not be if it were a sum.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass

from src.fpl.models.immutable import PlayerType
from src.fpl.projection.history import PlayerHistory


logger = logging.getLogger(__name__)


ATTACK_SIDE = 'attack_side'
"""Axis for what a player *does to* the opponent: their defence, times venue. Matches
`TeamStrength.attack_multiplier`, so a normalised rate and a fixture multiplier are in the same
units."""

PRESSURE_SIDE = 'pressure_side'
"""Axis for what the opponent *does to* the player: their attack, times venue. Drives defensive
actions and saves - both are work created by the other team."""

METRIC_AXIS = {
    'xg': ATTACK_SIDE,
    'xa': ATTACK_SIDE,
    'bonus': ATTACK_SIDE,
    'saves': PRESSURE_SIDE,
    'dc': PRESSURE_SIDE,
}
"""Which opponent rating moves each metric. Cards are absent on purpose: no adjustment."""

_NONE = {position: 0.0 for position in PlayerType}

OPPONENT_ELASTICITY: dict[str, dict[PlayerType, float]] = {
    'xg': {**_NONE, PlayerType.DEF: 0.58, PlayerType.MID: 0.51},
    'xa': {**_NONE, PlayerType.DEF: 0.31, PlayerType.MID: 0.28, PlayerType.FWD: 0.13},
    'bonus': {**_NONE, PlayerType.DEF: 0.83, PlayerType.MID: 0.51, PlayerType.FWD: 0.93},
    'saves': {**_NONE, PlayerType.GKP: 0.28},
    'dc': {**_NONE, PlayerType.DEF: 0.10, PlayerType.MID: 0.23, PlayerType.FWD: 0.11},
    'yellow': dict(_NONE),
    'red': dict(_NONE),
}
"""Fitted 2026-09-17 on 5,087 starts; see the module doc for the measurement.

Built from `_NONE` so every `PlayerType` is present, `PlayerType.MNG` included: a missing entry
would be a KeyError raised mid-projection, on one position, for one metric.

Clamped to [0, 1]. A forward's xG measured -0.11, which is noise around "no effect", and a negative
exponent would make a striker *better* against better defences. Goalkeeper `bonus` is 0.0 because it
was not measured: a keeper's bonus tracks clean sheets and saves, both of which the engine already
models against the opponent, so adjusting it again would double-count.
"""


@dataclass(frozen=True)
class OpponentExposure:
    """The average opponent a player's own record was earned against.

    Both fields are minutes-weighted means of the same multipliers the fixture projection uses, so
    dividing a rate by one of them yields a rate against a neutral opponent. 1.0 means the player
    faced an average schedule and nothing is adjusted away.
    """

    attack_side: float
    pressure_side: float
    minutes: float
    matches: int

    def axis(self, name: str) -> float:
        return self.attack_side if name == ATTACK_SIDE else self.pressure_side

    def as_dict(self) -> dict:
        return {
            'attack_side': round(self.attack_side, 3),
            'pressure_side': round(self.pressure_side, 3),
            'minutes': int(self.minutes),
            'matches': self.matches,
        }


NEUTRAL = OpponentExposure(attack_side=1.0, pressure_side=1.0, minutes=0.0, matches=0)
"""No evidence, so nothing to adjust. Returned for a player with no stored matches, and used by
every method that leaves the adjustment switched off."""


def normalise(rate: float, exposure: float, elasticity: float) -> float:
    """Strip the opponent out of a measured rate.

    `rate / exposure**elasticity`. At `elasticity` 0 the rate is returned untouched, which is how a
    metric with no measured opponent effect stays exactly as it was.
    """
    if elasticity <= 0.0 or exposure <= 0.0:
        return rate
    return rate / (exposure ** elasticity)


def apply(multiplier: float, elasticity: float) -> float:
    """Scale a fixture multiplier to the strength the metric actually responds at.

    The counterpart of `normalise`: `multiplier**elasticity`. At 0 it returns 1.0, so a metric with
    no opponent effect is projected flat across every fixture, however hard the run looks.
    """
    if elasticity <= 0.0:
        return 1.0
    if multiplier <= 0.0:
        return multiplier
    return multiplier ** elasticity


def build_exposures(
    histories: dict[int, PlayerHistory],
    ratings_by_season: dict[str, dict],
    home_advantage: float,
) -> dict[int, OpponentExposure]:
    """Mean opponent faced, per player, from every stored match.

    Parameters:
    - histories: per-player match rows, from `build_player_histories`.
    - ratings_by_season: season -> {short_name: TeamRating}. A season absent here contributes
      nothing, which is the honest handling for a season whose ratings cannot be built.
    - home_advantage: the same constant `TeamStrength` applies, so the venue term matches.

    Returns a mapping for every player in `histories`; those with no usable match get
    `NEUTRAL`, so a caller never has to special-case a missing entry.

    Matches whose opponent has no rating in that season are skipped and counted, not guessed at -
    the usual cause is a club that has since been relegated out of the ratings table.
    """
    home_term = math.sqrt(home_advantage)
    exposures: dict[int, OpponentExposure] = {}
    skipped = 0
    for player_id, history in histories.items():
        attack_total = pressure_total = minutes_total = 0.0
        matches = 0
        for match in history.matches:
            if match.minutes <= 0:
                continue
            ratings = ratings_by_season.get(match.season)
            if not ratings or match.opponent not in ratings:
                skipped += 1
                continue
            rating = ratings[match.opponent]
            venue = home_term if match.was_home else 1.0 / home_term
            weight = float(match.minutes)
            attack_total += rating.defence * venue * weight
            # Away from home the pressure is greater, so the venue term inverts.
            pressure_total += rating.attack / venue * weight
            minutes_total += weight
            matches += 1
        exposures[player_id] = OpponentExposure(
            attack_side=attack_total / minutes_total if minutes_total else 1.0,
            pressure_side=pressure_total / minutes_total if minutes_total else 1.0,
            minutes=minutes_total,
            matches=matches,
        ) if minutes_total else NEUTRAL
    if skipped:
        logger.info(
            "%d player-match(es) had no rating for the opponent and did not contribute to "
            "opponent exposure - usually a club relegated since.", skipped,
        )
    return exposures
