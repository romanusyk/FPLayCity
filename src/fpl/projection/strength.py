"""Team attack and defence ratings, and the clean-sheet model built on them.

Why not use FPL's own strength ratings
--------------------------------------
`bootstrap-static` carries `strength_attack_home`, `strength_defence_away` and friends, which
would be the obvious input. Before a season starts they are all zero - checked on the
2026-08-15 snapshot, all four fields are 0 for all 20 clubs - and only `strength_overall_home`
carries the 1-5 tier. Depending on them would silently rate every club identically for exactly
the ten gameweeks a redraft cares about.

So ratings are measured from last season's results instead: goals scored and conceded per
match, shrunk toward the league average, taken from the stored fixtures snapshot.

Promoted clubs
--------------
Coventry, Hull and Ipswich have no Premier League record at all. Rather than let them inherit
a league-average rating - which would flatter them badly - they are given the average of the
prior season's bottom three, and flagged `promoted=True` so the web app can mark every number
downstream of that assumption. It is a guess, and it is labelled as one.

Components
----------
- `TeamRating`: one club's attack and defence multipliers, with provenance.
- `TeamStrength`: the league table of ratings plus the fixture-level questions worth asking -
  expected goals for and against, clean-sheet probability, expected concession points.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass

from src.fpl.loader.store import JsonSnapshotStore, SnapshotSpec
from src.fpl.loader.utils import Season
from src.fpl.models.immutable import PlayerType, Query
from src.fpl.projection import poisson
from src.fpl.projection.scoring import GOALS_CONCEDED_PER_POINT


logger = logging.getLogger(__name__)


DEFAULT_SHRINKAGE_MATCHES = 8.0
"""Prior weight, in matches, for pulling a club's rating toward the league average of 1.0.

