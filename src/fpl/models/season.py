from src.fpl.aggregate import Aggregate
from src.fpl.models.immutable import Fixture, Teams, PlayerFixture, Players, Player, PlayerType, PlayerFixtures
from src.fpl.models.stats import (
    CleanSheetStatsAggregate, XGFixtureStatsAggregate,
    XAFixtureStatsAggregate, PlayerXGStatsAggregate, PlayerXAStatsAggregate,
)


class TeamStats:

    team_id: int
    clean_sheet_stats: CleanSheetStatsAggregate
    xg_stats: XGFixtureStatsAggregate
    xa_stats: XAFixtureStatsAggregate
    season: 'Season'

    def __init__(self, team_id: int, season: 'Season'):
        self.team_id = team_id
        self.clean_sheet_stats = CleanSheetStatsAggregate()
        self.xg_stats = XGFixtureStatsAggregate()
        self.xa_stats = XAFixtureStatsAggregate()
        self.season = season

    def add_fixture_and_stats(self, fixture: Fixture):
        self.clean_sheet_stats.add_fixture(fixture)
        self.xg_stats.add_fixture(fixture)
        self.xa_stats.add_fixture(fixture)
        if fixture.home.team_id == self.team_id:
            self.clean_sheet_stats.add_home_stats(fixture)
            self.xg_stats.add_home_stats(fixture)
            self.xa_stats.add_home_stats(fixture)
        elif fixture.away.team_id == self.team_id:
            self.clean_sheet_stats.add_away_stats(fixture)
            self.xg_stats.add_away_stats(fixture)
            self.xa_stats.add_away_stats(fixture)
        else:
            raise ValueError(f"Given {fixture=} contains to {self.team_id=}.")

    @property
    def cs_last_5(self) -> Aggregate:
        return self.cs_last(5)

    @property
    def cs_last_3(self) -> Aggregate:
        return self.cs_last(3)

    @property
    def cs_last_1(self) -> Aggregate:
        return self.cs_last(1)

    def cs_last(self, n: int) -> Aggregate:
        assert n > 0
        last_gw = self.season.gameweek
        total = 0.
        count = 0.
        for i in range(n):
            for fixture in self.clean_sheet_stats.fixtures[last_gw - i]:
                clean_sheet = fixture.home_clean_sheet if fixture.home.team_id == self.team_id else fixture.away_clean_sheet
                total += clean_sheet
                count += 1
        return Aggregate(total, count)

    @property
    def xg_form_norm_own_5(self) -> Aggregate:
        return self.xg_form_norm(5, 'own')

    @property
    def xg_form_norm_own_3(self) -> Aggregate:
        return self.xg_form_norm(3, 'own')

    @property
    def xg_form_norm_own_1(self) -> Aggregate:
        return self.xg_form_norm(1, 'own')

    @property
    def xg_form_norm_season_5(self) -> Aggregate:
        return self.xg_form_norm(5, 'season')

    @property
    def xg_form_norm_season_3(self) -> Aggregate:
        return self.xg_form_norm(3, 'season')

    @property
    def xg_form_norm_season_1(self) -> Aggregate:
        return self.xg_form_norm(1, 'season')

    @property
    def xa_form_norm_own_5(self) -> Aggregate:
        return self.xa_form_norm(5, 'own')

    @property
    def xa_form_norm_own_3(self) -> Aggregate:
        return self.xa_form_norm(3, 'own')

    @property
    def xa_form_norm_own_1(self) -> Aggregate:
        return self.xa_form_norm(1, 'own')

    @property
    def xa_form_norm_season_5(self) -> Aggregate:
        return self.xa_form_norm(5, 'season')

    @property
    def xa_form_norm_season_3(self) -> Aggregate:
        return self.xa_form_norm(3, 'season')

    @property
    def xa_form_norm_season_1(self) -> Aggregate:
        return self.xa_form_norm(1, 'season')

    def xg_form(self, n: int) -> Aggregate:
        assert n > 0
        last_gw = self.season.gameweek
        total = 0.
        count = 0.
        for i in range(n):
            for fixture in self.xg_stats.fixtures[last_gw - i]:
                xg = fixture.home.expected_goals if fixture.home.team_id == self.team_id else fixture.away.expected_goals
                total += xg
                count += 1
        return Aggregate(total, count)

    def xa_form(self, n: int) -> Aggregate:
        assert n > 0
        last_gw = self.season.gameweek
        total = 0.
        count = 0.
        for i in range(n):
            for fixture in self.xa_stats.fixtures[last_gw - i]:
                xa = fixture.home.expected_assists if fixture.home.team_id == self.team_id else fixture.away.expected_assists
                total += xa
                count += 1
        return Aggregate(total, count)

    def xg_form_norm(self, n: int, kind: str) -> Aggregate:
        assert n > 0
        last_gw = self.season.gameweek
        total = 0.
        count = 0.
        for i in range(n):
            for fixture in self.xg_stats.fixtures[last_gw - i]:
                xg = fixture.home.expected_goals if fixture.home.team_id == self.team_id else fixture.away.expected_goals
                fdr = fixture.home.difficulty if fixture.home.team_id == self.team_id else fixture.away.difficulty
                denom = self._get_xg_denom(kind, fdr)
                total += xg / denom if denom else 0.
                count += 1
        return Aggregate(total, count)

    def xa_form_norm(self, n: int, kind: str) -> Aggregate:
        assert n > 0
        last_gw = self.season.gameweek
        total = 0.
        count = 0.
        for i in range(n):
            for fixture in self.xa_stats.fixtures[last_gw - i]:
                xa = fixture.home.expected_assists if fixture.home.team_id == self.team_id else fixture.away.expected_assists
                fdr = fixture.home.difficulty if fixture.home.team_id == self.team_id else fixture.away.difficulty
                denom = self._get_xa_denom(kind, fdr)
                total += xa / denom if denom else 0.
                count += 1
        return Aggregate(total, count)

    def _get_xg_denom(self, kind: str, fdr: int) -> float:
        if kind == 'own':
            return self.xg_stats.fdr_norm[fdr]
        else:
            return self.season.xg_stats.fdr_norm[fdr]

    def _get_xa_denom(self, kind: str, fdr: int) -> float:
        if kind == 'own':
            return self.xa_stats.fdr_norm[fdr]
        else:
            return self.season.xa_stats.fdr_norm[fdr]

    @property
    def team_name(self) -> str:
        return Teams.get_one(team_id=self.team_id).name


