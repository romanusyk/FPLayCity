"""Will he play, and for how long.

Why this is the first model
---------------------------
Fifty-six percent of every FPL point awarded in 2025/26 was the appearance point. Goals, the
thing every model obsesses over, were fourteen. And appearance does not merely dominate the
total - it gates everything else, because a clean sheet needs 60 minutes and a defensive
contribution needs a start. Getting minutes roughly right matters more than getting expected
goals exactly right.

The dataset behind every number below
-------------------------------------
One season's transition, and it is worth knowing exactly how thin that is before trusting any
constant here.

**The prior and the target are FPL's own data, and they are solid.** The prior is 2024/25 starts
from `history_past`; the target is the GW1-5 2025/26 start share from `history`, both out of
`data/2025-2026/elements/*.json`. 822 elements, 533 carrying a 2024/25 row, 741 with GW1-5 rows.

**The pre-season signal is scraped, and it is partial.** `data/2025-2026/lineups/` holds 101
club-match records before the GW1 deadline - 90 friendlies, 11 competitive - covering all 20 clubs
at 2 to 8 matches each, mean 5.0. Clubs play more friendlies than that: behind-closed-doors and
untelevised matches are simply absent. "Started 0 of 4" therefore always means 0 of the 4 we
captured, which is why the club-evidence ramp below exists.

**The fitting population is 519 players** - non-managers with a 2024/25 row, a GW1-5 outcome, and a
club with pre-season evidence. 70 of them changed club.

**The target is coarse.** Five matches per player, so the outcome is quantised to 0, 0.2, ... 1.0.
That puts a floor under every MAE quoted here. The numbers are comparable to each other; they are
not comparable to a finer-grained study.

**The current season is thinner still where it matters most.** 2026/27 has 97 club-match records
but only **3** competitive ones, against 11 in 2025/26 - so `preseason_friendly_weight`, fitted on
those 11, is carrying more weight this year than the evidence for it really supports.

What no amount of care with this dataset can test:

- **A second year.** There is exactly one season on disk with stored pre-season lineups, so
  nothing here is validated across time. 2025 was also the first expanded Club World Cup summer,
  which is precisely the kind of one-off that could make a pre-season atypical. If the role curve
  is an artifact of that year rather than a fact about football, this data cannot tell us.
- **Whether a missing friendly means "not picked" or "not captured".** The club denominator
  handles the average case; it cannot handle a club whose captured matches happen to be the ones
  the first team sat out.

What predicts a start, measured
-------------------------------
Two signals are available in August, and blending them beats either alone. Backtested against
actual GW1-5 start share in 2025/26 (519 players with both signals):

| predictor | correlation | MAE |
|---|---|---|
| 2024/25 start share alone | 0.611 | 0.218 |
| pre-season *friendlies only* | 0.654 | 0.208 |
| pre-season incl. competitive fixtures | 0.687 | – |
| blend at `w = 0.60` | **0.758** | **0.191** |

Two things earned that improvement. Including the competitive pre-season fixtures - Community
Shield, Super Cup, Club World Cup - rather than only friendlies, and letting them outweigh
friendlies (see `ProjectionParams.preseason_friendly_weight`). The blend optimum is flat
between 0.5 and 0.7, so the exact value of `DEFAULT_PRESEASON_WEIGHT` is not worth arguing
about; what matters is that the fresher signal earns more than half the weight.

For the 222 players with pre-season minutes but no prior Premier League season, friendlies
alone still correlate 0.589 with GW1-5 starts. That is the cold-start case the whole exercise
exists for.

Thin evidence gets less trust
-----------------------------
Clubs range from two stored pre-season matches to eight. Trusting "started 1 of 1" as much as
"started 5 of 6" is obviously wrong, and it was: Mats Wieffer started 23 of 38 for Brighton last
season, started both of Brighton's two stored friendlies, and came out at a 0.84 start
probability.

An earlier attempt at this failed because it used the wrong shape and the wrong variable.
`w * n / (n + k)` on the *match count* made aggregate accuracy worse, because reliability is not
monotonic in match count - clubs with 3-4 stored matches predict better (r = 0.776) than clubs
with 5+ (r = 0.643), presumably because a long pre-season tour means more experimenting. Split
by club evidence, the best blend weight is 0.25 for clubs with 1-2 matches and 0.55-0.70 above
that.

What works is a ramp on evidence *weight*, `w * min(1, weight / PRESEASON_TRUST_WEIGHT)`. Keying
on weight rather than count means a single Community Shield reaches full trust on its own, which
is the right behaviour. Measured against a flat weight:

| | overall r | overall MAE | MAE on clubs with 1-2 matches |
|---|---|---|---|
| flat `w = 0.60` | 0.758 | 0.1914 | 0.1592 |
| ramp, `T = 0.75` | 0.759 | **0.1899** | **0.1263** |

A 21% error reduction on exactly the group that was wrong, and nothing given up elsewhere.

A nailed starter is a different question from a squad player
-----------------------------------------------------------
The blend weight above is flat, and that is wrong in a way that is obvious once you look at a
single case. Bruno Fernandes started 35 of 38 for United, stayed at United, is fit and takes
every set piece. He started 1 of 6 pre-season matches, so a flat `w = 0.60` put him at a 0.47
start probability - a rotation risk, ranked 46th, and *exactly* on the replacement line for a
midfielder.

The best blend weight is not flat. Split same-club players by last season's start share and fit
`w` inside each group (2025/26, 449 players):

| last season's start share | n | best `w` | what pre-season means for this group |
|---|---|---|---|
| under 0.30 | 246 | 0.3 | fringe: friendlies are played by whoever is left, so a start is cheap |
| 0.30 - 0.80 | 142 | **1.0** | genuine competition: pre-season *is* the manager's current verdict |
| 0.80 and above | 61 | **0.2** | nailed: absence is load management, not demotion |

A hump, not a ramp - and both ends matter for different reasons. `PRESEASON_ROLE_KNOTS`
interpolates `w` over prior start share, replacing the flat weight for players who stayed:

| | overall r | overall MAE | MAE, prior >= 0.80 | MAE, midfielders with prior >= 0.80 |
|---|---|---|---|---|
| flat `w = 0.60` | **0.759** | 0.1899 | 0.2327 | 0.2251 |
| knot curve | 0.760 | **0.1802** | **0.2022** | **0.1714** |

Five percent better overall, 13% on nailed starters, 24% on nailed midfielders, correlation
unchanged. Bruno goes from 0.47 to 0.71.

Two guards on it, both load-bearing:

- **Movers are excluded.** For a player who changed club the pre-season is the *only* observation
  of the new squad, and the fit agrees emphatically: over the 70 movers, MAE falls monotonically
  as `w` rises, from 0.355 at `w = 0` to 0.242 at `w = 1.0`. Applying the nailed-starter discount
  to them would delete the transfer model. They keep the flat weight.

  An open disagreement worth knowing about: in the cross-validation below, **all 20 folds chose
  1.0 for movers**, not the 0.60 shipped here. That is not being acted on yet, and the reason is a
  confound rather than caution. The backtest harness does not apply `transfer_role_multiplier`, so
  its "best" mover weight is compensating for a discount that the production model already applies
  to the prior term. At `w = 1.0` the prior term has zero weight and the transfer discount becomes
  dead code, which cannot be right either. Settling it needs a harness that applies the discount
  and fits both together; until then the shipped 0.60 is the conservative option. Listed in
  `docs/prediction_roadmap.md`.
- **It is fitted on one transition**, so the honest question is how much of that -5% is real and
  how much is a curve fitted to 519 particular players. Every table above is in-sample, and note
  that the flat 0.60 it is measured against was *also* fitted on this same data - comparing a
  refitted model to a previously-fitted one flatters the new one.

  Leave-one-club-out cross-validation settles it. Twenty folds; inside each, both the flat weight
  and the knots are chosen from scratch on 19 clubs and scored on the 20th, which no fold ever saw:

  | | out-of-sample MAE |
  |---|---|
  | flat weight, refitted per fold | 0.1934 |
  | role curve, refitted per fold | **0.1816** |

  -6.1%, slightly *better* out of sample than the -5.1% claimed in sample, so the gain is not an
  artifact of fitting. The shape is stable too: **all 20 folds put the middle knot at 1.0**, and 18
  of 20 put the top knot at 0.2 or below. The bottom knot is the unstable one, ranging 0.0 to 0.4
  across folds - the fringe end of the curve is the part to distrust.

  What this does *not* establish is stability across *years*. Cross-validating by club tests
  whether the curve generalises to a club it has not seen; only a second stored pre-season can test
  whether it generalises to another summer. See "The dataset behind every number below".

The known cost: a nailed starter really can be displaced by a summer signing, and pre-season is
the only evidence we have of it. Trusting it less means seeing that later. The projection cannot
model squad competition yet - `docs/prediction_roadmap.md` - so instead of hiding the tension the
board flags it: `preseason_role_drop` marks a player whose prior season says nailed and whose
pre-season says benched, which is the set of players worth checking the team news on yourself.

Two refinements tried and dropped
---------------------------------
- **Keying the curve on `starts / appearances`** - his start rate in the matches he actually
  played - instead of `starts / 38`. The motivation was real: `starts / 38` answers two questions
  at once, and Bukayo Saka's 0.658 is an injury record, not a rotation record. The measurement
  says no anyway. MAE rises from 0.1801 to 0.1870, worse in both halves, and worse *on the very
  group it targets* (0.2592 to 0.2926 over the 92 same-club players with a high start rate when
  playing but a middling overall share). The reason is instructive: a backup keeper who started
  the three matches he played scores 1.00 on that variable, so the change promotes reserves to
  "nailed" and lets their prior of 0.11 outvote a pre-season in which they started everything.
  Manchester United's Bayindir is the case in point - the variable says nailed, he then started
  all five. Conflating fitness with standing costs less than conflating backups with starters.
- **Treating "in no friendly squad at all" as absent evidence** rather than as zero starts, on
  the theory that elite players are away at tournaments rather than out of favour. The data
  says otherwise: those 134 players went on to a mean GW1-5 start share of 0.069 against a
  prior-season share of 0.218. Reading the absence as a zero cuts their error from 0.181 to
  0.109, and adopting the "no evidence" reading drops overall correlation from 0.748 to 0.653.
  Being left out of every friendly is a real signal, and a surprising name near the bottom of
  the board is usually that signal rather than a bug.

  Worth having the numbers to hand, because this is the objection the board attracts. Same-club
  players who started *no* pre-season match, by what last season said about them:

  | prior start share | n | mean actual GW1-5 start share | started none of GW1-5 |
  |---|---|---|---|
  | under 0.30 | 143 | 0.018 | 135 of 143 |
  | 0.30 - 0.60 | 11 | 0.164 | 7 of 11 |
  | 0.60 - 0.80 | 12 | **0.050** | **11 of 12** |
  | 0.80 and above | 4 | 0.200 | 3 of 4 |

  The 0.60-0.80 row is the one that looks wrong and is not. Its members were Justin Kluivert,
  Nicolas Jackson, Luis Díaz, Dejan Kulusevski, Ryan Christie, Vitaly Janelt - established
  players, all on 0.71-0.76 last season, every one of whom started nothing in GW1-5. Missing the
  entire pre-season while fit is how a pending transfer, a late return or a fitness programme
  looks from the outside, and it beats reputation. A projection of 0.18 for such a player is
  generous, not broken.

A transfer is not portable
--------------------------
Last season's start share describes a player's standing in *last season's* squad. Move him to a
deeper one and it stops describing anything. Measured over 2025/26, taking the 119 players who
started 70% or more of 2024/25 and asking what share of GW1-5 they actually started:

| | players | actual GW1-5 start share |
|---|---|---|
| stayed at the same club | 97 | **0.742** |
| changed club | 22 | **0.482** |
| ...of those, moved to a clearly stronger club | 11 | **0.400** |
| ...of those, a lateral move | 9 | 0.578 |

A nailed starter who moves up loses nearly half his start share. Individual cases are brutal:
BOU to ARS went from a 0.82 share to 0.00, SOU to NEW from 0.79 to 0.00, WOL to MCI from 0.97 to
0.60. `transfer_role_multiplier` applies that discount to the prior-season term - and only to
that term, because the pre-season term is already an observation of the *new* squad and needs no
correction.

The caveat is honest: 22 movers, 11 of them upward, and the constants are fitted on the same
sample. The direction is not in doubt; the magnitude is. `v1-baseline` leaves it off so the two
can be compared, and the calibration screen will settle it once GW1-5 resolves.

Availability overrides evidence
-------------------------------
Both signals describe a fit player's standing. `status` and `chance_of_playing_next_round`
describe whether he is fit, and they are FPL's own continuously-updated feed. They are applied
as a multiplier on top, never blended in.

Components
----------
- `MinutesEstimate`: p_start, expected minutes, and every input behind them.
- `MinutesModel`: builds estimates from prior-season totals, per-match history and friendlies.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from src.fpl.models.immutable import Player, PlayerSeason, PlayerType
from src.fpl.projection.history import PlayerHistory
from src.fpl.projection.preseason import PreseasonRole


logger = logging.getLogger(__name__)


DEFAULT_PRESEASON_WEIGHT = 0.60
"""Weight on pre-season start share when both signals exist. Measured; see the module docstring."""

DEFAULT_PRESEASON_FRIENDLY_WEIGHT = 0.20
"""Evidence weight of a friendly, against 1.0 for a competitive pre-season fixture.

