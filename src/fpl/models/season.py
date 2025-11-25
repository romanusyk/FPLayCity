"""
Season state management with progressive fixture replay and statistics.

Classes:
- TeamStats: Per-team statistics aggregator
  - Tracks CS/xG/xA/DC/points by FDR and side (home/away)
  - Provides form metrics (last N games) and normalized values
- PlayerStats: Per-player statistics aggregator
  - Tracks xG/xA/DC across fixtures
  - Provides form metrics and team share calculations
- Season: Main season state container
  - Replays fixtures gameweek-by-gameweek to build statistics
  - Maintains global and per-team/player stats
  - Used as context for all prediction models
"""
from src.fpl.aggregate import Aggregate
from src.fotmob.rotation.fotmob_adapter import FotmobAdapter
from src.fpl.models.immutable import Fixture, PlayerFixture, Player, PlayerType, Query
from src.fotmob.rotation.rotation_view import PlayerSquadRole, RivalStartHint
from src.fpl.models.stats import (
    CleanSheetStatsAggregate,
    XGFixtureStatsAggregate, XAFixtureStatsAggregate, DCFixtureStatsAggregate, PtsFixtureStatsAggregate,
    PlayerXGStatsAggregate, PlayerXAStatsAggregate, PlayerDCStatsAggregate,
)


class TeamStats:

    team_id: int
    clean_sheet_stats: CleanSheetStatsAggregate
    xg_stats: XGFixtureStatsAggregate
    xa_stats: XAFixtureStatsAggregate
    dc_stats: DCFixtureStatsAggregate
    pts_stats: PtsFixtureStatsAggregate
    season: 'Season'

    def __init__(self, team_id: int, season: 'Season'):
        self.team_id = team_id
        self.clean_sheet_stats = CleanSheetStatsAggregate()
        self.xg_stats = XGFixtureStatsAggregate()
        self.xa_stats = XAFixtureStatsAggregate()
        self.dc_stats = DCFixtureStatsAggregate()
        self.pts_stats = PtsFixtureStatsAggregate()
        self.season = season

    def add_fixture_and_stats(self, fixture: Fixture):
        self.clean_sheet_stats.add_fixture(fixture)
        self.xg_stats.add_fixture(fixture)
        self.xa_stats.add_fixture(fixture)
        self.dc_stats.add_fixture(fixture)
        self.pts_stats.add_fixture(fixture)
        if fixture.home.team_id == self.team_id:
            self.clean_sheet_stats.add_home_stats(fixture)
            self.xg_stats.add_home_stats(fixture)
            self.xa_stats.add_home_stats(fixture)
            self.dc_stats.add_home_stats(fixture)
            self.pts_stats.add_home_stats(fixture)
        elif fixture.away.team_id == self.team_id:
            self.clean_sheet_stats.add_away_stats(fixture)
            self.xg_stats.add_away_stats(fixture)
            self.xa_stats.add_away_stats(fixture)
            self.dc_stats.add_away_stats(fixture)
            self.pts_stats.add_away_stats(fixture)
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
        return Query.team(self.team_id).name


