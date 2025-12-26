"""
Prediction result containers and aggregators.

Structure:
- Group by gameweek first, then aggregate by team/player
- Predictions stored in dictionaries for efficient merging

Classes:
- TeamFixturePrediction: Team clean sheet prediction for a single fixture
- PlayerFixturePrediction: Player predictions (CS/xG/xA/DC) for a single fixture
- TeamTotalPrediction: Aggregated team predictions across multiple fixtures
- PlayerTotalPrediction: Aggregated player predictions with points calculation
- GameweekPrediction: All predictions for a single gameweek
- GameweekPredictions: Aggregates and sorts predictions across multiple gameweeks
"""
import dataclasses
from functools import reduce
from typing import Callable
import operator

from src.fpl.aggregate import Aggregate
from src.fpl.models.immutable import Metric, TeamFixture, PlayerFixture, Player, Team, Query, PlayerType, NewsFact
from src.fpl.models.season import Season
from src.fpl.forecast.models import SimpleModelResponse
from src.fotmob.rotation.rotation_view import PlayerSquadRole, RivalStartHint
from src.fpl.models.red_flags import build_red_flags, PlayerRegFlag


class TeamFixturePrediction:

    fixture: TeamFixture
    cs_prediction: Aggregate
    metrics_exp: dict[Metric, SimpleModelResponse]

    def __init__(self, fixture: TeamFixture, cs_prediction: Aggregate, metrics_exp: dict[Metric, SimpleModelResponse] | None = None):
        self.fixture = fixture
        self.cs_prediction = cs_prediction
        self.metrics_exp = metrics_exp or {}

    def __repr__(self):
        return f'{self.fixture.team}={self.cs_prediction} ({self.fixture.opponent_team})'


class PlayerFixturePrediction:

    fixture: PlayerFixture
    cs_prediction: Aggregate
    xg_prediction: Aggregate
    xa_prediction: Aggregate
    dc_prediction: Aggregate
    metrics_exp: dict[Metric, SimpleModelResponse]

    def __init__(
            self,
            fixture: PlayerFixture,
            cs_prediction: Aggregate,
            xg_prediction: Aggregate,
            xa_prediction: Aggregate,
            dc_prediction: Aggregate,
            metrics_exp: dict[Metric, SimpleModelResponse] | None = None,
    ):
        self.fixture = fixture
        self.cs_prediction = cs_prediction
        self.xg_prediction = xg_prediction
        self.xa_prediction = xa_prediction
        self.dc_prediction = dc_prediction
        self.metrics_exp = metrics_exp or {}

    def __repr__(self):
        return (
            f'{Query.player(self.fixture.player_id)}: '
            f'{self.xg_prediction.p:.1f} xG '
            f'+ {self.xa_prediction.p:.1f} xA '
            f'+ {self.dc_prediction.p:.1f} DC '
            f'+ {self.cs_prediction.p:.1f} CS'
        )


class TeamTotalPrediction:

    fixture_predictions: list[TeamFixturePrediction]

    def __init__(
            self,
            fixture_predictions: list[TeamFixturePrediction],
    ):
        self.fixture_predictions = fixture_predictions

    @property
    def team(self) -> Team:
        return self.fixture_predictions[0].fixture.team

    @staticmethod
    def _agg(aggregates: list[Aggregate]):
        return reduce(operator.add, aggregates)

    @property
    def xgc_exp(self) -> Aggregate:
        return self._agg([Aggregate(fp.metrics_exp[Metric.XGC].metric_exp, 1) for fp in self.fixture_predictions])

    @property
    def cs_prediction(self) -> Aggregate:
        return self._agg([fp.cs_prediction for fp in self.fixture_predictions])


