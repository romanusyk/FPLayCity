"""
Statistics aggregation classes for fixtures and players.

Classes:
- StatsAggregate: Base aggregator with FDR and side (home/away) breakdowns
- FixtureStatsAggregate: Base for fixture-level stats (extends StatsAggregate)
  - CleanSheetStatsAggregate: Aggregates clean sheets by FDR/side
  - XGFixtureStatsAggregate: Aggregates expected goals by FDR/side
  - XAFixtureStatsAggregate: Aggregates expected assists by FDR/side
  - DCFixtureStatsAggregate: Aggregates defensive contribution by FDR/side
  - PtsFixtureStatsAggregate: Aggregates points by FDR/side
- PlayerXGStatsAggregate: Aggregates player xG by FDR/side
- PlayerXAStatsAggregate: Aggregates player xA by FDR/side
- PlayerDCStatsAggregate: Aggregates player DC by FDR/side

Note: All aggregates track normalized values (fdr_norm) for scaling predictions.
"""
from collections import defaultdict
from locale import T_FMT
from src.fpl.aggregate import Aggregate
from src.fpl.models.immutable import Fixture, TeamFixture, PlayerFixture, Metric, PlayerType, Query


class StatsAggregate:

    fdr_aggregate: dict[int, Aggregate]
    side_aggregate: dict[str, Aggregate]

    def __init__(self):
        super().__init__()
        self.fdr_aggregate = {fdr: Aggregate(0, 0) for fdr in [1, 2, 3, 4, 5]}
        self.side_aggregate = {side: Aggregate(0, 0) for side in ['home', 'away']}

    @property
    def total(self) -> Aggregate:
        return self.side_aggregate['home'] + self.side_aggregate['away']

    @property
    def fdr_norm(self) -> dict[int, float]:
        f"""
        Returns FDR aggregates ratios to the total aggregate.
        """
        return {
            fdr: agg.p / self.total.p if self.total.p else 0.
            for fdr, agg in self.fdr_aggregate.items()
        }


class FixtureStatsAggregate(StatsAggregate):

    fixtures: dict[int, list[Fixture]]

    def __init__(self):
        super().__init__()
        self.fixtures = {gw: [] for gw in range(1, 39)}

    def fixture_to_aggregate(self, fixture: Fixture, side: str) -> Aggregate:
        raise NotImplemented

    def add_fixture(self, fixture: Fixture):
        self.fixtures[fixture.gameweek].append(fixture)

    def add_home_stats(self, fixture: Fixture):
        self.side_aggregate['home'] += self.fixture_to_aggregate(fixture, 'home')
        self.fdr_aggregate[fixture.home.difficulty] += self.fixture_to_aggregate(fixture, 'home')

    def add_away_stats(self, fixture: Fixture):
        self.side_aggregate['away'] += self.fixture_to_aggregate(fixture, 'away')
        self.fdr_aggregate[fixture.away.difficulty] += self.fixture_to_aggregate(fixture, 'away')


class CleanSheetStatsAggregate(FixtureStatsAggregate):

    def fixture_to_aggregate(self, fixture: Fixture, side: str) -> Aggregate:
        return Aggregate(fixture.home_clean_sheet if side == 'home' else fixture.away_clean_sheet, 1)


class XGFixtureStatsAggregate(FixtureStatsAggregate):

    def fixture_to_aggregate(self, fixture: Fixture, side: str) -> Aggregate:
        return Aggregate(fixture.home.expected_goals if side == 'home' else fixture.away.expected_goals, 1)


class XAFixtureStatsAggregate(FixtureStatsAggregate):

    def fixture_to_aggregate(self, fixture: Fixture, side: str) -> Aggregate:
        return Aggregate(fixture.home.expected_assists if side == 'home' else fixture.away.expected_assists, 1)


class DCFixtureStatsAggregate(FixtureStatsAggregate):

    def fixture_to_aggregate(self, fixture: Fixture, side: str) -> Aggregate:
        return Aggregate(fixture.home.defensive_contribution if side == 'home' else fixture.away.defensive_contribution, 1)


class PtsFixtureStatsAggregate(FixtureStatsAggregate):

    def fixture_to_aggregate(self, fixture: Fixture, side: str) -> Aggregate:
        return Aggregate(fixture.home.total_points if side == 'home' else fixture.away.total_points, 1)


