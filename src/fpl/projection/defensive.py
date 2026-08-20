"""Defensive contribution as a shrunk hit rate.

The award is a step function
----------------------------
`defensive_contribution` in the FPL history is a raw action count - clearances, blocks,
interceptions and tackles for defenders, plus recoveries for everyone else. Clear 10 (defender)
or 12 (everyone else) in a match and you get +2; clear 9 and you get nothing. A model that
multiplies a season-average action count by a per-action rate, which is what
`Player.dc_points` does today, is wrong in form as well as scale.

Why not the raw hit rate either
-------------------------------
Two teammates can average the same actions and hit the threshold at very different rates -
Alderete 36% and Ballard 67% on 10.2 and 10.6 actions per match. That gap is real. Splitting
each player's 2025/26 starts into odd and even matches (players with 20+ starts, GW1-30):

| | hit rate | mean actions |
|---|---|---|
| DEF (51 players) | r = 0.80 | r = 0.87 |
| MID (49 players) | r = 0.68 | r = 0.81 |

Hit rate repeats, so it is a property of the player and not noise. But the *mean* repeats
better, which means an observed hit rate on a small sample is the noisier of the two
estimators. Neither wins outright, so we combine them: shrink the observed hit rate toward the
rate-implied one, weighted by how many starts we actually saw. Small sample, trust the rate;
large sample, trust what happened. `docs/webapp_plan.md` section 1 covers the minutes-confound
control that got us here.

Components
----------
- `DefensiveEstimate`: what the model believes, and every input behind it.
- `DefensiveContributionModel`: builds estimates from per-match history.
"""
from __future__ import annotations

from dataclasses import dataclass

from src.fpl.models.immutable import PlayerType
from src.fpl.projection import poisson
from src.fpl.projection.history import PlayerHistory
from src.fpl.projection.scoring import (
    DEFENSIVE_CONTRIBUTION_POINTS,
    defensive_contribution_threshold,
)


DEFAULT_SHRINKAGE_STARTS = 5.0
"""Prior weight `k`, expressed in starts, for shrinking the observed hit rate.

Derived from the split-half table above rather than picked by taste. Spearman-Brown turns the
half-sample correlations into full-sample reliabilities of 0.89 (DEF) and 0.81 (MID), and at
the ~26-start median the weight `n / (n + k)` reproduces those at `k = n(1 - R) / R` = 3.2 and
6.1. Five splits the difference. Override per method via `ProjectionParams.dc_shrinkage_starts`
- it is exactly the sort of number the run comparison screen exists to argue about.
"""

MIN_STARTS_FOR_OBSERVED_RATE = 3
"""Below this, the observed hit rate carries no independent information worth blending."""


def implied_hit_rate(threshold: int, actions_per_90: float, minutes_per_start: float) -> float:
    """The hit rate a player's action *rate* implies, as a Poisson tail.

    This is the prior that an observed hit rate gets shrunk toward. Poisson is an
    approximation, and a measured one: across 2025/26 players with 20+ starts it correlates
    0.96 with the observed hit rate and runs about two percentage points low (DEF 0.272
    predicted vs 0.290 actual; MID 0.177 vs 0.197), which is the signature of mild
    overdispersion. That bias is diluted as soon as a player has real starts behind them.
    """
    return poisson.tail(threshold, actions_per_90 * minutes_per_start / 90.0)


