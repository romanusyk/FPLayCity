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
from functools import reduce
import operator

from src.fpl.aggregate import Aggregate
from src.fpl.models.immutable import TeamFixture, PlayerFixture, Player, Team, Query, PlayerType


class TeamFixturePrediction:

    fixture: TeamFixture
    cs_prediction: Aggregate

    def __init__(self, fixture: TeamFixture, cs_prediction: Aggregate):
        self.fixture = fixture
        self.cs_prediction = cs_prediction

    def __repr__(self):
        return f'{self.fixture.team}={self.cs_prediction} ({self.fixture.opponent_team})'


class PlayerFixturePrediction:

    fixture: PlayerFixture
    cs_prediction: Aggregate
    xg_prediction: Aggregate
    xa_prediction: Aggregate
    dc_prediction: Aggregate

    def __init__(
            self,
            fixture: PlayerFixture,
            cs_prediction: Aggregate,
            xg_prediction: Aggregate,
            xa_prediction: Aggregate,
            dc_prediction: Aggregate,
    ):
        self.fixture = fixture
        self.cs_prediction = cs_prediction
        self.xg_prediction = xg_prediction
        self.xa_prediction = xa_prediction
        self.dc_prediction = dc_prediction

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
    def cs_prediction(self) -> Aggregate:
        return self._agg([fp.cs_prediction for fp in self.fixture_predictions])



class PlayerTotalPrediction:

    fixture_predictions: list[PlayerFixturePrediction]

    def __init__(
            self,
            fixture_predictions: list[PlayerFixturePrediction],
    ):
        self.fixture_predictions = fixture_predictions

    @property
    def player(self) -> Player:
        return Query.player(self.fixture_predictions[0].fixture.player_id)

    @staticmethod
    def _agg(aggregates: list[Aggregate]):
        return reduce(operator.add, aggregates)

    @property
    def cs_prediction(self) -> Aggregate:
        return self._agg([fp.cs_prediction for fp in self.fixture_predictions])

    @property
    def xg_prediction(self) -> Aggregate:
        return self._agg([fp.xg_prediction for fp in self.fixture_predictions])

    @property
    def xa_prediction(self) -> Aggregate:
        return self._agg([fp.xa_prediction for fp in self.fixture_predictions])

    @property
    def dc_prediction(self) -> Aggregate:
        return self._agg([fp.dc_prediction for fp in self.fixture_predictions])

    @property
    def cs_predicted_points(self) -> float:
        return self.cs_prediction.p * self.player.clean_sheet_points

    @property
    def xg_predicted_points(self) -> float:
        return self.xg_prediction.p * self.player.goal_points

    @property
    def xa_predicted_points(self) -> float:
        return self.xa_prediction.p * self.player.assist_points

    @property
    def dc_predicted_points(self) -> float:
        return self.dc_prediction.p * self.player.dc_points

    @property
    def total_predicted_points(self) -> float:
        return self.cs_predicted_points + self.xg_predicted_points + self.xa_predicted_points + self.dc_predicted_points

    @property
    def total_predicted_points_per_value(self) -> float:
        return self.total_predicted_points / self.player.now_cost

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

    def __repr__(self):
        return (
            f'{self.player}: {self.total_predicted_points:.1f} | '
            f'{self.actual_points} '
            f'({self.total_predicted_points_per_value:.1f}/£) = '
            f'{self.xg_predicted_points:.1f} xG '
            f'+ {self.xa_predicted_points:.1f} xA '
            f'+ {self.dc_predicted_points:.1f} DC '
            f'+ {self.cs_predicted_points:.1f} CS'
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

    def __init__(self, gameweek_predictions: list[GameweekPrediction]):
        self.gameweek_predictions = gameweek_predictions
        self.pos = None

    @property
    def teams_total_cs_desc(self) -> list[TeamTotalPrediction]:
        return sorted(self.teams_total_predictions, key=lambda p: -p.cs_prediction.p)

    @property
    def players_total_cs_desc(self) -> list[PlayerTotalPrediction]:
        return sorted(self.players_total_predictions, key=lambda p: -p.cs_predicted_points)

    @property
    def players_total_xg_desc(self) -> list[PlayerTotalPrediction]:
        return sorted(self.players_total_predictions, key=lambda p: -p.xg_predicted_points)

    @property
    def players_total_xa_desc(self) -> list[PlayerTotalPrediction]:
        return sorted(self.players_total_predictions, key=lambda p: -p.xa_predicted_points)

    @property
    def players_total_dc_desc(self) -> list[PlayerTotalPrediction]:
        return sorted(self.players_total_predictions, key=lambda p: -p.dc_predicted_points)
    
    @property
    def players_total_points_desc(self) -> list[PlayerTotalPrediction]:
        return sorted(self.players_total_predictions, key=lambda p: -p.total_predicted_points)

    @property
    def players_total_points_per_value_desc(self) -> list[PlayerTotalPrediction]:
        return sorted(self.players_total_predictions, key=lambda p: -p.total_predicted_points_per_value)

    @property
    def teams_total_predictions(self) -> list[TeamTotalPrediction]:
        total_predictions = []
        for team_id in self.gameweek_predictions[0].team_fixture_predictions:
            total_predictions.append(TeamTotalPrediction(
                [gp.team_fixture_predictions[team_id] for gp in self.gameweek_predictions],
            ))
        return total_predictions

    @property
    def players_total_predictions(self) -> list[PlayerTotalPrediction]:
        total_predictions = []
        for player_id in self.gameweek_predictions[0].player_fixture_predictions:
            if self.pos is not None and Query.player(player_id).player_type != self.pos:
                continue
            total_predictions.append(PlayerTotalPrediction(
                [gp.player_fixture_predictions[player_id] for gp in self.gameweek_predictions],
            ))
        return total_predictions
