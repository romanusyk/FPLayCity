"""Named projection methods.

A *method* is a name, a note saying what it changes, and a set of parameters. Naming them
matters more than it looks: the point of the web app is comparing runs, and a comparison
between "the projection" and "the projection, but different" is useless. `v1-baseline` versus
`v0-raw-dc` is an argument you can settle.

Two of the methods here exist purely as controls. `v0-raw-dc` turns off the empirical-Bayes
shrinkage on defensive contribution, and `v0-no-preseason` ignores friendlies. Running them
alongside the baseline shows exactly what those two decisions are worth, in ranks and, once the
gameweeks resolve, in points.

Adding a method
---------------
Add an entry to `METHODS`. If it needs a knob that does not exist yet, add a field to
`ProjectionParams` with a default that leaves existing methods unchanged - run artifacts record
the full parameter set, so an old run stays reproducible even as the dataclass grows.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, fields

from src.fpl.projection.defensive import DEFAULT_SHRINKAGE_STARTS
from src.fpl.projection.minutes import (
    DEFAULT_PRESEASON_FRIENDLY_WEIGHT,
    DEFAULT_PRESEASON_WEIGHT,
    PRESEASON_ROLE_KNOTS,
    PRESEASON_TRUST_WEIGHT,
)
from src.fpl.projection.rates import SHRINKAGE_MINUTES
from src.fpl.projection.strength import DEFAULT_SHRINKAGE_MATCHES


DRAFT = 'draft'
FPL = 'fpl'
GAMES = (DRAFT, FPL)
"""The two games. They are projected separately so their methods can diverge."""


@dataclass(frozen=True)
class ProjectionParams:
    """Every knob the engine reads. Serialised verbatim into each run artifact."""

    gameweek_from: int = 1
    gameweek_to: int = 10
    dc_shrinkage_starts: float = DEFAULT_SHRINKAGE_STARTS
    preseason_weight: float = DEFAULT_PRESEASON_WEIGHT
    preseason_friendly_weight: float = DEFAULT_PRESEASON_FRIENDLY_WEIGHT
    preseason_trust_weight: float = PRESEASON_TRUST_WEIGHT
    preseason_role_knots: tuple[float, float, float] | None = PRESEASON_ROLE_KNOTS
    """Pre-season weight by last season's start share. None falls back to the flat weight."""
    use_preseason: bool = True
    team_shrinkage_matches: float = DEFAULT_SHRINKAGE_MATCHES
    rate_shrinkage_minutes: float = SHRINKAGE_MINUTES
    adjust_for_club_change: bool = True
    discount_transfers: bool = False

    @property
    def horizon(self) -> int:
        return self.gameweek_to - self.gameweek_from + 1

    def replace(self, **changes) -> 'ProjectionParams':
        """Return a copy with `changes` applied.

        Raises:
        - TypeError: on an unknown field name, so a typo in a CLI override cannot be ignored.
        """
        known = {field.name for field in fields(self)}
        unknown = set(changes) - known
        if unknown:
            raise TypeError(
                f"Unknown projection parameter(s): {sorted(unknown)}. Known: {sorted(known)}"
            )
        return ProjectionParams(**{**asdict(self), **changes})

    def as_dict(self) -> dict:
        """JSON-ready parameters.

        `preseason_role_knots` is widened to a list: a run artifact is compared field by field
        against the in-memory body it was written from, and a tuple that comes back as a list
        breaks that equality for no reason anyone would enjoy debugging.
        """
        params = asdict(self)
        if params['preseason_role_knots'] is not None:
            params['preseason_role_knots'] = list(params['preseason_role_knots'])
        return params


@dataclass(frozen=True)
class ProjectionMethod:
    """A named, described parameter set."""

    name: str
    notes: str
    params: ProjectionParams

    def as_dict(self) -> dict:
        return {'name': self.name, 'notes': self.notes, 'params': self.params.as_dict()}


BASELINE = ProjectionParams()

FLAT_ROLE = BASELINE.replace(preseason_role_knots=None)
"""The pre-2026-08-18 blend: one pre-season weight for everyone.

Every method that existed before the role curve is pinned to this, so a run generated today
under an old method name still means what the name meant when it was coined - otherwise every
stored comparison silently changes its subject.
"""

METHODS: dict[str, ProjectionMethod] = {
    'v1-baseline': ProjectionMethod(
        name='v1-baseline',
        notes=(
            'Full component model: measured minutes blend, empirical-Bayes defensive '
            'contribution, Poisson clean sheets and concessions, shrunk per-90 rates.'
        ),
        params=FLAT_ROLE,
    ),
    'v0-raw-dc': ProjectionMethod(
        name='v0-raw-dc',
        notes=(
            'Control. Identical to v1-baseline except the defensive-contribution hit rate is '
            'taken raw instead of shrunk toward the rate-implied prior.'
        ),
        params=FLAT_ROLE.replace(dc_shrinkage_starts=0.0),
    ),
    'v0-no-preseason': ProjectionMethod(
        name='v0-no-preseason',
        notes=(
            'Control. Identical to v1-baseline except pre-season friendlies are ignored, so '
            'p_start rests on last season alone.'
        ),
        params=FLAT_ROLE.replace(use_preseason=False, preseason_weight=0.0),
    ),
    'v2-transfer': ProjectionMethod(
        name='v2-transfer',
        notes=(
            'v1-baseline plus a transfer discount: last season\'s start share is scaled down '
            'for a player who has changed club, harder the bigger the step up. Nailed starters '
            'who moved up realised 0.40 of a start share in 2025/26 GW1-5 against 0.74 for '
            'those who stayed.'
        ),
        params=FLAT_ROLE.replace(discount_transfers=True),
    ),
    'v2-transfer-no-preseason': ProjectionMethod(
        name='v2-transfer-no-preseason',
        notes=(
            'The transfer discount without the pre-season signal. Answers the question "is a '
            'new signing overrated" using last season alone, for when the friendly sample is '
            'too thin to trust.'
        ),
        params=FLAT_ROLE.replace(
            discount_transfers=True, use_preseason=False, preseason_weight=0.0
        ),
    ),
    'v3-role-trust': ProjectionMethod(
        name='v3-role-trust',
        notes=(
            'v2-transfer plus the pre-season role curve: how much pre-season outweighs last '
            'season now depends on last season. A nailed starter who stayed put keeps his prior '
            '(pre-season absence is rest); a squad player is judged almost entirely on '
            'pre-season (it is the manager deciding an open place). Cuts start-share error 5% '
            'overall and 13% on nailed starters. Movers are exempt.'
        ),
        params=BASELINE.replace(discount_transfers=True),
    ),
    'v3-role-trust-flat': ProjectionMethod(
        name='v3-role-trust-flat',
        notes=(
            'Control for v3-role-trust: the same method with one flat pre-season weight for '
            'everyone. Identical to v2-transfer; kept under its own name so the comparison '
            'screen shows the role curve as the single difference.'
        ),
        params=FLAT_ROLE.replace(discount_transfers=True),
    ),
}


DEFAULT_METHOD = 'v3-role-trust'


def method(name: str) -> ProjectionMethod:
    """Look up a method by name.

    Raises:
    - KeyError: for an unknown name, listing what is available.
    """
    if name not in METHODS:
        raise KeyError(
            f"Unknown projection method '{name}'. Available: {', '.join(sorted(METHODS))}. "
            f"Add new ones to METHODS in src/fpl/projection/methods.py."
        )
    return METHODS[name]