Fitted, with a caveat worth stating: aggregate accuracy barely moves across the whole range
(correlation 0.752 at 1.0 up to 0.758 at 0.20), because in 2025/26 only six of twenty clubs
played a competitive pre-season fixture at all and the average is dominated by clubs that
played none. 0.20 is the measured optimum, and it is also what individual cases demand - four
Arsenal friendlies started by Kepa should not outrank David Raya starting the Community Shield,
and at 0.35 they did.

Separate from `RotationConfig.DEFAULT_MATCH_KIND_WEIGHTS`, which stays at 0.35 for the
in-season rotation view. That number governs a different question and was not fitted here.
"""

PRESEASON_TRUST_WEIGHT = 0.75
"""Club evidence weight at which the pre-season signal earns its full blend weight.

Below it the weight ramps down linearly. At `preseason_friendly_weight = 0.20` that is roughly
four friendlies, or one competitive fixture on its own. Fitted; see the module docstring.
"""

PRESEASON_ROLE_KNOTS = (0.20, 1.00, 0.15)
"""Pre-season blend weight at last season's start share 0.0, 0.5 and 1.0, interpolated between.

The absolute weight, not a multiplier on `DEFAULT_PRESEASON_WEIGHT`: this curve replaces the flat
weight for players who stayed at their club, and the flat weight still governs movers. Fitted
per bucket and rounded; the region 0.20-1.00-0.00 through 0.20-1.00-0.20 is flat to within
0.0002 MAE, so the exact end points are not worth arguing about. See the module docstring for
the fit and the split-half check.
"""

NAILED_PRIOR_SHARE = 0.80
"""Prior start share above which a player counts as last season's first choice.

