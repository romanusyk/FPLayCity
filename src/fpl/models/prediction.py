from typing import Generic, TypeVar

from src.fpl.aggregate import Aggregate
from src.fpl.models.immutable import Fixture, Teams, PlayerFixture, Players, Player
from src.fpl.models.season import Season


class FixturePrediction:

    fixture: Fixture
    home_prediction: Aggregate
    away_prediction: Aggregate

    def __init__(self, fixture: Fixture, home_prediction: Aggregate, away_prediction: Aggregate):
        self.fixture = fixture
        self.home_prediction = home_prediction
        self.away_prediction = away_prediction

    def __repr__(self):
        return (
            f'{self.home_prediction} '
            f'{Teams.get_one(team_id=self.fixture.home.team_id).name} '
            f'{self.fixture.home.score}:{self.fixture.away.score} '
            f'{Teams.get_one(team_id=self.fixture.away.team_id).name} '
            f'{self.away_prediction}'
        )


class TeamPredictions:

    team_id: int
    predictions: list[FixturePrediction]

    def __init__(self, team_id: int):
        self.team_id = team_id
        self.predictions = []

    @property
    def predicted_sum(self) -> float:
        return sum(
            pr.home_prediction.p
            if pr.fixture.home.team_id == self.team_id
            else pr.away_prediction.p
            for pr in self.predictions
        )

    @property
    def actual_sum(self) -> int:
        return sum(
            pr.fixture.home_clean_sheet
            if pr.fixture.home.team_id == self.team_id
            else pr.fixture.away_clean_sheet
            for pr in self.predictions
        )

    def __repr__(self):
        summary = []
        for pr in self.predictions:
            side = (
                'H'
                if pr.fixture.home.team_id == self.team_id
                else 'A'
            )
            opponent = (
                pr.fixture.away
                if pr.fixture.home.team_id == self.team_id
                else pr.fixture.home
            )
            aggregate = (
                pr.home_prediction
                if pr.fixture.home.team_id == self.team_id
                else pr.away_prediction
            )
            full_score = f'{pr.fixture.home.score}:{pr.fixture.away.score}'
            summary.append(f"{Teams.get_one(team_id=opponent.team_id).name} ({side}) {full_score} = {aggregate.p:.2f}")
        return (
            f"{Teams.get_one(team_id=self.team_id).name}: {self.predicted_sum:.2f} | {self.actual_sum}"
            f" = ({', '.join(summary)})"
        )


class PlayerFixtureAggregate:

    fixture: PlayerFixture
    prediction: Aggregate

    def __init__(self, fixture: PlayerFixture, prediction: Aggregate):
        self.fixture = fixture
        self.prediction = prediction


class PlayerFixturePrediction:

    aggregate: PlayerFixtureAggregate

    def __init__(self, aggregate: PlayerFixtureAggregate):
        self.aggregate = aggregate

    @property
    def fixture(self) -> PlayerFixture:
        return self.aggregate.fixture

    @property
    def prediction(self) -> Aggregate:
        return self.aggregate.prediction

    @property
    def predicted(self) -> float:
        raise NotImplemented

    @property
    def actual(self) -> float:
        raise NotImplemented


class PlayerFixtureCsPrediction(PlayerFixturePrediction):

    @property
    def predicted(self) -> float:
        return self.prediction.p

    @property
    def actual(self) -> float:
        return self.fixture.clean_sheets or 0.

    def __repr__(self):
        side = 'H' if self.fixture.was_home else 'A'
        return (
            f'{self.fixture.opponent_team.name} ({side}) -> '
            f'{self.prediction.p:.2f}: '
            f'{self.fixture.clean_sheets}'
        )


class PlayerFixtureXgPrediction(PlayerFixturePrediction):

    @property
    def predicted(self) -> float:
        return self.prediction.p

    @property
    def actual(self) -> float:
        return self.fixture.expected_goals or 0.

    def __repr__(self):
        side = 'H' if self.fixture.was_home else 'A'
        return (
            f'{self.fixture.opponent_team.name} ({side}) -> '
            f'{self.prediction.p:.2f}: '
            f'{self.fixture.expected_goals} '
            f'({int(100 * self.fixture.expected_goals_share)}%) -> '
            f'{self.fixture.goals_scored}'
        )


class PlayerFixtureXaPrediction(PlayerFixturePrediction):

    @property
    def predicted(self) -> float:
        return self.prediction.p

    @property
    def actual(self) -> float:
        return self.fixture.expected_assists or 0.

    def __repr__(self):
        side = 'H' if self.fixture.was_home else 'A'
        return (
            f'{self.fixture.opponent_team.name} ({side}) -> '
            f'{self.prediction.p:.2f}: '
            f'{self.fixture.expected_assists} '
            f'({int(100 * self.fixture.expected_assists_share)}%) -> '
            f'{self.fixture.assists}'
        )


class PlayerFixtureDcPrediction(PlayerFixturePrediction):

    @property
    def predicted(self) -> float:
        return self.prediction.p

    @property
    def actual(self) -> float:
        return self.fixture.defensive_contribution or 0.

    def __repr__(self):
        side = 'H' if self.fixture.was_home else 'A'
        return (
            f'{self.fixture.opponent_team.name} ({side}) -> '
            f'{self.prediction.p:.2f}: '
            f'{self.fixture.defensive_contribution}'
        )


PlayerFixturePredictionT = TypeVar('PlayerFixturePredictionT', bound=PlayerFixturePrediction)