class PlayerXGStatsAggregate(StatsAggregate):

    def add_player_fixture(self, pf: PlayerFixture):
        self.side_aggregate[pf.side] += Aggregate(pf.expected_goals, 1)
        self.fdr_aggregate[pf.team_fixture.difficulty] += Aggregate(pf.expected_goals, 1)


class PlayerXAStatsAggregate(StatsAggregate):

    def add_player_fixture(self, pf: PlayerFixture):
        self.side_aggregate[pf.side] += Aggregate(pf.expected_assists, 1)
        self.fdr_aggregate[pf.team_fixture.difficulty] += Aggregate(pf.expected_assists, 1)


class PlayerDCStatsAggregate(StatsAggregate):

    def add_player_fixture(self, pf: PlayerFixture):
        self.side_aggregate[pf.side] += Aggregate(pf.defensive_contribution, 1)
        self.fdr_aggregate[pf.team_fixture.difficulty] += Aggregate(pf.defensive_contribution, 1)


class StatsQuery:

    teams_metrics = defaultdict(lambda: Aggregate(0, 0))
    players_metrics = defaultdict(lambda: Aggregate(0, 0))

    @classmethod
    def build(cls, next_gw: int):
        for gameweek in range(1, next_gw):
            for fixture in Query.fixtures_by_gameweek(gameweek):
                for metric in Metric:
                    for team_fixture in [fixture.home, fixture.away]:
                        fdr = team_fixture.difficulty
                        if metric in [Metric.XGC, Metric.XG, Metric.XA, Metric.DC, Metric.CS]:
                            cls.teams_metrics[(metric, team_fixture.side, fdr)] += Aggregate(
                                fixture.get_metric(metric, team_fixture.team_id), 1,
                            )
                        for player_fixture in team_fixture.player_fixtures:
                            cls.players_metrics[(metric, team_fixture.side, fdr, player_fixture.player.player_type)] += Aggregate(
                                player_fixture.get_metric(metric), player_fixture.minutes / 90,
                            )

    @classmethod
    def teams_avg(
        cls,
        metric: Metric,
        side: str,
        fdr: int,
    ) -> Aggregate:
        """
        Returns the aggregate of the metric for teams
        in matches of the given FDR and side.
        """
        return cls.teams_metrics[(metric, side, fdr)]

    @classmethod
    def team_fixture_avg(
        cls,
        metric: Metric,
        team_fixture: TeamFixture,
    ) -> Aggregate:
        return cls.teams_avg(metric, team_fixture.side, team_fixture.difficulty)

    @classmethod
    def players_avg(
        cls,
        metric: Metric,
        side: str,
        fdr: int,
        position: PlayerType,
    ) -> Aggregate:
        """
        Returns the aggregate of the metric for players of the given position
        in matches of the given FDR and side.
        """
        return cls.players_metrics[(metric, side, fdr, position)]


    @classmethod
    def player_fixture_avg(cls, metric: Metric, player_fixture: PlayerFixture) -> Aggregate:
        return cls.players_avg(
            metric,
            player_fixture.team_fixture.side,
            player_fixture.team_fixture.difficulty,
            player_fixture.player.player_type,
        )

    @classmethod
    def team_w(cls, metric: Metric, team_id: int, first_gw: int, last_gw: int, avg: bool = False) -> Aggregate:
        history = [
            tf
            for tf in Query.team_fixtures_by_team_and_gameweeks(team_id, first_gw, last_gw)
        ]
        total_metric = 0.
        total_count = 0
        for tf in history:
            total_metric += tf.get_metric(metric) if not avg else cls.team_fixture_avg(metric, tf).p
            total_count += 1
        return Aggregate(total_metric, total_count)

    @classmethod
    def player_w(cls, metric: Metric, player_id: int, first_gw: int, last_gw: int, avg: bool = False) -> Aggregate:
        history = [
            pf
            for pf in Query.player_fixtures_by_player_and_gameweeks(player_id, first_gw, last_gw)
        ]
        total_metric = 0.
        total_count = 0
        for pf in history:
            total_metric += pf.get_metric(metric) if not avg else cls.player_fixture_avg(metric, pf).p
            total_count += 1
        return Aggregate(total_metric, total_count)