Used only for the `preseason_role_drop` flag, not by the blend - the curve is continuous, and a
threshold in the model would put a cliff between a 0.79 and a 0.81 starter.
"""

ROLE_DROP_PRESEASON_SHARE = 0.35
"""Pre-season start share below which a nailed starter is worth a second look by hand."""

MATCHES_PER_SEASON = 38

MINUTES_PER_START: dict[PlayerType, float] = {
    PlayerType.GKP: 89.6,
    PlayerType.DEF: 85.4,
    PlayerType.MID: 80.1,
    PlayerType.FWD: 78.4,
}
"""Fallback minutes per start, measured over 2025/26 GW1-30."""

FULL_MATCH_RATE: dict[PlayerType, float] = {
    PlayerType.GKP: 0.994,
    PlayerType.DEF: 0.947,
    PlayerType.MID: 0.907,
    PlayerType.FWD: 0.903,
}
"""P(60+ minutes | started), measured over 2025/26 GW1-30. Gates the clean sheet."""

CAMEO_RATE: dict[PlayerType, float] = {
    PlayerType.GKP: 0.003,
    PlayerType.DEF: 0.176,
    PlayerType.MID: 0.324,
    PlayerType.FWD: 0.371,
}
"""P(came off the bench | did not start), measured over 2025/26 GW1-30.

