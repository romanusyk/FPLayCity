from dataclasses import asdict, dataclass
from pandas import DataFrame
from src.fpl.models.prediction import GameweekPredictions, PlayerFixture, PlayerFixturePrediction, PlayerTotalPrediction, TeamFixturePrediction, TeamTotalPrediction, SimpleModelResponse
from src.fpl.models.immutable import Metric, NewsFact, Player, Team, TeamFixture, Query
from src.fpl.models.stats import StatsQuery
from src.fotmob.rotation.rotation_view import (
    PlayerSquadRole,
    RivalStartHint,
    RivalSubDetail,
)


@dataclass
class PlayerView:

    player_id: int
    web_name: str
    now_cost: float
    position: str
    team: str
    red_flags: list[str]

    @classmethod
    def build(cls, data: Player, red_flags: list[str] | None = None) -> 'PlayerView':
        return cls(
            player_id=data.player_id,
            web_name=data.web_name,
            now_cost=data.now_cost,
            position=data.player_type.name,
            team=data.team.short_name,
            red_flags=red_flags or [],
        )

    def __repr__(self):
        return f"!{self.red_flags} {self.web_name} ({self.now_cost}£) {self.position} - {self.team}"


@dataclass
class RivalView:

    count: int
    player: PlayerView

    @classmethod
    def build(cls, data: RivalSubDetail) -> 'RivalView':
        return cls(
            count=data.sub_count,
            player=PlayerView.build(Query.player(data.fpl_player_id)),
        )

    def __repr__(self):
        return f"{self.player.web_name} ({self.count})"


@dataclass
class PlayerSquadRoleView:
    
    appearances: int
    starts: int
    benched: int
    unavailable: int
    start_ratio: float
    is_first_team: bool
    rivals: list[RivalView]


    @classmethod
    def build(cls, data: PlayerSquadRole, rivalry: RivalStartHint) -> 'PlayerSquadRoleView':
        if not data or not rivalry:
            return None
        return cls(
            appearances=data.total_matches,
            starts=data.starts,
            benched=data.benched,
            unavailable=data.unavailable,
            start_ratio=data.start_ratio,
            is_first_team=data.is_first_team,
            rivals=[RivalView.build(rival) for rival in rivalry.rivals_sorted],
        )

    def __repr__(self):
        return f"ST={self.starts}/{self.appearances} (rivals={self.rivals})"


@dataclass
class NewsFactView:

    fact: str
    form: float
    availability: float
    news_id: int

    @classmethod
    def build(cls, data: NewsFact) -> 'NewsFactView':
        return cls(
            fact=data.fact,
            form=data.form,
            availability=data.availability,
            news_id=data.news_id,
        )

    def __repr__(self):
        return f"[form={self.form:.1f}, fit={self.availability:.1f}] {self.fact}"


@dataclass
class NewsView:
    
    facts: list[NewsFactView]
    avg_form: float
    avg_availability: float

    @classmethod
    def build(cls, data: list[NewsFact]) -> 'NewsView':
        return cls(
            facts=[NewsFactView.build(fact) for fact in data],
            avg_form=sum(fact.form for fact in data) / len(data) if data else 0,
            avg_availability=sum(fact.availability for fact in data) / len(data) if data else 0,
        )

    def __repr__(self):
        return f"form={self.avg_form:.1f}, fit={self.avg_availability:.1f} ({[f.fact[:24] for f in self.facts]})"