A full season is 38 matches, so 8 leaves an established club's rating essentially intact while
stopping a small stored sample - our 2025/26 snapshot holds 30 gameweeks, and a mid-season
snapshot could hold far fewer - from producing extreme ratings.
"""

PROMOTED_CLUB_SAMPLE = 3
"""How many of last season's worst clubs to average for a promoted club's starting rating."""


@dataclass(frozen=True)
class TeamRating:
    """One club's scoring and conceding rate, as a multiple of the league average.

    `attack` above 1.0 means the club scores more than an average side; `defence` above 1.0
    means it *concedes* more, so lower is better. Both are on the same scale so a fixture
    lambda is just `league_average * attack[a] * defence[b]`.
    """

    short_name: str
    attack: float
    defence: float
    matches: int
    goals_for: int
    goals_against: int
    promoted: bool

    def as_dict(self) -> dict:
        return {
            'attack': round(self.attack, 3),
            'defence': round(self.defence, 3),
            'matches': self.matches,
            'goals_for': self.goals_for,
            'goals_against': self.goals_against,
            'promoted': self.promoted,
        }


def _prior_season_results(prior_season: str) -> tuple[dict[str, list[int]], float, float]:
    """Read last season's finished fixtures.

    Returns:
    - `{short_name: [goals_for, goals_against, matches]}`
    - league average goals per team per match
    - home advantage, as home goals divided by away goals

    Raises:
    - FileNotFoundError: if the prior season has no stored fixtures or bootstrap. Without a
      prior season there is nothing to rate clubs on, and a caller must handle that explicitly
      rather than receive flat ratings.
    """
    fixtures_store = JsonSnapshotStore(SnapshotSpec(base_path=f"data/{prior_season}/fixtures"))
    bootstrap_store = JsonSnapshotStore(SnapshotSpec(base_path=f"data/{prior_season}/bootstrap"))
    if fixtures_store.find_latest() is None or bootstrap_store.find_latest() is None:
        raise FileNotFoundError(
            f"Need both data/{prior_season}/fixtures and data/{prior_season}/bootstrap to rate "
            f"clubs. Fetch them with: uv run -m src.fpl.fetch --season {prior_season}"
        )
    short_names = {team['id']: team['short_name'] for team in bootstrap_store.load_latest()['teams']}

    totals: dict[str, list[int]] = {name: [0, 0, 0] for name in short_names.values()}
    home_goals = away_goals = 0
    for fixture in fixtures_store.load_latest():
        if not fixture['finished']:
            continue
        home, away = short_names[fixture['team_h']], short_names[fixture['team_a']]
        home_score, away_score = fixture['team_h_score'], fixture['team_a_score']
        totals[home][0] += home_score
        totals[home][1] += away_score
        totals[home][2] += 1
        totals[away][0] += away_score
        totals[away][1] += home_score
        totals[away][2] += 1
        home_goals += home_score
        away_goals += away_score

    played = sum(row[2] for row in totals.values())
    if not played:
        raise ValueError(
            f"data/{prior_season}/fixtures holds no finished fixtures. Club ratings cannot be "
            f"measured from it."
        )
    league_average = sum(row[0] for row in totals.values()) / played
    home_advantage = home_goals / away_goals if away_goals else 1.0
    return totals, league_average, home_advantage


class TeamStrength:
    """Fixture-level expectations derived from last season's goals.

    Parameters:
    - season: the season being projected. Ratings come from the season before it.
    - shrinkage_matches: prior weight pulling each rating toward 1.0.

    Raises:
    - FileNotFoundError: if the prior season's snapshots are missing.
    """

    def __init__(self, season: str | None = None, shrinkage_matches: float = DEFAULT_SHRINKAGE_MATCHES):
        self.season = season or Season.CURRENT
        self.prior_season = Season.previous(self.season)
        totals, self.league_average_goals, self.home_advantage = _prior_season_results(self.prior_season)
        self._shrinkage = shrinkage_matches
        self.prior_ratings: dict[str, TeamRating] = {}
        """Every club rated from last season, relegated ones included. Needed to price a move."""
        self.ratings = self._build_ratings(totals)

    def _build_ratings(self, totals: dict[str, list[int]]) -> dict[str, TeamRating]:
        """Rate every club in the season being projected, promoted sides included."""
        measured: dict[str, TeamRating] = {}
        for short_name, (goals_for, goals_against, matches) in totals.items():
            if not matches:
                continue
            measured[short_name] = TeamRating(
                short_name=short_name,
                attack=self._shrink(goals_for / matches, matches),
                defence=self._shrink(goals_against / matches, matches),
                matches=matches,
                goals_for=goals_for,
                goals_against=goals_against,
                promoted=False,
            )

        self.prior_ratings = measured
        weakest = sorted(measured.values(), key=lambda rating: rating.attack)[:PROMOTED_CLUB_SAMPLE]
        leakiest = sorted(measured.values(), key=lambda rating: -rating.defence)[:PROMOTED_CLUB_SAMPLE]
        promoted_attack = sum(rating.attack for rating in weakest) / len(weakest)
        promoted_defence = sum(rating.defence for rating in leakiest) / len(leakiest)

        ratings: dict[str, TeamRating] = {}
        promoted: list[str] = []
        for team in Query.all_teams():
            existing = measured.get(team.short_name)
            if existing is not None:
                ratings[team.short_name] = existing
                continue
            promoted.append(team.short_name)
            ratings[team.short_name] = TeamRating(
                short_name=team.short_name,
                attack=promoted_attack,
                defence=promoted_defence,
                matches=0,
                goals_for=0,
                goals_against=0,
                promoted=True,
            )
        if promoted:
            logger.info(
                "No %s record for %s - rated from the bottom-%d average (attack %.2f, "
                "defence %.2f) and flagged as promoted.",
                self.prior_season, ", ".join(sorted(promoted)), PROMOTED_CLUB_SAMPLE,
                promoted_attack, promoted_defence,
            )
        return ratings

    def _shrink(self, rate: float, matches: int) -> float:
        """Pull a per-match rate toward the league average and express it as a multiplier."""
        weight = matches / (matches + self._shrinkage)
        blended = weight * rate + (1.0 - weight) * self.league_average_goals
        return blended / self.league_average_goals

    def rating(self, short_name: str) -> TeamRating:
        """Return a club's rating.

        Raises:
        - KeyError: for a club not in the projected season, which means the caller is holding a
          stale team id.
        """
        if short_name not in self.ratings:
            raise KeyError(
                f"No rating for club '{short_name}' in {self.season}. Known clubs: "
                f"{sorted(self.ratings)}"
            )
        return self.ratings[short_name]

    def expected_goals_for(self, team: str, opponent: str, at_home: bool) -> float:
        """Expected goals a club scores in one fixture."""
        venue = math.sqrt(self.home_advantage) if at_home else 1.0 / math.sqrt(self.home_advantage)
        return self.league_average_goals * self.rating(team).attack * self.rating(opponent).defence * venue

    def expected_goals_against(self, team: str, opponent: str, at_home: bool) -> float:
        """Expected goals a club concedes in one fixture."""
        return self.expected_goals_for(opponent, team, not at_home)

    def clean_sheet_probability(self, team: str, opponent: str, at_home: bool) -> float:
        """P(the club concedes nothing), as a Poisson zero.

        Poisson understates draws and shutouts slightly - the Dixon-Coles correction exists for
        exactly that - but at this level of input precision the correction is smaller than the
        error in the ratings themselves.
        """
        return math.exp(-self.expected_goals_against(team, opponent, at_home))

    def expected_concession_points(self, team: str, opponent: str, at_home: bool) -> float:
        """Expected point penalty from goals conceded, for a goalkeeper or defender.

        FPL deducts one point per two goals conceded, so this is `-E[floor(GC / 2)]` under the
        same Poisson - not `-lambda / 2`, which would over-charge low-scoring fixtures.
        """
        return -poisson.expected_floor_div(
            self.expected_goals_against(team, opponent, at_home),
            GOALS_CONCEDED_PER_POINT[PlayerType.DEF],
        )

    def attack_multiplier(self, opponent: str, at_home: bool) -> float:
        """How much easier than an average fixture this one is for an attacker.

        Deliberately only the opponent and the venue. A player's own xG rate already carries
        their team's attacking quality, so multiplying by it again would double-count. The one
        case that needs more is a player who changed club, and `club_change_multiplier` handles
        that explicitly rather than folding it in here.
        """
        venue = math.sqrt(self.home_advantage) if at_home else 1.0 / math.sqrt(self.home_advantage)
        return self.rating(opponent).defence * venue

    def quality(self, short_name: str) -> float:
        """A single number for how good a club is: attack divided by defence.

        Crude on purpose. It is used to compare two clubs' squad depth, where the question is
        only "is this a step up", and a composite of the two ratings already in hand answers
        that without inventing a third model.
        """
        rating = self.rating(short_name)
        return rating.attack / rating.defence if rating.defence else rating.attack

    def prior_quality(self, short_name: str) -> float | None:
        """`quality` for a club as it was last season, relegated clubs included.

        Returns None for a club with no Premier League record last season - a promoted side, or
        a move from abroad. That is not the same as "average"; the caller must handle it.
        """
        rating = self.prior_ratings.get(short_name)
        if rating is None:
            return None
        return rating.attack / rating.defence if rating.defence else rating.attack

    def quality_ratio(self, new_club: str, prior_club: str | None) -> float | None:
        """`prior club quality / new club quality`. Below 1.0 means a step up.

        Returns None when the prior club cannot be rated, and 1.0 when the player did not move.
        """
        if prior_club is None or prior_club == new_club:
            return 1.0
        prior = self.prior_quality(prior_club)
        if prior is None:
            return None
        new = self.quality(new_club)
        if new <= 0:
            return None
        return prior / new

    def club_change_multiplier(self, new_club: str, prior_club: str | None) -> float | None:
        """Scale a transferred player's attacking rate from their old club's level to their new one.

        Returns None when `prior_club` is unknown or was outside the Premier League, which is
        not the same as 1.0: it means we cannot say, and the caller should flag the projection
        rather than quietly assume the move is neutral.
        """
        if prior_club is None or prior_club == new_club:
            return 1.0
        prior = self.prior_ratings.get(prior_club)
        if prior is None or prior.attack <= 0:
            return None
        return self.rating(new_club).attack / prior.attack