Conditioned on the player's club playing, not on him being in the squad, because that is the
question a projection asks: given he does not start, how often does he get on at all.
"""

CAMEO_MINUTES: dict[PlayerType, float] = {
    PlayerType.GKP: 47.0,
    PlayerType.DEF: 17.7,
    PlayerType.MID: 19.0,
    PlayerType.FWD: 18.0,
}

SHRINKAGE_MATCHES = 6.0
"""Prior weight, in matches, for pulling a player's own rates toward the position default."""

LATERAL_MOVE_MULTIPLIER = 0.78
"""What a nailed starter keeps after a move between clubs of similar quality.

0.578 / 0.742 from the table in the module docstring: a lateral mover realised 0.578 of a start
share where a stayer realised 0.742.
"""

MOVE_QUALITY_EXPONENT = 0.4
"""How much harder a step *up* is, as a power of the clubs' quality ratio.

Solved rather than guessed. A big upgrade realised 0.400 against the stayers' 0.742, so it keeps
0.54 where a lateral move keeps 0.78 - a further factor of 0.69. A representative big upgrade in
the sample (Nottingham to Manchester City) has a quality ratio of about 0.40, and
`0.40 ** 0.4 = 0.69`.
"""

MIN_MOVE_MULTIPLIER = 0.40
"""Floor on the discount. Below this the prior season is telling us nothing worth keeping."""