@dataclass
class PlayerFixtureView:

    game: str
    xg_act: float
    xg_avg: float
    xg_exc: float
    xg_exc_form: float
    xg_exp: float
    xa_act: float
    xa_avg: float
    xa_exc: float
    xa_exc_form: float
    xa_exp: float
    dc_act: float
    dc_avg: float
    dc_exc: float
    dc_exc_form: float
    dc_exp: float
    cs_act: float
    cs_avg: float
    cs_exc: float
    cs_exc_form: float
    cs_exp: float

    @classmethod
    def build(cls, data: PlayerFixture, metrics_exp: dict[Metric, SimpleModelResponse] | None = None) -> 'PlayerFixtureView':
        metrics = {}
        for metric in [Metric.XG, Metric.XA, Metric.DC, Metric.CS]:
            metrics[f"{metric.name.lower()}_act"] = data.get_metric(metric)
            metrics[f"{metric.name.lower()}_avg"] = StatsQuery.player_fixture_avg(metric, data).p
            metrics[f"{metric.name.lower()}_exc"] = data.get_metric(metric) - StatsQuery.player_fixture_avg(metric, data).p if data.get_metric(metric) is not None else None
            metrics[f"{metric.name.lower()}_exc_form"] = metrics_exp[metric].metric_exc if metrics_exp else -1
            metrics[f"{metric.name.lower()}_exp"] = metrics_exp[metric].metric_exp if metrics_exp else -1
        return cls(
            game=(
                f"gw={data.gameweek} fdr={data.team_fixture.difficulty} "
                f"{data.fixture.home.team.short_name} {data.fixture.home.score}:{data.fixture.away.score} {data.fixture.away.team.short_name}"
            ),
            **metrics,
        )


@dataclass
class PlayerStatsView:
    total_exp_xg: float
    total_exp_xg_pts: float
    total_exp_xa: float
    total_exp_xa_pts: float
    total_exp_dc: float
    total_exp_dc_pts: float
    total_exp_cs: float
    total_exp_cs_pts: float
    total_exp_points: float

    def __repr__(self):
        totals = f"total={self.total_exp_points:.2f}pts"

        xg_share = 100 * self.total_exp_xg_pts / self.total_exp_points if self.total_exp_points else 0.
        xa_share = 100 * self.total_exp_xa_pts / self.total_exp_points if self.total_exp_points else 0.
        dc_share = 100 * self.total_exp_dc_pts / self.total_exp_points if self.total_exp_points else 0.
        cs_share = 100 * self.total_exp_cs_pts / self.total_exp_points if self.total_exp_points else 0.

        xg_str = f"xg=[{self.total_exp_xg:.2f} {self.total_exp_xg_pts:.2f}pts {xg_share:.1f}%]"
        xa_str = f"xa=[{self.total_exp_xa:.2f} {self.total_exp_xa_pts:.2f}pts {xa_share:.1f}%]"
        dc_str = f"dc=[{self.total_exp_dc:.2f} {self.total_exp_dc_pts:.2f}pts {dc_share:.1f}%]"
        cs_str = f"cs=[{self.total_exp_cs:.2f} {self.total_exp_cs_pts:.2f}pts {cs_share:.1f}%]"

        return f"{totals} {xg_str} {xa_str} {dc_str} {cs_str}"