class PlayerTotalPrediction:

    fixture_predictions: list[PlayerFixturePrediction]

    def __init__(
            self,
            season: Season,
            fixture_predictions: list[PlayerFixturePrediction],
            min_history_gws: int,
    ):
        self.season = season
        self.fixture_predictions = fixture_predictions
        self.min_history_gws = min_history_gws

    @property
    def player(self) -> Player:
        return Query.player(self.fixture_predictions[0].fixture.player_id)

    @staticmethod
    def _agg(aggregates: list[Aggregate]):
        return reduce(operator.add, aggregates)
    
    @property
    def cs_exp(self) -> Aggregate:
        return self._agg([Aggregate(fp.metrics_exp[Metric.CS].metric_exp, 1) for fp in self.fixture_predictions])

    @property
    def cs_prediction(self) -> Aggregate:
        return self._agg([fp.cs_prediction for fp in self.fixture_predictions])

    @property
    def xg_exp(self) -> Aggregate:
        return self._agg([Aggregate(fp.metrics_exp[Metric.XG].metric_exp, 1) for fp in self.fixture_predictions])

    @property
    def xg_prediction(self) -> Aggregate:
        return self._agg([fp.xg_prediction for fp in self.fixture_predictions])

    @property
    def xa_exp(self) -> Aggregate:
        return self._agg([Aggregate(fp.metrics_exp[Metric.XA].metric_exp, 1) for fp in self.fixture_predictions])

    @property
    def xa_prediction(self) -> Aggregate:
        return self._agg([fp.xa_prediction for fp in self.fixture_predictions])

    @property
    def dc_exp(self) -> Aggregate:
        return self._agg([Aggregate(fp.metrics_exp[Metric.DC].metric_exp, 1) for fp in self.fixture_predictions])

    @property
    def dc_prediction(self) -> Aggregate:
        return self._agg([fp.dc_prediction for fp in self.fixture_predictions])

    @property
    def cs_exp_points(self) -> float:
        return self.cs_exp.p * self.player.clean_sheet_points
    
    @property
    def cs_predicted_points(self) -> float:
        return self.cs_prediction.p * self.player.clean_sheet_points

    @property
    def xg_exp_points(self) -> float:
        return self.xg_exp.p * self.player.goal_points

    @property
    def xg_predicted_points(self) -> float:
        return self.xg_prediction.p * self.player.goal_points

    @property
    def xa_exp_points(self) -> float:
        return self.xa_exp.p * self.player.assist_points

    @property
    def xa_predicted_points(self) -> float:
        return self.xa_prediction.p * self.player.assist_points

    @property
    def dc_exp_points(self) -> float:
        return self.dc_exp.p * self.player.dc_points

    @property
    def dc_predicted_points(self) -> float:
        return self.dc_prediction.p * self.player.dc_points

    @property
    def total_exp_points(self) -> float:
        return self.cs_exp_points + self.xg_exp_points + self.xa_exp_points + self.dc_exp_points

    @property
    def total_predicted_points(self) -> float:
        return self.cs_predicted_points + self.xg_predicted_points + self.xa_predicted_points + self.dc_predicted_points

    @property
    def total_exp_points_per_value(self) -> float:
        return self.total_exp_points / self.player.now_cost

    @property
    def total_predicted_points_per_value(self) -> float:
        return self.total_predicted_points / self.player.now_cost

    @property
    def million_per_total_exp_points(self) -> float:
        return self.player.now_cost / self.total_exp_points if self.total_exp_points else 999.

    @property
    def million_per_total_predicted_points(self) -> float:
        return self.player.now_cost / self.total_predicted_points if self.total_predicted_points else 999.

    @property
    def actual_points(self) -> int | None:
        result = None
        for fp in self.fixture_predictions:
            if fp.fixture.total_points is not None:
                result = result or 0
                result += fp.fixture.total_points
        return result

    @property
    def actual_points_per_value(self) -> float | None:
        return self.actual_points / self.player.now_cost if self.actual_points else None

    @property
    def red_flags(self) -> list[PlayerRegFlag]:
        return build_red_flags(self.player.player_id)

    @property
    def squad_role(self) -> PlayerSquadRole | None:
        if not self.season.rotation_adapter or self.player.minutes == 0:
            return None
        return self.season.get_player_squad_role(self.player.player_id)

    @property
    def rotation_rivals(self) -> RivalStartHint | None:
        if not self.season.rotation_adapter or self.player.minutes == 0:
            return None
        return self.season.get_rival_start_hint(self.player.player_id)

    @property
    def news_facts(self) -> list[NewsFact]:
        """Returns all available facts for this player relevant to the prediction horizon."""
        gws = {fp.fixture.gameweek for fp in self.fixture_predictions}
        player_id = self.fixture_predictions[0].fixture.player_id
        all_facts = Query.news_facts_by_player(player_id)
        return [f for f in all_facts if f.next_gameweek in gws]

    @property
    def a_points_breakdown(self) -> str:
        return (
            f'{self.total_predicted_points:.2f} '
            f'({self.million_per_total_predicted_points:.2f}£) = '
            f'{self.xg_predicted_points:.2f} '
            f'{int(100 * self.xg_predicted_points / self.total_predicted_points)}% xG '
            f'+ {self.xa_predicted_points:.2f} '
            f'{int(100 * self.xa_predicted_points / self.total_predicted_points)}% xA '
            f'+ {self.dc_predicted_points:.2f} '
            f'{int(100 * self.dc_predicted_points / self.total_predicted_points)}% DC '
            f'+ {self.cs_predicted_points:.2f} '
            f'{int(100 * self.cs_predicted_points / self.total_predicted_points)}% CS '
        )

    def __repr__(self):
        xg_share = self.season.player_stats[self.player.player_id].share_last(self.min_history_gws, 'xg')
        xa_share = self.season.player_stats[self.player.player_id].share_last(self.min_history_gws, 'xa')
        role_suffix = ''
        squad_role = self.squad_role
        if squad_role and squad_role.total_matches:
            role_suffix = f" [{'FT' if squad_role.is_first_team else 'ST'} {squad_role.starts}/{squad_role.total_matches}]"
        return (
            f'{self.red_flags}'
            f'{self.player}: {self.total_predicted_points:.2f} '
            f'({self.million_per_total_predicted_points:.2f}£) '
            f'team['
            f'{100 * xg_share:.1f}% xG '
            f'{100 * xa_share:.1f}% xA'
            f'] '
            f'{role_suffix}'
        )