DOUBTFUL_DEFAULT = 0.5
"""Assumed availability for `status='d'` when FPL gives no percentage."""

STATUS_MEANINGS = {
    'a': 'available',
    'd': 'doubtful',
    'i': 'injured',
    's': 'suspended',
    'u': 'unavailable',
    'n': 'not in squad',
}


def transfer_role_multiplier(moved: bool, quality_ratio: float | None) -> float:
    """How much of a prior-season start share survives a transfer.

    Parameters:
    - moved: whether the player changed club. False returns 1.0 immediately; whether the clubs
      happen to be rated equally is beside the point.
    - quality_ratio: prior club quality divided by new club quality, from
      `TeamStrength.quality_ratio`. Below 1.0 means a step up. None when the old club cannot be
      rated - a promoted side, or a move from outside the league.

    Returns:
    - 1.0 for a player who stayed put.
    - `LATERAL_MOVE_MULTIPLIER` when he moved but the old club cannot be rated: we know a
      transfer costs start share even when we cannot size the step, and the caller flags the
      projection as unpriced either way.
    - Otherwise the scaled discount, floored at `MIN_MOVE_MULTIPLIER` and capped at 1.0. A move
      to a *weaker* club is capped rather than turned into a bonus - nothing in the data says a
      player starts more often than he used to, only that a step up costs him.
    """
    if not moved:
        return 1.0
    if quality_ratio is None:
        return LATERAL_MOVE_MULTIPLIER
    scaled = LATERAL_MOVE_MULTIPLIER * (quality_ratio ** MOVE_QUALITY_EXPONENT)
    return max(MIN_MOVE_MULTIPLIER, min(1.0, scaled))