@dataclass
class PlayerPredictionView:

    player: PlayerView
    squad_role: PlayerSquadRoleView
    news: NewsView
    fixtures: DataFrame

    stats: PlayerStatsView

    @classmethod
    def build(cls, data: PlayerTotalPrediction, next_gameweek: int, history_gws: int) -> 'PlayerPredictionView':
        history = [
            PlayerFixtureView.build(pf)
            for pf in Query.player_fixtures_by_player_and_gameweeks(data.player.player_id, next_gameweek - history_gws, next_gameweek - 1)
        ]
        fixtures = [
            PlayerFixtureView.build(fp.fixture, fp.metrics_exp)
            for fp in data.fixture_predictions
        ]
        return cls(
            player=PlayerView.build(data.player, data.red_flags),
            squad_role=PlayerSquadRoleView.build(
                data.squad_role,
                data.rotation_rivals,
            ),
            news=NewsView.build(data.news_facts),
            fixtures=DataFrame([asdict(fixture) for fixture in history + fixtures]),
            stats=PlayerStatsView(
                total_exp_xg=data.xg_exp.p,
                total_exp_xg_pts=data.xg_exp_points,
                total_exp_xa=data.xa_exp.p,
                total_exp_xa_pts=data.xa_exp_points,
                total_exp_dc=data.dc_exp.p,
                total_exp_dc_pts=data.dc_exp_points,
                total_exp_cs=data.cs_exp.p,
                total_exp_cs_pts=data.cs_exp_points,
                total_exp_points=data.total_exp_points,
            ),
        )

    def __repr__(self):
        now_cost_per_pts = self.player.now_cost / self.stats.total_exp_points if self.stats.total_exp_points else -1.

        xg_share = int(100 * self.stats.total_exp_xg_pts / self.stats.total_exp_points) if self.stats.total_exp_points else 0
        xa_share = int(100 * self.stats.total_exp_xa_pts / self.stats.total_exp_points) if self.stats.total_exp_points else 0
        dc_share = int(100 * self.stats.total_exp_dc_pts / self.stats.total_exp_points) if self.stats.total_exp_points else 0
        cs_share = int(100 * self.stats.total_exp_cs_pts / self.stats.total_exp_points) if self.stats.total_exp_points else 0

        top_rival_str = f" top rival: {self.squad_role.rivals[0].player.web_name} ({self.squad_role.rivals[0].count})" if self.squad_role.rivals else ""
        role_str = f"started=[{self.squad_role.starts}/{self.squad_role.appearances}{top_rival_str}]"

        news_str = f"[{len(self.news.facts)} news ({self.news.avg_form:.1f} form {self.news.avg_availability:.1f} fit)]" if self.news.facts else ""

        return f"{self.player} [{self.stats.total_exp_points:.2f}pts = {xg_share}% xg + {xa_share}% xa + {dc_share}% dc + {cs_share}% cs | {now_cost_per_pts:.1f}£/pts] {role_str} {news_str}"


@dataclass
class TeamView:

    team_id: int
    name: str
    short_name: str

    @classmethod
    def build(cls, data: Team) -> 'TeamView':
        return cls(
            team_id=data.team_id,
            name=data.name,
            short_name=data.short_name,
        )

    def __repr__(self):
        return f"[{self.team_id}] {self.short_name}"


