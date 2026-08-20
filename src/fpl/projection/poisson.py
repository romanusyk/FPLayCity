"""Poisson helpers shared by the projection models.

Three FPL scoring rules are step functions on a count - saves in threes, goals conceded in
twos, defensive actions against a threshold - and for all three the naive shortcut is wrong in
the same direction. `E[floor(saves / 3)]` is not `E[saves] / 3`: a keeper averaging 3.0 saves
scores 0.66 points, not 1.0, because the floor throws away the remainder every match. Over ten
gameweeks that is a three-point error on a single component.

Poisson is an approximation for all of these - real counts are mildly overdispersed - but it
is the right shape, and it is one line rather than a distributional model per statistic.
"""
from __future__ import annotations

import math


MAX_TAIL_TERMS = 40
"""Terms to sum before truncating. At realistic FPL lambdas the omitted mass is below 1e-12."""


def probabilities(mean: float, terms: int = MAX_TAIL_TERMS):
    """Yield `(k, P(X = k))` for `X ~ Poisson(mean)`, k from 0 upward."""
    if mean < 0:
        raise ValueError(f"Poisson mean must be >= 0, got {mean}")
    probability = math.exp(-mean)
    for k in range(terms):
        yield k, probability
        probability *= mean / (k + 1)


def tail(threshold: int, mean: float) -> float:
    """P(X >= threshold) for `X ~ Poisson(mean)`."""
    if threshold <= 0:
        return 1.0
    if mean <= 0:
        return 0.0
    below = sum(probability for k, probability in probabilities(mean) if k < threshold)
    return max(0.0, min(1.0, 1.0 - below))


def expected_floor_div(mean: float, divisor: int) -> float:
    """`E[floor(X / divisor)]` for `X ~ Poisson(mean)`.

    Raises:
    - ValueError: for a non-positive divisor.
    """
    if divisor <= 0:
        raise ValueError(f"divisor must be positive, got {divisor}")
    if mean <= 0:
        return 0.0
    return sum((k // divisor) * probability for k, probability in probabilities(mean))