def preseason_weight_for_prior(
    prior_share: float | None,
    knots: tuple[float, float, float] = PRESEASON_ROLE_KNOTS,
) -> float:
    """Pre-season blend weight for a player who stayed, given last season's start share.

    Piecewise linear over the three knots, which sit at prior shares 0.0, 0.5 and 1.0. Low at
    both ends for opposite reasons - a fringe player's friendly starts say little about league
    starts, and a first-choice player's friendly absence says little either - and highest in the
    middle, where the squad place is genuinely open and pre-season is the manager deciding it.

    Parameters:
    - prior_share: last season's starts as a share of a full campaign, or None for a player with
      no Premier League record. None returns the middle knot: with no prior to weigh against,
      relative trust is not the question and `_blend` uses the pre-season signal whole anyway.
    """
    low, middle, high = knots
    if prior_share is None:
        return middle
    if prior_share <= 0.5:
        return low + (middle - low) * (prior_share / 0.5)
    return middle + (high - middle) * ((prior_share - 0.5) / 0.5)


@dataclass(frozen=True)
class MinutesEstimate:
    """Everything the projection needs to know about whether a player takes the field.

    `p_start` is already multiplied by `availability`; `role_share` is the same figure before
    that multiplier, so the web app can show "first choice, but injured" as the two separate
    facts it is.
    """

    position: PlayerType
    p_start: float
    role_share: float
    availability: float
    status: str
    news: str
    prior_start_share: float | None
    moved: bool
    transfer_multiplier: float
    preseason_start_share: float | None
    preseason_matches: int
    preseason_weight_used: float
    minutes_per_start: float
    full_match_rate: float
    cameo_rate: float
    cameo_minutes: float
    sample_starts: int

    @property
    def p_sixty_plus(self) -> float:
        """P(the player reaches 60 minutes) - the clean-sheet and second-appearance-point gate."""
        return self.p_start * self.full_match_rate

    @property
    def p_appear(self) -> float:
        return self.p_start + (1.0 - self.p_start) * self.cameo_rate

    @property
    def expected_minutes(self) -> float:
        return (
            self.p_start * self.minutes_per_start
            + (1.0 - self.p_start) * self.cameo_rate * self.cameo_minutes
        )

    @property
    def is_role_drop(self) -> bool:
        """Last season says first choice, pre-season says benched.

        The model deliberately discounts the pre-season term for these players, so this is the
        set where that decision could be wrong - a summer signing has taken the place and squad
        competition is not modelled. Surfaced as a flag rather than folded into the number,
        because the thing to do about it is read the team news, which a projection cannot.
        """
        return (
            not self.moved
            and self.prior_start_share is not None
            and self.prior_start_share >= NAILED_PRIOR_SHARE
            and self.preseason_start_share is not None
            and self.preseason_start_share < ROLE_DROP_PRESEASON_SHARE
        )

    @property
    def status_meaning(self) -> str:
        return STATUS_MEANINGS.get(self.status, self.status)

    def as_dict(self) -> dict:
        return {
            'p_start': round(self.p_start, 3),
            'role_share': round(self.role_share, 3),
            'availability': round(self.availability, 3),
            'status': self.status,
            'status_meaning': self.status_meaning,
            'news': self.news,
            'prior_start_share': (
                round(self.prior_start_share, 3) if self.prior_start_share is not None else None
            ),
            'moved': self.moved,
            'transfer_multiplier': round(self.transfer_multiplier, 3),
            'is_role_drop': self.is_role_drop,
            'preseason_start_share': (
                round(self.preseason_start_share, 3)
                if self.preseason_start_share is not None else None
            ),
            'preseason_matches': self.preseason_matches,
            'preseason_weight_used': round(self.preseason_weight_used, 3),
            'minutes_per_start': round(self.minutes_per_start, 1),
            'full_match_rate': round(self.full_match_rate, 3),
            'cameo_rate': round(self.cameo_rate, 3),
            'expected_minutes': round(self.expected_minutes, 1),
            'sample_starts': self.sample_starts,
        }