@dataclass
class TeamFixtureView:
    
    gameweek: int
    difficulty: int
    home_team: TeamView
    home_score: int
    away_team: TeamView
    away_score: int

    xg_diff: float
    xg: float
    xg_avg: float
    xg_exc: float
    xgc: float
    xgc_avg: float
    xgc_exc: float
    cs: float
    cs_avg: float

    xg_exc_form: float | None
    xgc_exc_form: float | None
    cs_exc_form: float | None
    xg_exp: float | None
    xgc_exp: float | None
    cs_exp: float | None

    @classmethod
    def from_team_fixture(cls, data: TeamFixture) -> 'TeamFixtureView':
        fixture = data.fixture
        xg = data.get_metric(Metric.XG)
        xgc = data.get_metric(Metric.XGC)
        cs = data.get_metric(Metric.CS)
        xg_avg = StatsQuery.team_fixture_avg(Metric.XG, data).p
        xgc_avg = StatsQuery.team_fixture_avg(Metric.XGC, data).p
        cs_avg = StatsQuery.team_fixture_avg(Metric.CS, data).p
        xg_exc = xg - xg_avg if xg is not None else None
        xgc_exc = xgc - xgc_avg if xgc is not None else None
        return cls(
            gameweek=fixture.gameweek,
            difficulty=data.difficulty,
            home_team=TeamView.build(fixture.home.team),
            home_score=fixture.home.score,
            away_team=TeamView.build(fixture.away.team),
            away_score=fixture.away.score,
            xg_diff=xg - xgc,
            xg=xg,
            xg_avg=xg_avg,
            xg_exc=xg_exc,
            xgc=xgc,
            xgc_avg=xgc_avg,
            xgc_exc=xgc_exc,
            cs=cs,
            cs_avg=cs_avg,
            xg_exc_form=None,
            xgc_exc_form=None,
            cs_exc_form=None,
            xg_exp=None,
            xgc_exp=None,
            cs_exp=None,
        )

    @classmethod
    def from_team_fixture_prediction(cls, data: TeamFixturePrediction) -> 'TeamFixtureView':
        result = cls.from_team_fixture(data.fixture)
        result.xg_exc_form = data.metrics_exp[Metric.XG].metric_exc
        result.xgc_exc_form = data.metrics_exp[Metric.XGC].metric_exc
        result.cs_exc_form = data.metrics_exp[Metric.CS].metric_exc
        result.xg_exp = data.metrics_exp[Metric.XG].metric_exp
        result.xgc_exp = data.metrics_exp[Metric.XGC].metric_exp
        result.cs_exp = data.metrics_exp[Metric.CS].metric_exp
        return result

    @property
    def game(self) -> str:
        return f"[{self.gameweek}] {self.home_team.short_name} {self.home_score}:{self.away_score} {self.away_team.short_name} (fdr={self.difficulty})"

    def to_pandas_row(self) -> dict:
        return {
            'game': self.game,
            'xg_diff': self.xg_diff,
            'xg': self.xg,
            'xg_avg': self.xg_avg,
            'xg_exc': self.xg_exc,
            'xgc': self.xgc,
            'xgc_avg': self.xgc_avg,
            'xgc_exc': self.xgc_exc,
            'cs': self.cs,
            'cs_avg': self.cs_avg,
            'xg_exc_form': self.xg_exc_form,
            'xgc_exc_form': self.xgc_exc_form,
            'cs_exc_form': self.cs_exc_form,
            'xg_exp': self.xg_exp,
            'xgc_exp': self.xgc_exp,
            'cs_exp': self.cs_exp,
        }


@dataclass
class TeamPredictionView:

    team: TeamView
    fixtures: DataFrame
    avg_xg_exp: float
    avg_xgc_exp: float
    avg_cs_exp: float

    @classmethod
    def build(cls, data: TeamTotalPrediction, next_gameweek: int, history_gws: int) -> 'TeamPredictionView':
        history = [
            TeamFixtureView.from_team_fixture(tf)
            for tf in Query.team_fixtures_by_team_and_gameweeks(data.team.team_id, next_gameweek - history_gws, next_gameweek - 1)
        ]
        fixtures = [
            TeamFixtureView.from_team_fixture_prediction(fp)
            for fp in data.fixture_predictions
        ]
        return cls(
            team=TeamView.build(data.team),
            fixtures=DataFrame([fixture.to_pandas_row() for fixture in history + fixtures]),
            avg_xg_exp=sum(fixture.xg_exp for fixture in fixtures) / len(fixtures) if fixtures else 0,
            avg_xgc_exp=sum(fixture.xgc_exp for fixture in fixtures) / len(fixtures) if fixtures else 0,
            avg_cs_exp=sum(fixture.cs_exp for fixture in fixtures) / len(fixtures) if fixtures else 0,
        )

    def to_markdown(self) -> str:
        return f"""
        ## {self.team.name} (team_id={self.team.team_id}, short_name={self.team.short_name})
        
        Expected averages: xg={self.avg_xg_exp:.2f} xgc={self.avg_xgc_exp:.2f} cs={self.avg_cs_exp:.2f}
        
        {self.fixtures.to_markdown(index=False, floatfmt=".2f")}
        """

    def __repr__(self):
        return f"{self.team.short_name}: expected avgs: xg={self.avg_xg_exp:.2f} xgc={self.avg_xgc_exp:.2f} cs={self.avg_cs_exp:.2f}"


# Next steps:
#
# 1. Implement this view
# 2. Migrate Models to StatsQuery
# 3. MCP
