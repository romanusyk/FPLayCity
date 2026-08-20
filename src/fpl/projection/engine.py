"""Turn the component models into projected points over a range of gameweeks.

What the engine does
--------------------
For each player and each of their club's fixtures in the horizon it asks the same eleven
questions the FPL scoring function asks, using the component models rather than a single
regression, and sums the answers. Nothing here invents a number: every term traces to
`minutes.py`, `rates.py`, `defensive.py` or `strength.py`, and every term is kept separately so
the web app can show the breakdown instead of a total you have to trust.

What it deliberately does not model
-----------------------------------
- **Own goals and missed penalties.** Together about 0.3% of points. Modelling them would add
  noise, not accuracy.
- **Set-piece duty.** FPL publishes who takes penalties, corners and free kicks, and it is
  genuinely predictive - but the player's own xG and xA already include whatever set pieces he
  took last season, so adding a bonus would double-count. Newly appointed takers are the real
  gap, and until that is modelled properly the duty is surfaced as a flag on the board rather
  than folded silently into a number.
- **Opponent-specific defensive contribution.** Some sides concede far more tackles and
  interceptions than others. Listed in `docs/prediction_roadmap.md` as the next thing to build.

Every one of these is a *known* omission with a stated size, which is the difference between a
simplification and a bug.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from src.fotmob.models.fotmob import MatchKind
from src.fotmob.rotation.rotation_config import RotationConfig
from src.fpl.loader.utils import Season
from src.fpl.models.immutable import (
    Player,
    PlayerSeason,
    PlayerType,
    Query,
)
from src.fpl.projection import poisson
from src.fpl.projection.defensive import DefensiveContributionModel, DefensiveEstimate
from src.fpl.projection.history import PlayerHistory, build_player_histories
from src.fpl.projection.methods import ProjectionParams
from src.fpl.projection.minutes import (
    MinutesEstimate,
    MinutesModel,
    transfer_role_multiplier,
)
from src.fpl.projection.preseason import PreseasonRole, build_preseason_roles
from src.fpl.projection.rates import PlayerRates, RateModel
from src.fpl.projection.scoring import (
    APPEARANCE_POINTS_FULL,
    APPEARANCE_POINTS_PARTIAL,
    ASSIST_POINTS,
    CLEAN_SHEET_POINTS,
    DEFENSIVE_CONTRIBUTION_POINTS,
    GOAL_POINTS,
    GOALS_CONCEDED_PER_POINT,
    MatchScore,
    RED_CARD_POINTS,
    SAVES_PER_POINT,
    YELLOW_CARD_POINTS,
)
from src.fpl.projection.strength import TeamStrength


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FixtureProjection:
    """One player, one fixture."""

    gameweek: int
    fixture_id: int
    opponent: str
    at_home: bool
    clean_sheet_probability: float
    attack_multiplier: float
    score: MatchScore

    def as_dict(self) -> dict:
        return {
            'gameweek': self.gameweek,
            'fixture_id': self.fixture_id,
            'opponent': self.opponent,
            'at_home': self.at_home,
            'clean_sheet_probability': round(self.clean_sheet_probability, 3),
            'attack_multiplier': round(self.attack_multiplier, 3),
            'points': round(self.score.total, 3),
        }


@dataclass
class PlayerProjection:
    """One player's projection over the whole horizon, with its inputs attached.

    `flags` is where every judgement call the models made becomes visible: a promoted club, a
    transfer we could not price, a projection resting on no evidence at all. The board shows
    them; they are not decoration.
    """

    player_id: int
    web_name: str
    team: str
    position: PlayerType
    price: float
    ownership: float
    fixtures: list[FixtureProjection]
    minutes: MinutesEstimate
    rates: PlayerRates
    defensive: DefensiveEstimate
    prior_season: PlayerSeason | None
    flags: list[str] = field(default_factory=list)

    @property
    def total(self) -> MatchScore:
        total = MatchScore()
        for fixture in self.fixtures:
            total = total + fixture.score
        return total

    @property
    def points(self) -> float:
        return self.total.total

    @property
    def points_per_gameweek(self) -> float:
        return self.points / len(self.fixtures) if self.fixtures else 0.0

    @property
    def points_per_million(self) -> float:
        return self.points / self.price if self.price else 0.0

    def as_dict(self) -> dict:
        return {
            'player_id': self.player_id,
            'web_name': self.web_name,
            'team': self.team,
            'position': self.position.name,
            'price': self.price,
            'ownership': self.ownership,
            'points': round(self.points, 2),
            'points_per_gameweek': round(self.points_per_gameweek, 3),
            'points_per_million': round(self.points_per_million, 3),
            'components': {name: round(value, 3) for name, value in self.total.as_dict().items()},
            'inputs': {
                'minutes': self.minutes.as_dict(),
                'rates': self.rates.as_dict(),
                'defensive_contribution': self.defensive.as_dict(),
            },
            'prior_season': _prior_season_summary(self.prior_season),
            'fixtures': [fixture.as_dict() for fixture in self.fixtures],
            'flags': self.flags,
        }


def _prior_season_summary(prior_season: PlayerSeason | None) -> dict | None:
    """A compact view of last season, or None when the player has no Premier League record."""
    if prior_season is None:
        return None
    return {
        'season': prior_season.season,
        'club': prior_season.prior_team,
        'source': prior_season.source.value,
        'minutes': prior_season.minutes,
        'starts': prior_season.starts,
        'total_points': prior_season.total_points,
        'goals_scored': prior_season.goals_scored,
        'assists': prior_season.assists,
        'clean_sheets': prior_season.clean_sheets,
        'defensive_contribution': prior_season.defensive_contribution,
        'is_new_club': prior_season.is_new_club,
    }


class ProjectionEngine:
    """Assemble component models into per-player projections.

    Requires the collections to be populated - call `src.fpl.loader.load.load_from_snapshots`
    first.
    """

    def __init__(self, params: ProjectionParams, season: str | None = None):
        self.season = season or Season.CURRENT
        self.params = params
        self.evidence_season = Season.previous(self.season)

        self.histories = build_player_histories(self.season)
        self.strength = TeamStrength(self.season, params.team_shrinkage_matches)
        self.rate_model = RateModel(
            Query.player_seasons_by_season(self.evidence_season),
            params.rate_shrinkage_minutes,
        )
        self.minutes_model = MinutesModel(
            params.preseason_weight,
            trust_weight=params.preseason_trust_weight,
            role_knots=params.preseason_role_knots,
        )
        self.defensive_model = DefensiveContributionModel(params.dc_shrinkage_starts)
        self.preseason = (
            build_preseason_roles(
                self.season,
                RotationConfig(match_kind_weights={
                    MatchKind.COMPETITIVE: 1.0,
                    MatchKind.FRIENDLY: params.preseason_friendly_weight,
                }),
                before_gameweek=params.gameweek_from,
            )
            if params.use_preseason else {}
        )

    def project_all(self) -> list[PlayerProjection]:
        """Project every outfield player and goalkeeper in the season.

        Managers (`PlayerType.MNG`) are excluded: they are a separate FPL game mode with their
        own scoring, not players with zero output.
        """
        projections = [
            self.project(player)
            for player in Query.all_players()
            if player.player_type is not PlayerType.MNG
        ]
        no_fixtures = [p for p in projections if not p.fixtures]
        if no_fixtures:
            raise ValueError(
                f"{len(no_fixtures)} players have no fixtures in GW{self.params.gameweek_from}-"
                f"{self.params.gameweek_to} (e.g. {no_fixtures[0].web_name}, "
                f"{no_fixtures[0].team}). The fixtures snapshot is incomplete; re-fetch it."
            )
        logger.info(
            "Projected %d players over GW%d-%d using method params %s",
            len(projections), self.params.gameweek_from, self.params.gameweek_to, self.params,
        )
        return projections

    def project(self, player: Player) -> PlayerProjection:
        """Project one player over the configured horizon."""
        history = self.histories.get(player.player_id) or PlayerHistory(player.player_id, player.code)
        prior_season = Query.player_season(player.player_id, self.evidence_season)
        preseason: PreseasonRole | None = self.preseason.get(player.player_id)

        minutes = self.minutes_model.estimate(
            player, prior_season, history, preseason,
            transfer_multiplier=self._transfer_multiplier(player, prior_season),
            moved=bool(prior_season and prior_season.is_new_club),
        )
        rates = self.rate_model.estimate(
            player.player_type,
            prior_season,
            club_attack=self.strength.rating(player.team.short_name).attack,
        )
        defensive = self.defensive_model.estimate(player.player_type, history)

        flags, club_change = self._flags(player, prior_season, minutes, history)
        fixtures = [
            self._project_fixture(player, minutes, rates, defensive, team_fixture, club_change)
            for team_fixture in self._horizon_fixtures(player.team_id)
        ]
        return PlayerProjection(
            player_id=player.player_id,
            web_name=player.web_name,
            team=player.team.short_name,
            position=player.player_type,
            price=player.now_cost,
            ownership=player.selected_by_percent,
            fixtures=fixtures,
            minutes=minutes,
            rates=rates,
            defensive=defensive,
            prior_season=prior_season,
            flags=flags,
        )

    def _transfer_multiplier(self, player: Player, prior_season: PlayerSeason | None) -> float:
        """Discount on last season's start share for a player who has since changed club.

        Off unless `params.discount_transfers` is set, so `v1-baseline` and the `v2-transfer`
        methods differ in exactly this one way and the comparison isolates it.
        """
        if not self.params.discount_transfers or prior_season is None:
            return 1.0
        return transfer_role_multiplier(
            moved=prior_season.is_new_club,
            quality_ratio=self.strength.quality_ratio(
                player.team.short_name, prior_season.prior_team
            ),
        )

    def _horizon_fixtures(self, team_id: int) -> list:
        """Every fixture a club plays in the horizon, doubles and blanks included as they fall."""
        return Query.team_fixtures_by_team_and_gameweeks(
            team_id, self.params.gameweek_from, self.params.gameweek_to
        )

    def _flags(
        self,
        player: Player,
        prior_season: PlayerSeason | None,
        minutes: MinutesEstimate,
        history: PlayerHistory,
    ) -> tuple[list[str], float]:
        """Collect the caveats on this projection, and the club-change scaling to apply."""
        flags: list[str] = []
        club_change = 1.0

        if self.strength.rating(player.team.short_name).promoted:
            flags.append('promoted_club')
        if prior_season is None:
            flags.append('no_prior_season')
        elif prior_season.is_new_club:
            flags.append('new_club')
            multiplier = self.strength.club_change_multiplier(
                player.team.short_name, prior_season.prior_team
            )
            if multiplier is None:
                flags.append('club_change_unpriced')
            elif self.params.adjust_for_club_change:
                club_change = multiplier
        if not history.matches:
            flags.append('no_match_history')
        if minutes.status != 'a':
            flags.append(f'status_{minutes.status}')
        if minutes.preseason_matches and minutes.preseason_start_share == 0.0:
            flags.append('no_preseason_start')
        if minutes.is_role_drop:
            flags.append('preseason_role_drop')
        flags.extend(player.set_piece_roles)
        return flags, club_change

    def _project_fixture(
        self,
        player: Player,
        minutes: MinutesEstimate,
        rates: PlayerRates,
        defensive: DefensiveEstimate,
        team_fixture,
        club_change: float,
    ) -> FixtureProjection:
        """Score one fixture, component by component."""
        position = player.player_type
        team = player.team.short_name
        opponent = team_fixture.opponent_team.short_name
        at_home = team_fixture.side == 'home'

        attack = self.strength.attack_multiplier(opponent, at_home) * club_change
        nineties = minutes.expected_minutes / 90.0
        clean_sheet = self.strength.clean_sheet_probability(team, opponent, at_home)

        conceded_divisor = GOALS_CONCEDED_PER_POINT.get(position)
        concession = 0.0
        if conceded_divisor is not None:
            # Scaled by minutes on the pitch: a player only concedes while playing, and the
            # floor is applied to the full-match distribution before scaling, not after.
            concession = self.strength.expected_concession_points(team, opponent, at_home) * nineties

        score = MatchScore(
            appearance=(
                minutes.p_sixty_plus * APPEARANCE_POINTS_FULL
                + (minutes.p_appear - minutes.p_sixty_plus) * APPEARANCE_POINTS_PARTIAL
            ),
            goals=rates.xg * nineties * attack * GOAL_POINTS[position],
            assists=rates.xa * nineties * attack * ASSIST_POINTS,
            clean_sheets=minutes.p_sixty_plus * clean_sheet * CLEAN_SHEET_POINTS[position],
            goals_conceded=concession,
            saves=poisson.expected_floor_div(rates.saves * nineties, SAVES_PER_POINT),
            defensive_contribution=(
                minutes.p_start * defensive.hit_rate * DEFENSIVE_CONTRIBUTION_POINTS
            ),
            bonus=rates.bonus * nineties,
            cards=(
                rates.yellow * nineties * YELLOW_CARD_POINTS
                + rates.red * nineties * RED_CARD_POINTS
            ),
        )
        return FixtureProjection(
            gameweek=team_fixture.gameweek,
            fixture_id=team_fixture.fixture_id,
            opponent=opponent,
            at_home=at_home,
            clean_sheet_probability=clean_sheet,
            attack_multiplier=attack,
            score=score,
        )