class MinutesModel:
    """Estimate p_start and expected minutes for one gameweek.

    Parameters:
    - preseason_weight: weight on friendly start share when both signals exist.
    - no_evidence_p_start: what to assume for a player with neither a prior Premier League
      season nor pre-season minutes. Zero is the honest answer - we have seen nothing - and
      the run artifact records `sample_starts=0` so the web app can mark it rather than let a
      reader mistake it for a prediction.
    """

    def __init__(
        self,
        preseason_weight: float = DEFAULT_PRESEASON_WEIGHT,
        no_evidence_p_start: float = 0.0,
        trust_weight: float = PRESEASON_TRUST_WEIGHT,
        role_knots: tuple[float, float, float] | None = PRESEASON_ROLE_KNOTS,
    ):
        if not 0.0 <= preseason_weight <= 1.0:
            raise ValueError(f"preseason_weight must be in [0, 1], got {preseason_weight}")
        if trust_weight <= 0:
            raise ValueError(f"trust_weight must be positive, got {trust_weight}")
        if role_knots is not None:
            if len(role_knots) != 3:
                raise ValueError(f"role_knots must be three values, got {role_knots}")
            if not all(0.0 <= knot <= 1.0 for knot in role_knots):
                raise ValueError(f"every role knot must be in [0, 1], got {role_knots}")
        self._preseason_weight = preseason_weight
        self._no_evidence = no_evidence_p_start
        self._trust_weight = trust_weight
        self._role_knots = role_knots

    def estimate(
        self,
        player: Player,
        prior_season: PlayerSeason | None,
        history: PlayerHistory,
        preseason: PreseasonRole | None,
        transfer_multiplier: float = 1.0,
        moved: bool = False,
    ) -> MinutesEstimate:
        """Build an estimate for one player.

        Parameters:
        - prior_season: last season's totals, or None for a player with no Premier League
          record. None is meaningful and is not replaced with zeros.
        - history: per-match rows, used for this player's own minutes and cameo rates.
        - preseason: friendly involvement, or None when the club has no stored friendlies.
        - transfer_multiplier: discount on the prior-season term for a player who changed club,
          from `transfer_role_multiplier`. Applied only to that term - the pre-season term is
          already an observation of the new squad.
        - moved: whether the player changed club. A mover keeps the flat pre-season weight
          rather than the role curve, because for him pre-season is the only observation of the
          squad he is now in. Read from `prior_season.is_new_club` by the caller so that a
          player with no prior season at all is not silently treated as a stayer.
        """
        position = player.player_type
        prior_share = self._prior_start_share(prior_season)
        discounted_prior = None if prior_share is None else prior_share * transfer_multiplier
        has_preseason = bool(preseason and preseason.has_evidence)
        preseason_share = preseason.start_share if has_preseason else None
        preseason_weight = (
            self._preseason_weight_for(preseason.team_weight, prior_share, moved)
            if has_preseason else 0.0
        )

        role_share = self._blend(discounted_prior, preseason_share, preseason_weight)
        availability = self._availability(player)

        starts = history.starts()
        return MinutesEstimate(
            position=position,
            p_start=role_share * availability,
            role_share=role_share,
            availability=availability,
            status=player.status,
            news=player.news or '',
            prior_start_share=prior_share,
            moved=moved,
            transfer_multiplier=transfer_multiplier,
            preseason_start_share=preseason_share,
            preseason_matches=preseason.team_matches if preseason else 0,
            preseason_weight_used=preseason_weight,
            minutes_per_start=self._minutes_per_start(position, starts),
            full_match_rate=self._full_match_rate(position, starts),
            cameo_rate=self._cameo_rate(position, history),
            cameo_minutes=CAMEO_MINUTES[position],
            sample_starts=len(starts),
        )

    @staticmethod
    def _prior_start_share(prior_season: PlayerSeason | None) -> float | None:
        """Last season's starts as a share of a full campaign, or None with no record."""
        if prior_season is None:
            return None
        return min(1.0, prior_season.starts / MATCHES_PER_SEASON)

    def _preseason_weight_for(
        self, team_weight: float, prior_share: float | None, moved: bool
    ) -> float:
        """Blend weight for the pre-season term.

        Two independent reductions, multiplied:

        - **How much the club played.** A club with one friendly should not have its pre-season
          start shares trusted as much as a club with six, and a club with a competitive fixture
          should be trusted fully.
        - **How strong the prior is.** A nailed starter's pre-season absence is rest; a squad
          player's is a decision. `preseason_weight_for_prior` carries the fitted curve. It is
          skipped for movers, whose prior describes a squad they have left, and skipped entirely
          when `role_knots` is None so the flat weight can be run as a control.
        """
        base = (
            self._preseason_weight
            if moved or self._role_knots is None
            else preseason_weight_for_prior(prior_share, self._role_knots)
        )
        return base * min(1.0, team_weight / self._trust_weight)

    def _blend(
        self,
        prior_share: float | None,
        preseason_share: float | None,
        preseason_weight: float,
    ) -> float:
        """Combine the two role signals, falling back cleanly when one is absent.

        When only the pre-season signal exists there is nothing to blend toward, so it is used
        whole regardless of how thin it is - the ramp expresses relative trust between the two
        signals, not absolute confidence.
        """
        if prior_share is None and preseason_share is None:
            return self._no_evidence
        if prior_share is None:
            return preseason_share
        if preseason_share is None:
            return prior_share
        return preseason_weight * preseason_share + (1.0 - preseason_weight) * prior_share

    @staticmethod
    def _availability(player: Player) -> float:
        """Turn FPL's status feed into a multiplier on the player's role.

        `chance_of_playing_next_round` is authoritative when FPL publishes it. When it is null
        the meaning depends on status: for an available player null means 100%, for anyone else
        it means FPL has flagged a problem without quantifying it.
        """
        chance = player.chance_of_playing_next_round
        if chance is not None:
            return chance / 100.0
        if player.status == 'a':
            return 1.0
        if player.status == 'd':
            return DOUBTFUL_DEFAULT
        return 0.0

    @staticmethod
    def _minutes_per_start(position: PlayerType, starts: list) -> float:
        if not starts:
            return MINUTES_PER_START[position]
        weight = len(starts) / (len(starts) + SHRINKAGE_MATCHES)
        observed = sum(match.minutes for match in starts) / len(starts)
        return weight * observed + (1.0 - weight) * MINUTES_PER_START[position]

    @staticmethod
    def _full_match_rate(position: PlayerType, starts: list) -> float:
        if not starts:
            return FULL_MATCH_RATE[position]
        weight = len(starts) / (len(starts) + SHRINKAGE_MATCHES)
        observed = sum(1 for match in starts if match.minutes >= 60) / len(starts)
        return weight * observed + (1.0 - weight) * FULL_MATCH_RATE[position]

    @staticmethod
    def _cameo_rate(position: PlayerType, history: PlayerHistory) -> float:
        non_starts = [match for match in history.matches if not match.started]
        if not non_starts:
            return CAMEO_RATE[position]
        weight = len(non_starts) / (len(non_starts) + SHRINKAGE_MATCHES)
        observed = sum(1 for match in non_starts if match.played) / len(non_starts)
        return weight * observed + (1.0 - weight) * CAMEO_RATE[position]