class GameweekPrediction:
    """
    Predictions for a single gameweek.

    Contains all fixture and player predictions for one gameweek.
    Predictions stored as dicts keyed by player_id for efficient merging.
    """

    def __init__(self, gameweek: int):
        self.gameweek = gameweek
        self.team_fixture_predictions: dict[int, TeamFixturePrediction] = {}
        self.player_fixture_predictions: dict[int, PlayerFixturePrediction] = {}

    def add_team_fixture_prediction(self, prediction: TeamFixturePrediction):
        self.team_fixture_predictions[prediction.fixture.team_id] = prediction

    def add_player_fixture_prediction(self, prediction: PlayerFixturePrediction):
        self.player_fixture_predictions[prediction.fixture.player_id] = prediction


class GameweekPredictions:
    """
    Aggregates predictions across multiple gameweeks.

    Computes all aggregations on-the-fly from gameweek_predictions.
    No pre-computed attributes - everything calculated on demand.
    """
    gameweek_predictions: list[GameweekPrediction]
    pos: PlayerType | None
    team_only: bool

    def __init__(self, season: Season, gameweek_predictions: list[GameweekPrediction], next_gameweek: int, min_history_gws: int):
        self.season = season
        self.gameweek_predictions = gameweek_predictions
        self.next_gameweek = next_gameweek
        self.min_history_gws = min_history_gws
        self.pos = None
        self.team_only = False
        self.my_team = [
            67, 470,
            373, 411, 72, 436, 261,
            16, 119, 384, 390, 169,
            430, 136, 283,
        ]

    @property
    def teams_xgc_exp_asc(self) -> list[TeamTotalPrediction]:
        return sorted(self.teams_total_predictions, key=lambda p: p.xgc_exp.p)

    @property
    def teams_total_cs_desc(self) -> list[TeamTotalPrediction]:
        return sorted(self.teams_total_predictions, key=lambda p: -p.cs_prediction.p)

    @property
    def players_points_exp_desc(self) -> list[PlayerTotalPrediction]:
        return sorted(self.players_total_predictions, key=lambda p: -p.total_exp_points)

    @property
    def players_total_points_desc(self) -> list[PlayerTotalPrediction]:
        return sorted(self.players_total_predictions, key=lambda p: -p.total_predicted_points)

    @property
    def players_points_exp_per_value_desc(self) -> list[PlayerTotalPrediction]:
        return sorted(self.players_total_predictions, key=lambda p: p.million_per_total_exp_points)

    @property
    def players_total_points_per_value_desc(self) -> list[PlayerTotalPrediction]:
        return sorted(self.players_total_predictions, key=lambda p: p.million_per_total_predicted_points)

    @property
    def teams_total_predictions(self) -> list[TeamTotalPrediction]:
        total_predictions = []
        for team_id in self.gameweek_predictions[0].team_fixture_predictions:
            total_predictions.append(TeamTotalPrediction(
                [gp.team_fixture_predictions[team_id] for gp in self.gameweek_predictions],
            ))
        return total_predictions

    def my_fpl_players(self, sort_key: Callable[[PlayerTotalPrediction], float] = lambda p: p.million_per_total_exp_points) -> list[PlayerTotalPrediction]:
        return self.players_total_predictions(player_whitelist=Query.my_fpl_presence_ids(self.next_gameweek - 1), sort_key=sort_key)

    def top_fpl_players(
        self,
        position: PlayerType | None = None,
        sort_key: Callable[[PlayerTotalPrediction], float] = lambda p: p.million_per_total_exp_points,
    ) -> list[PlayerTotalPrediction]:
        return self.players_total_predictions(
            position=position,
            sort_key=sort_key,
        )

    def my_draft_players(self, sort_key: Callable[[PlayerTotalPrediction], float] = lambda p: -p.total_exp_points) -> list[PlayerTotalPrediction]:
        return self.players_total_predictions(player_whitelist=Query.my_draft_presence_ids(self.next_gameweek - 1), sort_key=sort_key)

    def top_draft_players(
        self,
        position: PlayerType | None = None,
        sort_key: Callable[[PlayerTotalPrediction], float] = lambda p: -p.total_exp_points,
    ) -> list[PlayerTotalPrediction]:
        return self.players_total_predictions(
            position=position,
            player_blacklist=Query.all_draft_presence_ids(self.next_gameweek - 1) - Query.my_draft_presence_ids(self.next_gameweek - 1),
            sort_key=sort_key,
        )

    def players_total_predictions(
        self,
        position: PlayerType | None = None,
        player_whitelist: set[int] | None = None,
        player_blacklist: set[int] | None = None,
        sort_key: Callable[[PlayerTotalPrediction], float] = lambda p: -p.total_exp_points,
    ) -> list[PlayerTotalPrediction]:
        total_predictions = []
        for player_id in self.gameweek_predictions[0].player_fixture_predictions:
            if position is not None and Query.player(player_id).player_type != position:
                continue
            if player_whitelist and player_id not in player_whitelist:
                continue
            if player_blacklist and player_id in player_blacklist:
                continue
            total_predictions.append(PlayerTotalPrediction(
                self.season,
                [gp.player_fixture_predictions[player_id] for gp in self.gameweek_predictions],
                min_history_gws=self.min_history_gws,
            ))
        return sorted(total_predictions, key=sort_key)
