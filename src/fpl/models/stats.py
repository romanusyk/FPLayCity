from src.fpl.aggregate import Aggregate
from src.fpl.models.immutable import Fixture, PlayerFixture


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
