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

from src.fpl.projection.defensive import (
    DEFAULT_ACTIONS_SHRINKAGE_MINUTES,
    DEFAULT_SHRINKAGE_STARTS,
)

OPPONENT_DC_ACTIONS_MINUTES = 450.0
"""Actions-per-90 prior for v5: five matches, the same weight `rate_shrinkage_minutes` uses for
xG and xA. Not fitted - a judgement, with `v5-dc-actions-shrinkage` as its control.

Four starts (360 minutes) then carry 44% weight against the position mean instead of 100%, and a
full season (2,600 minutes) carries 85%, so a real sample is barely touched."""

OPPONENT_DC_SHRINKAGE_STARTS = 12.0
"""DC prior weight, in starts, for v5. Not fitted - a judgement, with `v5-dc-shrinkage` as its
control.

At the old 5.0 a player with four starts gets 4/(4+5) = 44% weight on an observed hit rate drawn
from four matches, which is how a defender with one month of Premier League football reached the
top of a waiver board. At 12.0 those four starts carry 25%, and a full season (34 starts) still
carries 74%, so a real sample is barely touched. The cost is being slower to notice a genuine
change in a player's defensive role."""
from src.fpl.projection.minutes import (
    CURRENT_SEASON_FULL_MATCHES,
    DEFAULT_PRESEASON_FRIENDLY_WEIGHT,
    DEFAULT_PRESEASON_WEIGHT,
    PRESEASON_ROLE_KNOTS,
    PRESEASON_TRUST_WEIGHT,
)
from src.fpl.projection.rates import SHRINKAGE_MINUTES
from src.fpl.projection.strength import DEFAULT_CURRENT_PRIOR_MATCHES, DEFAULT_SHRINKAGE_MATCHES


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
    role_evidence_before_gameweek: int | None = None
    """Deadline that closes the role-evidence window. None means the projection's own start.

    Every match stored before this gameweek's deadline feeds `build_preseason_roles`, weighted by
    kind - a friendly at `preseason_friendly_weight`, a competitive fixture at 1.0. Before GW1 the
    two settings are identical, which is why this only became a choice once a gameweek had been
    played:

    - `None` uses everything up to the first projected gameweek, so a GW2-11 run counts GW1's real
      line-ups at five times a friendly. That is the same mechanism that put Raya back above Kepa
      off one Community Shield, applied to better evidence.
    - An int pins the window. `1` is the pre-season window, which is the configuration every
      constant in `src/fpl/projection/minutes.py` was fitted in.

    Which is better is not yet measured - one gameweek is not a fit - so `v3-role-preseason-only`
    exists as the control.
    """
    current_season_full_matches: float | None = None
    """Played gameweeks at which the recent-matches term fully replaces last season's start share.

    None keeps the fitted pre-season behaviour at every point in the season, which is what every
    method coined before 2026-08-26 meant and is therefore what they keep. See
    `current_season_ramp` in `src/fpl/projection/minutes.py`.
    """
    strength_current_prior_matches: float | None = None
    """Current-season matches at which this season weighs as much as all of last season, in the
    club ratings. None ignores the current season, which is the historical behaviour. See
    `TeamStrength` in `src/fpl/projection/strength.py`."""
    dc_actions_shrinkage_minutes: float = DEFAULT_ACTIONS_SHRINKAGE_MINUTES
    """Prior weight, in minutes, pulling defensive actions per 90 toward the position average.

    The counterpart of `rate_shrinkage_minutes`, which has always done this for xG and xA. 0.0 is
    the historical behaviour and an asymmetry rather than a decision: a four-start sample of 12.5
    actions implied a hit rate of 0.80 and was believed outright.
    """
    honour_suspension_return: bool = False
    """Read "Suspended until <date>" out of FPL's news text and end the suppression there.

    A ban is a known number of matches. Applied to a whole horizon it cost Foden roughly 25 points
    of a GW5-14 projection and had a waiver board recommending his sale. See
    `src/fpl/projection/availability.py`.
    """
    honour_injury_return: bool = False
    """The same for "Expected back <date>". Weaker evidence than a ban - FPL's estimate of a
    medical timeline, and a returning player may not walk back into the side - which is why it is a
    separate switch from the suspension one.
    """
    status_return_role_share: float = 1.0
    """Share of his normal role a player is credited with from the return gameweek on.

    1.0 says he walks straight back in. Right for a ban; optimistic for an injury. **Not fitted** -
    one season holds too few long absences - so it is a judgement, and the two switches above let
    the injury case be turned off entirely rather than discounted by a guessed number.
    """
    adjust_history_for_opponent: bool = False
    """Divide each measured rate by the opponent strength it was earned against.

    A player's per-90 rates and defensive actions describe the schedule he actually played. Left
    raw, a defender who piled up actions against the division's best attacks carries that number
    into an average fixture. See `src/fpl/projection/opponent.py` for the fitted elasticities.
    False reproduces every method coined before 2026-09-17.
    """
    opponent_elasticity_forward: bool = False
    """Apply a fixture's opponent multiplier at the metric's fitted elasticity, not at 1.0.

    The engine multiplied xG by the full multiplier, which assumes output is exactly proportional
    to how weak the defence is. Measured, a midfielder's xG moves at about half that rate and a
    forward's not at all. False keeps the old full-strength multiplication.

    Pairs with `adjust_history_for_opponent`: with both on, past and future use the same exponent,
    which is the only self-consistent setting. Either alone is a control, not a recommendation.
    """
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
    'v3-role-preseason-only': ProjectionMethod(
        name='v3-role-preseason-only',
        notes=(
            'Control for v3-role-trust once a gameweek has been played: role evidence is cut off '
            'at the GW1 deadline, so played gameweeks are ignored and the minutes blend runs in '
            'exactly the configuration its constants were fitted in. The default counts GW1 '
            'line-ups at five times a friendly instead. Diff the two to see what the played '
            'gameweeks are doing; before GW1 the pair are identical.'
        ),
        params=BASELINE.replace(discount_transfers=True, role_evidence_before_gameweek=1),
    ),
    'v4-form-minutes': ProjectionMethod(
        name='v4-form-minutes',
        notes=(
            'v3-role-trust plus a ramp on the minutes blend: the weight on recent matches walks '
            'from the fitted pre-season curve to 1.0 over the first five gameweeks, so who a '
            'manager has actually been picking takes over from last season\'s start share as the '
            'evidence accumulates. Inert before GW1. One parameter away from v3-role-trust.'
        ),
        params=BASELINE.replace(
            discount_transfers=True, current_season_full_matches=CURRENT_SEASON_FULL_MATCHES,
        ),
    ),
    'v4-form-strength': ProjectionMethod(
        name='v4-form-strength',
        notes=(
            'v3-role-trust plus current-season results in the club ratings: this season is blended '
            'with last at n/(n+5), so five matches weigh as much as all of last season. Fixes the '
            'blind spot where a club that has genuinely changed keeps a year-old rating. One '
            'parameter away from v3-role-trust.'
        ),
        params=BASELINE.replace(
            discount_transfers=True,
            strength_current_prior_matches=DEFAULT_CURRENT_PRIOR_MATCHES,
        ),
    ),
    'v4-current-form': ProjectionMethod(
        name='v4-current-form',
        notes=(
            '**The default from GW2 2026/27 on.** Both v4 levers together: the minutes blend ramps '
            'toward the matches just played, and the club ratings blend this season with last. '
            'Neither constant is fitted - a ramp cannot be fitted on one gameweek - so both are '
            'stated judgements with v4-form-minutes and v4-form-strength as the single-lever '
            'controls, and v3-role-trust as the do-nothing baseline. Identical to v3-role-trust '
            'before GW1.'
        ),
        params=BASELINE.replace(
            discount_transfers=True,
            current_season_full_matches=CURRENT_SEASON_FULL_MATCHES,
            strength_current_prior_matches=DEFAULT_CURRENT_PRIOR_MATCHES,
        ),
    ),
    'v5-opponent-adjusted': ProjectionMethod(
        name='v5-opponent-adjusted',
        notes=(
            '**The default from GW5 2026/27 on.** Makes the projection opponent-aware in both '
            'directions. Every measured rate - xG, xA, bonus, saves and defensive actions - is '
            'divided by the opponent strength it was earned against, and every fixture multiplier '
            'is applied at that metric\'s fitted elasticity rather than at full strength. Also '
            f'raises DC shrinkage from {DEFAULT_SHRINKAGE_STARTS:.0f} to '
            f'{OPPONENT_DC_SHRINKAGE_STARTS:.0f} starts, so a hit rate off four matches no longer '
            'moves a board. Three levers, so v5-opponent-history, v5-opponent-forward and '
            'v5-dc-shrinkage isolate one each against v4-current-form.'
        ),
        params=BASELINE.replace(
            discount_transfers=True,
            current_season_full_matches=CURRENT_SEASON_FULL_MATCHES,
            strength_current_prior_matches=DEFAULT_CURRENT_PRIOR_MATCHES,
            adjust_history_for_opponent=True,
            opponent_elasticity_forward=True,
            dc_actions_shrinkage_minutes=OPPONENT_DC_ACTIONS_MINUTES,
        ),
    ),
    'v5-status-duration': ProjectionMethod(
        name='v5-status-duration',
        notes=(
            'v5-opponent-adjusted plus a read of how long a flagged player is actually out. FPL '
            'publishes it in plain text - "Suspended until 19 Oct", "Expected back 10 Oct" - and '
            'the model used to apply the flag to every gameweek in the horizon, so a two-match ban '
            'cost a player the whole run. Foden, GW5-14 on 2026-09-17: ~7 points projected against '
            'a true figure in the low thirties, and a waiver board recommending his sale. Players '
            'whose news says "Unknown return date" are unchanged. Two levers, isolated by '
            'v5-suspension-duration and v5-injury-duration.'
        ),
        params=BASELINE.replace(
            discount_transfers=True,
            current_season_full_matches=CURRENT_SEASON_FULL_MATCHES,
            strength_current_prior_matches=DEFAULT_CURRENT_PRIOR_MATCHES,
            adjust_history_for_opponent=True,
            opponent_elasticity_forward=True,
            dc_actions_shrinkage_minutes=OPPONENT_DC_ACTIONS_MINUTES,
            honour_suspension_return=True,
            honour_injury_return=True,
        ),
    ),
    'v5-suspension-duration': ProjectionMethod(
        name='v5-suspension-duration',
        notes=(
            'Control: only bans get a return date. The strongest case, because a suspension is a '
            'known number of matches rather than a medical estimate, and a player returning from '
            'one is fit. One parameter from v5-opponent-adjusted.'
        ),
        params=BASELINE.replace(
            discount_transfers=True,
            current_season_full_matches=CURRENT_SEASON_FULL_MATCHES,
            strength_current_prior_matches=DEFAULT_CURRENT_PRIOR_MATCHES,
            adjust_history_for_opponent=True,
            opponent_elasticity_forward=True,
            dc_actions_shrinkage_minutes=OPPONENT_DC_ACTIONS_MINUTES,
            honour_suspension_return=True,
        ),
    ),
    'v5-injury-duration': ProjectionMethod(
        name='v5-injury-duration',
        notes=(
            'Control: only injuries get a return date. The weaker case - "Expected back" is a '
            'medical estimate that slips, and a returning player may start on the bench, which '
            'status_return_role_share could discount but is not fitted. One parameter from '
            'v5-opponent-adjusted.'
        ),
        params=BASELINE.replace(
            discount_transfers=True,
            current_season_full_matches=CURRENT_SEASON_FULL_MATCHES,
            strength_current_prior_matches=DEFAULT_CURRENT_PRIOR_MATCHES,
            adjust_history_for_opponent=True,
            opponent_elasticity_forward=True,
            dc_actions_shrinkage_minutes=OPPONENT_DC_ACTIONS_MINUTES,
            honour_injury_return=True,
        ),
    ),
    'v5-opponent-history': ProjectionMethod(
        name='v5-opponent-history',
        notes=(
            'Control for v5: only the backward half. Measured rates are normalised by the opponents '
            'they were earned against, while fixtures are still projected at full strength. '
            'Deliberately inconsistent - past and future use different exponents - and it exists to '
            'show what the normalisation alone does. One parameter from v4-current-form.'
        ),
        params=BASELINE.replace(
            discount_transfers=True,
            current_season_full_matches=CURRENT_SEASON_FULL_MATCHES,
            strength_current_prior_matches=DEFAULT_CURRENT_PRIOR_MATCHES,
            adjust_history_for_opponent=True,
        ),
    ),
    'v5-opponent-forward': ProjectionMethod(
        name='v5-opponent-forward',
        notes=(
            'Control for v5: only the forward half. Fixture multipliers are applied at each '
            'metric\'s fitted elasticity instead of at 1.0, so the board stops over-reacting to an '
            'easy run, while rates stay as measured. One parameter from v4-current-form.'
        ),
        params=BASELINE.replace(
            discount_transfers=True,
            current_season_full_matches=CURRENT_SEASON_FULL_MATCHES,
            strength_current_prior_matches=DEFAULT_CURRENT_PRIOR_MATCHES,
            opponent_elasticity_forward=True,
        ),
    ),
    'v5-dc-actions-shrinkage': ProjectionMethod(
        name='v5-dc-actions-shrinkage',
        notes=(
            'Control for v5: only the actions-per-90 shrinkage, toward the position mean at '
            f'{OPPONENT_DC_ACTIONS_MINUTES:.0f} minutes. This is the lever that actually moves a '
            'thin sample: `implied_hit_rate` is a tail probability, so believing 12.5 actions off '
            'four starts overstates the hit rate far more than the raw average is out by. One '
            'parameter from v4-current-form.'
        ),
        params=BASELINE.replace(
            discount_transfers=True,
            current_season_full_matches=CURRENT_SEASON_FULL_MATCHES,
            strength_current_prior_matches=DEFAULT_CURRENT_PRIOR_MATCHES,
            dc_actions_shrinkage_minutes=OPPONENT_DC_ACTIONS_MINUTES,
        ),
    ),
    'v5-dc-shrinkage': ProjectionMethod(
        name='v5-dc-shrinkage',
        notes=(
            'Control, and a negative result worth keeping. Raises the *hit-rate* prior from '
            f'{DEFAULT_SHRINKAGE_STARTS:.0f} to {OPPONENT_DC_SHRINKAGE_STARTS:.0f} starts, which '
            'sounds like the fix for a four-start sample and is not: it shrinks the observed rate '
            'toward the *implied* one, and the implied one is computed from the same four starts. '
            'Muharemović\'s hit rate went 0.777 -> 0.786, the wrong way. Kept so nobody proposes '
            'it again. One parameter from v4-current-form.'
        ),
        params=BASELINE.replace(
            discount_transfers=True,
            current_season_full_matches=CURRENT_SEASON_FULL_MATCHES,
            strength_current_prior_matches=DEFAULT_CURRENT_PRIOR_MATCHES,
            dc_shrinkage_starts=OPPONENT_DC_SHRINKAGE_STARTS,
        ),
    ),
}


DEFAULT_METHOD = 'v5-status-duration'


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