@dataclass(frozen=True)
class DefensiveEstimate:
    """Per-start probability of clearing the defensive-contribution threshold.

    Every field is surfaced in the web app's player detail, because the whole point of the
    shrinkage is that you can see what it did and disagree with it.
    """

    position: PlayerType
    threshold: int
    starts: int
    hits: int
    actions_per_start: float
    actions_per_90: float
    minutes_per_start: float
    observed_hit_rate: float | None
    implied_hit_rate: float
    hit_rate: float
    shrinkage_weight: float

    @property
    def points_per_start(self) -> float:
        return self.hit_rate * DEFENSIVE_CONTRIBUTION_POINTS

    @property
    def has_award(self) -> bool:
        """False for goalkeepers, who cannot earn the award at all."""
        return self.threshold > 0

    def as_dict(self) -> dict:
        return {
            'threshold': self.threshold,
            'starts': self.starts,
            'hits': self.hits,
            'actions_per_start': round(self.actions_per_start, 2),
            'actions_per_90': round(self.actions_per_90, 2),
            'minutes_per_start': round(self.minutes_per_start, 1),
            'observed_hit_rate': (
                round(self.observed_hit_rate, 3) if self.observed_hit_rate is not None else None
            ),
            'implied_hit_rate': round(self.implied_hit_rate, 3),
            'hit_rate': round(self.hit_rate, 3),
            'shrinkage_weight': round(self.shrinkage_weight, 3),
        }


class DefensiveContributionModel:
    """Estimate a player's per-start chance of clearing the defensive threshold.

    Parameters:
    - shrinkage_starts: prior weight `k`. 0 disables shrinkage and returns the raw observed
      hit rate, which is what the `v1-raw-dc` method uses so the two can be compared.
    - assumed_minutes_per_start: fallback used when a player has no starts on record, so the
      rate-implied prior still has a minutes figure to scale by.
    """

    def __init__(
        self,
        shrinkage_starts: float = DEFAULT_SHRINKAGE_STARTS,
        assumed_minutes_per_start: float = 80.0,
    ):
        if shrinkage_starts < 0:
            raise ValueError(f"shrinkage_starts must be >= 0, got {shrinkage_starts}")
        self._k = shrinkage_starts
        self._assumed_minutes = assumed_minutes_per_start

    def estimate(self, position: PlayerType, history: PlayerHistory) -> DefensiveEstimate:
        """Build an estimate from every start on record.

        A player with no starts gets `hit_rate` 0.0 with `starts=0`. That is not a claim that
        they never clear the threshold - it is the absence of evidence, and callers show the
        sample size alongside the number so the difference is visible.
        """
        threshold = defensive_contribution_threshold(position)
        starts = [match for match in history.starts() if match.minutes > 0]

        if not starts or threshold == 0:
            return DefensiveEstimate(
                position=position,
                threshold=threshold,
                starts=len(starts),
                hits=0,
                actions_per_start=0.0,
                actions_per_90=0.0,
                minutes_per_start=self._assumed_minutes if not starts else (
                    sum(m.minutes for m in starts) / len(starts)
                ),
                observed_hit_rate=None,
                implied_hit_rate=0.0,
                hit_rate=0.0,
                shrinkage_weight=0.0,
            )

        total_actions = sum(match.defensive_contribution for match in starts)
        total_minutes = sum(match.minutes for match in starts)
        hits = sum(1 for match in starts if match.cleared_defensive_threshold(position))

        actions_per_start = total_actions / len(starts)
        actions_per_90 = total_actions / total_minutes * 90.0
        minutes_per_start = total_minutes / len(starts)

        implied = implied_hit_rate(threshold, actions_per_90, minutes_per_start)
        observed = hits / len(starts) if len(starts) >= MIN_STARTS_FOR_OBSERVED_RATE else None

        if observed is None:
            hit_rate, weight = implied, 0.0
        elif self._k == 0:
            hit_rate, weight = observed, 1.0
        else:
            weight = len(starts) / (len(starts) + self._k)
            hit_rate = weight * observed + (1.0 - weight) * implied

        return DefensiveEstimate(
            position=position,
            threshold=threshold,
            starts=len(starts),
            hits=hits,
            actions_per_start=actions_per_start,
            actions_per_90=actions_per_90,
            minutes_per_start=minutes_per_start,
            observed_hit_rate=observed,
            implied_hit_rate=implied,
            hit_rate=hit_rate,
            shrinkage_weight=weight,
        )