class PlayerStats:

    player_id: int
    fixtures: dict[int, list[PlayerFixture]]
    xg_stats: PlayerXGStatsAggregate
    xa_stats: PlayerXAStatsAggregate
    season: 'Season'

    def __init__(self, player_id: int, season: 'Season'):
        super().__init__()
        self.fixtures = {gw: [] for gw in range(1, 39)}
        self.player_id = player_id
        self.xg_stats = PlayerXGStatsAggregate()
        self.xa_stats = PlayerXAStatsAggregate()
        self.season = season

    def add_player_fixture(self, pf: PlayerFixture):
        assert pf.player_id == self.player_id
        self.fixtures[pf.gameweek].append(pf)
        self.xg_stats.add_player_fixture(pf)
        self.xa_stats.add_player_fixture(pf)

    @property
    def xg_last_5(self) -> Aggregate:
        return self.last(5, 'xg')

    @property
    def xg_last_3(self) -> Aggregate:
        return self.last(3, 'xg')

    @property
    def xg_last_1(self) -> Aggregate:
        return self.last(1, 'xg')

    @property
    def xa_last_5(self) -> Aggregate:
        return self.last(5, 'xa')

    @property
    def xa_last_3(self) -> Aggregate:
        return self.last(3, 'xa')

    @property
    def xa_last_1(self) -> Aggregate:
        return self.last(1, 'xa')

    def share_last(self, n: int, metric: str) -> float:
        player_metric = self.last(n, metric)
        team_metric = (
            self.season.team_stats[Players.by_id(self.player_id).team_id].xg_form(n)
            if metric == 'xg' else
            self.season.team_stats[Players.by_id(self.player_id).team_id].xa_form(n)
        )
        return player_metric.total / team_metric.total if team_metric.count else 0.

    def last(self, n: int, metric: str) -> Aggregate:
        assert n > 0
        last_gw = self.season.gameweek
        total = 0.
        count = 0.
        for i in range(n):
            for pf in self.fixtures[last_gw - i]:
                total += pf.expected_goals if metric == 'xg' else pf.expected_assists
                count += 1
        return Aggregate(total, count)

    @property
    def player(self) -> Player:
        return Players.by_id(self.player_id)

    def __repr__(self):
        return (
            f'{self.player.web_name}: '
            f'xG%(season)={self.share_last(self.season.gameweek, "xg"):.2f} '
            f'xG(5)={self.xg_last_5.total:.2f} xG(3)={self.xg_last_3.total:.2f} xG(1)={self.xg_last_1.total:.2f}'
            f'xA%(season)={self.share_last(self.season.gameweek, "xa"):.2f} '
            f'xA(5)={self.xa_last_5.total:.2f} xA(3)={self.xa_last_3.total:.2f} xA(1)={self.xa_last_1.total:.2f}'
        )


class Season:

    gameweek: int
    clean_sheet_stats: CleanSheetStatsAggregate
    xg_stats: XGFixtureStatsAggregate
    xa_stats: XAFixtureStatsAggregate
    team_stats: dict[int, TeamStats]
    player_stats: dict[int, PlayerStats]

    # view options
    pos: PlayerType | None

    def __init__(self):
        self.gameweek = 0
        self.clean_sheet_stats = CleanSheetStatsAggregate()
        self.xg_stats = XGFixtureStatsAggregate()
        self.xa_stats = XAFixtureStatsAggregate()
        self.team_stats = {team.team_id: TeamStats(team.team_id, self) for team in Teams.items}
        self.player_stats = {player_id: PlayerStats(player_id, self) for player_id in Players.items_by_id}

        # view options
        self.pos = None

    def play(self, fixtures: list[Fixture]):
        for fixture in fixtures:
            assert fixture.gameweek == self.gameweek + 1

            self.clean_sheet_stats.add_fixture(fixture)
            self.clean_sheet_stats.add_home_stats(fixture)
            self.clean_sheet_stats.add_away_stats(fixture)

            self.xg_stats.add_fixture(fixture)
            self.xg_stats.add_home_stats(fixture)
            self.xg_stats.add_away_stats(fixture)

            self.xa_stats.add_fixture(fixture)
            self.xa_stats.add_home_stats(fixture)
            self.xa_stats.add_away_stats(fixture)

            self.team_stats[fixture.home.team_id].add_fixture_and_stats(fixture)
            self.team_stats[fixture.away.team_id].add_fixture_and_stats(fixture)

            for pf in PlayerFixtures.by_fixture(fixture.fixture_id):
                self.player_stats[pf.player_id].add_player_fixture(pf)

        self.gameweek += 1

    @property
    def top_xg_players(self) -> list[PlayerStats]:
        return sorted(
            filter(
                lambda ps: self.pos is None or ps.player.player_type == self.pos,
                self.player_stats.values(),
            ),
            key=lambda ps: -ps.xg_last_5.p,
        )