class PlayerPredictions(Generic[PlayerFixturePredictionT]):

    player_id: int
    predictions: list[PlayerFixturePredictionT]

    def __init__(self, player_id: int):
        self.player_id = player_id
        self.predictions = []

    @property
    def predicted_sum(self) -> float:
        return sum(pr.predicted for pr in self.predictions)

    @property
    def actual_sum(self) -> int:
        return sum(pr.actual for pr in self.predictions)

    def __repr__(self):
        summary = []
        for pr in self.predictions:
            summary.append(pr.__repr__())
        return (
            f"{Players.by_id(self.player_id).web_name}: {self.predicted_sum:.2f} | {self.actual_sum}"
            f" = ({', '.join(summary)})"
        )


class PlayerTotalPrediction:

    def __init__(
            self,
            player: Player,
            cs: PlayerPredictions[PlayerFixtureCsPrediction],
            xg: PlayerPredictions[PlayerFixtureXgPrediction],
            xa: PlayerPredictions[PlayerFixtureXaPrediction],
            dc: PlayerPredictions[PlayerFixtureDcPrediction],
    ):
        self.player = player
        self.cs = cs
        self.xg = xg
        self.xa = xa
        self.dc = dc
        self.prediction = Aggregate(self.total_points, 1)
        self.prediction_per_value = Aggregate(self.total_points, player.now_cost)

    @property
    def cs_points(self) -> float:
        return self.cs.predicted_sum * self.player.clean_sheet_points

    @property
    def xg_points(self) -> float:
        return self.xg.predicted_sum * self.player.goal_points

    @property
    def xa_points(self) -> float:
        return self.xa.predicted_sum * self.player.assist_points

    @property
    def dc_points(self) -> float:
        return self.dc.predicted_sum * self.player.dc_points

    @property
    def total_points(self) -> float:
        return self.cs_points + self.xg_points + self.xa_points + self.dc_points

    @property
    def total_points_per_value(self) -> float:
        return self.total_points / self.player.now_cost

    def __repr__(self):
        return (
            f'{self.player}: {self.total_points:.1f} ({self.total_points_per_value:.1f}/£) = '
            f'{self.xg_points:.1f} xG '
            f'+ {self.xa_points:.1f} xA '
            f'+ {self.dc_points:.1f} DC '
            f'+ {self.cs_points:.1f} CS'
        )


class GameweekPredictions:

    team_predictions: dict[int, TeamPredictions]
    player_cs_predictions: dict[int, PlayerPredictions[PlayerFixtureCsPrediction]]
    player_xg_predictions: dict[int, PlayerPredictions[PlayerFixtureXgPrediction]]
    player_xa_predictions: dict[int, PlayerPredictions[PlayerFixtureXaPrediction]]
    player_dc_predictions: dict[int, PlayerPredictions[PlayerFixtureDcPrediction]]

    my_team = [
        502, 470,
        291, 575, 72, 191, 541,
        381, 449, 582, 299, 86,
        430, 525, 252,
    ]

    def __init__(self, season: Season):
        self.team_predictions = {team.team_id: TeamPredictions(team.team_id) for team in Teams.items}
        self.player_cs_predictions = {player_id: PlayerPredictions(player_id) for player_id in Players.items_by_id}
        self.player_xg_predictions = {player_id: PlayerPredictions(player_id) for player_id in Players.items_by_id}
        self.player_xa_predictions = {player_id: PlayerPredictions(player_id) for player_id in Players.items_by_id}
        self.player_dc_predictions = {player_id: PlayerPredictions(player_id) for player_id in Players.items_by_id}
        self.season = season

    def add_team_prediction(self, prediction: FixturePrediction):
        self.team_predictions[prediction.fixture.home.team_id].predictions.append(prediction)
        self.team_predictions[prediction.fixture.away.team_id].predictions.append(prediction)

    def add_player_cs_prediction(self, prediction: PlayerFixtureCsPrediction):
        self.player_cs_predictions[prediction.fixture.player_id].predictions.append(prediction)

    def add_player_xg_prediction(self, prediction: PlayerFixtureXgPrediction):
        self.player_xg_predictions[prediction.fixture.player_id].predictions.append(prediction)

    def add_player_xa_prediction(self, prediction: PlayerFixtureXaPrediction):
        self.player_xa_predictions[prediction.fixture.player_id].predictions.append(prediction)

    def add_player_dc_prediction(self, prediction: PlayerFixtureDcPrediction):
        self.player_dc_predictions[prediction.fixture.player_id].predictions.append(prediction)

    @property
    def teams_desc(self) -> list[TeamPredictions]:
        return sorted(self.team_predictions.values(), key=lambda pr: -pr.predicted_sum)

    @property
    def players_xg_desc(self) -> list[PlayerPredictions]:
        return sorted(self.player_xg_predictions.values(), key=lambda pr: -pr.predicted_sum)

    @property
    def players_xa_desc(self) -> list[PlayerPredictions]:
        return sorted(self.player_xa_predictions.values(), key=lambda pr: -pr.predicted_sum)

    @property
    def players_dc_desc(self) -> list[PlayerPredictions]:
        return sorted(self.player_dc_predictions.values(), key=lambda pr: -pr.predicted_sum)

    @property
    def players_points_desc(self) -> list[PlayerTotalPrediction]:
        predictions = []
        for player in Players.items_by_id.values():
            if self.season.pos is not None and player.player_type != self.season.pos:
                continue
            if self.my_team and player.player_id not in self.my_team:
                continue
            predictions.append(PlayerTotalPrediction(
                player=player,
                cs=self.player_cs_predictions[player.player_id],
                xg=self.player_xg_predictions[player.player_id],
                xa=self.player_xa_predictions[player.player_id],
                dc=self.player_dc_predictions[player.player_id],
            ))
        return sorted(predictions, key=lambda pr: -pr.prediction.p)

    @property
    def players_points_per_value_desc(self) -> list[PlayerTotalPrediction]:
        return sorted(self.players_points_desc, key=lambda pr: -pr.prediction_per_value.p)