class PlayerStats:

    player_id: int
    fixtures: dict[int, list[PlayerFixture]]
    xg_stats: PlayerXGStatsAggregate
    xa_stats: PlayerXAStatsAggregate
    dc_stats: PlayerDCStatsAggregate
    season: 'Season'

    def __init__(self, player_id: int, season: 'Season'):
        super().__init__()
        self.fixtures = {gw: [] for gw in range(1, 39)}
        self.player_id = player_id
        self.xg_stats = PlayerXGStatsAggregate()
        self.xa_stats = PlayerXAStatsAggregate()
        self.dc_stats = PlayerDCStatsAggregate()
        self.season = season

    def add_player_fixture(self, pf: PlayerFixture):
        assert pf.player_id == self.player_id
        self.fixtures[pf.gameweek].append(pf)
        self.xg_stats.add_player_fixture(pf)
        self.xa_stats.add_player_fixture(pf)
        self.dc_stats.add_player_fixture(pf)

    def last_n_fixtures(self, n: int) -> list[PlayerFixture]:
        assert n > 0
        result = []
        for i in range(38, 0, -1):
            for pf in self.fixtures[i][::-1]:
                result.append(pf)
                if len(result) == n:
                    return result[::-1]
        return result[::-1]

    @property
    def mp_last_5(self) -> Aggregate:
        return self.last(5, 'mp')

    @property
    def mp_last_3(self) -> Aggregate:
        return self.last(3, 'mp')

    @property
    def mp_last_1(self) -> Aggregate:
        return self.last(1, 'mp')

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

    @property
    def dc_last_5(self) -> Aggregate:
        return self.last(5, 'dc')

    @property
    def dc_last_3(self) -> Aggregate:
        return self.last(3, 'dc')

    @property
    def dc_last_1(self) -> Aggregate:
        return self.last(1, 'dc')

    def share_last(self, n: int, metric: str) -> float:
        player_metric = self.last(n, metric)
        team_metric = (
            self.season.team_stats[Query.player(self.player_id).team_id].xg_form(n)
            if metric == 'xg' else
            self.season.team_stats[Query.player(self.player_id).team_id].xa_form(n)
        )
        return player_metric.total / team_metric.total if team_metric.count else 0.

    def last(self, n: int, metric: str) -> Aggregate:
        assert n > 0
        last_gw = self.season.gameweek
        total = 0.
        count = 0.
        for i in range(n):
            for pf in self.fixtures[last_gw - i]:
                total += {
                    'mp': pf.minutes,
                    'xg': pf.expected_goals,
                    'xa': pf.expected_assists,
                    'dc': pf.defensive_contribution,
                    'pts': pf.total_points,
                }[metric]
                count += 1
        return Aggregate(total, count)

    @property
    def player(self) -> Player:
        return Query.player(self.player_id)

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
    dc_stats: DCFixtureStatsAggregate
    pts_stats: PtsFixtureStatsAggregate
    team_stats: dict[int, TeamStats]
    player_stats: dict[int, PlayerStats]

    # view options
    pos: PlayerType | None

    def __init__(self):
        self.gameweek = 0
        self.clean_sheet_stats = CleanSheetStatsAggregate()
        self.xg_stats = XGFixtureStatsAggregate()
        self.xa_stats = XAFixtureStatsAggregate()
        self.dc_stats = DCFixtureStatsAggregate()
        self.pts_stats = PtsFixtureStatsAggregate()
        self.team_stats = {team.team_id: TeamStats(team.team_id, self) for team in Query.all_teams()}
        self.player_stats = {player.player_id: PlayerStats(player.player_id, self) for player in Query.all_players()}

        # view options
        self.pos = None
        self.rotation_adapter: FotmobAdapter | None = None

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

            self.dc_stats.add_fixture(fixture)
            self.dc_stats.add_home_stats(fixture)
            self.dc_stats.add_away_stats(fixture)

            self.pts_stats.add_fixture(fixture)
            self.pts_stats.add_home_stats(fixture)
            self.pts_stats.add_away_stats(fixture)

            self.team_stats[fixture.home.team_id].add_fixture_and_stats(fixture)
            self.team_stats[fixture.away.team_id].add_fixture_and_stats(fixture)

            for pf in Query.player_fixtures_by_fixture(fixture.fixture_id):
                self.player_stats[pf.player_id].add_player_fixture(pf)

        self.gameweek += 1

    def attach_rotation_adapter(self, adapter: FotmobAdapter):
        self.rotation_adapter = adapter

    def get_player_squad_role(self, fpl_player_id: int) -> PlayerSquadRole:
        if not self.rotation_adapter:
            raise ValueError("Rotation adapter is not attached to the season")
        return self.rotation_adapter.get_player_squad_role(fpl_player_id, self.gameweek)

    def get_rival_start_hint(self, fpl_player_id: int) -> RivalStartHint:
        if not self.rotation_adapter:
            raise ValueError("Rotation adapter is not attached to the season")
        return self.rotation_adapter.get_rival_start_hint(fpl_player_id, self.gameweek)

    @property
    def top_xg_players(self) -> list[PlayerStats]:
        return sorted(
            filter(
                lambda ps: self.pos is None or ps.player.player_type == self.pos,
                self.player_stats.values(),
            ),
            key=lambda ps: -ps.xg_last_5.p,
        )
