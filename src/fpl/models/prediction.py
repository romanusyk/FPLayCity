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


class PlayerFixtureXgPrediction(PlayerFixturePrediction):

    @property
    def predicted(self) -> float:
        return self.prediction.total

    @property
    def actual(self) -> float:
        return self.fixture.expected_goals or 0.

    def __repr__(self):
        side = 'H' if self.fixture.was_home else 'A'
        return (
            f'{self.fixture.opponent_team.name} ({side}) -> '
            f'{self.prediction.total:.2f}: '
            f'{self.fixture.expected_goals} '
            f'({int(100 * self.fixture.expected_goals_share)}%) -> '
            f'{self.fixture.goals_scored}'
        )


class PlayerFixtureXaPrediction(PlayerFixturePrediction):

    @property
    def predicted(self) -> float:
        return self.prediction.total

    @property
    def actual(self) -> float:
        return self.fixture.expected_assists or 0.

    def __repr__(self):
        side = 'H' if self.fixture.was_home else 'A'
        return (
            f'{self.fixture.opponent_team.name} ({side}) -> '
            f'{self.prediction.total:.2f}: '
            f'{self.fixture.expected_assists} '
            f'({int(100 * self.fixture.expected_assists_share)}%) -> '
            f'{self.fixture.assists}'
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
            team_cs: TeamPredictions,
            xg: PlayerPredictions[PlayerFixtureXgPrediction],
            xa: PlayerPredictions[PlayerFixtureXaPrediction],
    ):
        self.player = player
        self.team_cs = team_cs
        self.xg = xg
        self.xa = xa
        self.prediction = Aggregate(
            self.team_cs.predicted_sum * player.clean_sheet_points
            + xg.predicted_sum * player.goal_points
            + xa.predicted_sum * player.assist_points,
            1
        )

    def __repr__(self):
        return (
            f'{self.player.web_name}: {self.prediction.p:.1f} = '
            f'{self.xg.predicted_sum:.2f} xG '
            f'+ {self.xa.predicted_sum:.2f} xA '
            f'+ {int(100 * self.team_cs.predicted_sum)}% CS'
        )


class GameweekPredictions:

    team_predictions: dict[int, TeamPredictions]
    player_xg_predictions: dict[int, PlayerPredictions[PlayerFixtureXgPrediction]]
    player_xa_predictions: dict[int, PlayerPredictions[PlayerFixtureXaPrediction]]

    def __init__(self, season: Season):
        self.team_predictions = {team.team_id: TeamPredictions(team.team_id) for team in Teams.items}
        self.player_xg_predictions = {player_id: PlayerPredictions(player_id) for player_id in Players.items_by_id}
        self.player_xa_predictions = {player_id: PlayerPredictions(player_id) for player_id in Players.items_by_id}
        self.season = season

    def add_team_prediction(self, prediction: FixturePrediction):
        self.team_predictions[prediction.fixture.home.team_id].predictions.append(prediction)
        self.team_predictions[prediction.fixture.away.team_id].predictions.append(prediction)

    def add_player_xg_prediction(self, prediction: PlayerFixtureXgPrediction):
        self.player_xg_predictions[prediction.fixture.player_id].predictions.append(prediction)

    def add_player_xa_prediction(self, prediction: PlayerFixtureXaPrediction):
        self.player_xa_predictions[prediction.fixture.player_id].predictions.append(prediction)

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
    def players_points_desc(self) -> list[PlayerTotalPrediction]:
        predictions = []
        for player in Players.items_by_id.values():
            if self.season.pos is not None and player.player_type != self.season.pos:
                continue
            predictions.append(PlayerTotalPrediction(
                player=player,
                team_cs=self.team_predictions[player.team_id],
                xg=self.player_xg_predictions[player.player_id],
                xa=self.player_xa_predictions[player.player_id],
            ))
        return sorted(predictions, key=lambda pr: -pr.prediction.p)
