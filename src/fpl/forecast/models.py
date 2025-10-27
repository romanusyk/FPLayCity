from src.fpl.aggregate import Aggregate, swa, wa
from src.fpl.models.immutable import Fixture, PlayerFixture, Players
from src.fpl.models.prediction import (
    FixturePrediction,
    PlayerFixtureAggregate,
    PlayerFixturePrediction,
    PlayerFixtureXgPrediction,
    PlayerFixtureXaPrediction,
)
from src.fpl.models.season import Season


class FixtureModel:

    def predict(self, fixture: Fixture) -> FixturePrediction:
        return FixturePrediction(
            fixture=fixture,
            home_prediction=self.predict_for_team(fixture.home.team_id, fixture),
            away_prediction=self.predict_for_team(fixture.away.team_id, fixture),
        )

    def scale_for_team(self, team_id: int, fixture: Fixture) -> float:
        return 1.0

    def predict_for_team(self, team_id: int, fixture: Fixture) -> Aggregate:
        pass


class CleanSheetModel(FixtureModel):

    def __init__(self, season: Season):
        self.season = season


class SeasonAvgCleanSheetModel(CleanSheetModel):

    def predict_for_team(self, team_id: int, fixture: Fixture) -> Aggregate:
        return self.season.team_stats[team_id].clean_sheet_stats.total


class Last5CleanSheetModel(CleanSheetModel):

    def predict_for_team(self, team_id: int, fixture: Fixture) -> Aggregate:
        return self.season.team_stats[team_id].cs_last_5


class AllAndFormCleanSheetModel(CleanSheetModel):

    def __init__(self, season: Season):
        super().__init__(season)
        self.avg_model = SeasonAvgCleanSheetModel(season)
        self.form_model = Last5CleanSheetModel(season)

    def predict_for_team(self, team_id: int, fixture: Fixture) -> Aggregate:
        return swa(
            self.avg_model.predict_for_team(team_id, fixture),
            self.form_model.predict_for_team(team_id, fixture),
        )


class AvgFDRCleanSheetModel(CleanSheetModel):

    def predict_for_team(self, team_id: int, fixture: Fixture) -> Aggregate:
        if fixture.home.team_id == team_id:
            return self.season.clean_sheet_stats.fdr_aggregate[fixture.home.difficulty]
        else:
            return self.season.clean_sheet_stats.fdr_aggregate[fixture.away.difficulty]


class AvgSeasonAndFDRCleanSheetModel(CleanSheetModel):

    def __init__(self, season: Season):
        super().__init__(season)
        self.avg_model = SeasonAvgCleanSheetModel(season)
        self.fdr_model = AvgFDRCleanSheetModel(season)

    def predict_for_team(self, team_id: int, fixture: Fixture) -> Aggregate:
        return swa(
            self.avg_model.predict_for_team(team_id, fixture),
            self.fdr_model.predict_for_team(team_id, fixture),
        )


class UltimateCleanSheetModel(CleanSheetModel):

    def __init__(self, season: Season):
        super().__init__(season)
        self.fdr_model = AvgFDRCleanSheetModel(season)

    def predict_for_team(self, team_id: int, fixture: Fixture) -> Aggregate:
        side = 'home' if fixture.home.team_id == team_id else 'away'
        side_team_agg = self.season.team_stats[team_id].clean_sheet_stats.side_aggregate[side]
        total_team_agg = self.season.team_stats[team_id].clean_sheet_stats.total
        return wa(
            (self.fdr_model.predict_for_team(team_id, fixture), 0.6),
            (
                wa(
                    (side_team_agg, side_team_agg.count),
                    (total_team_agg, (38. - side_team_agg.count)),
                ),
                0.4,
            ),
        )


class XGModel(FixtureModel):

    def __init__(self, season: Season):
        self.season = season


class SimpleXGModel(XGModel):

    def scale_for_team(self, team_id: int, fixture: Fixture) -> float:
        side = 'home' if fixture.home.team_id == team_id else 'away'
        fdr = fixture.home.difficulty if side == 'home' else fixture.away.difficulty
        team_xg_stats = self.season.team_stats[team_id].xg_stats
        if team_xg_stats.fdr_aggregate[fdr].count >= 3:
            scale = team_xg_stats.fdr_norm[fdr]
        else:
            scale = self.season.xg_stats.fdr_norm[fdr]
        return scale

    def predict_for_team(self, team_id: int, fixture: Fixture) -> Aggregate:
        team_form = self.season.team_stats[team_id].xg_form_norm_own_3
        scale = self.scale_for_team(team_id, fixture)
        return Aggregate(team_form.p * scale, 1)


class XAModel(FixtureModel):

    def __init__(self, season: Season):
        self.season = season


class SimpleXAModel(XAModel):

    def scale_for_team(self, team_id: int, fixture: Fixture) -> float:
        side = 'home' if fixture.home.team_id == team_id else 'away'
        fdr = fixture.home.difficulty if side == 'home' else fixture.away.difficulty
        team_xa_stats = self.season.team_stats[team_id].xa_stats
        if team_xa_stats.fdr_aggregate[fdr].count >= 3:
            scale = team_xa_stats.fdr_norm[fdr]
        else:
            scale = self.season.xa_stats.fdr_norm[fdr]
        return scale

    def predict_for_team(self, team_id: int, fixture: Fixture) -> Aggregate:
        team_form = self.season.team_stats[team_id].xa_form_norm_own_3
        scale = self.scale_for_team(team_id, fixture)
        return Aggregate(team_form.p * scale, 1)


class DCModel(FixtureModel):

    def __init__(self, season: Season):
        self.season = season


class SimpleDCModel(DCModel):

    def scale_for_team(self, team_id: int, fixture: Fixture) -> float:
        side = 'home' if fixture.home.team_id == team_id else 'away'
        fdr = fixture.home.difficulty if side == 'home' else fixture.away.difficulty
        team_dc_stats = self.season.team_stats[team_id].dc_stats
        if team_dc_stats.fdr_aggregate[fdr].count >= 3:
            scale = team_dc_stats.fdr_norm[fdr]
        else:
            scale = self.season.dc_stats.fdr_norm[fdr]
        return scale

    def predict_for_team(self, team_id: int, fixture: Fixture) -> Aggregate:
        raise NotImplementedError


class PtsModel(FixtureModel):

    def __init__(self, season: Season):
        self.season = season


class SimplePtsModel(PtsModel):

    def scale_for_team(self, team_id: int, fixture: Fixture) -> float:
        side = 'home' if fixture.home.team_id == team_id else 'away'
        fdr = fixture.home.difficulty if side == 'home' else fixture.away.difficulty
        team_pls_stats = self.season.team_stats[team_id].pts_stats
        if team_pls_stats.fdr_aggregate[fdr].count >= 3:
            scale = team_pls_stats.fdr_norm[fdr]
        else:
            scale = self.season.pts_stats.fdr_norm[fdr]
        return scale

    def predict_for_team(self, team_id: int, fixture: Fixture) -> Aggregate:
        raise NotImplementedError


class PlayerFixtureModel:

    def __init__(self, season: Season, last_n_weeks: int = 5):
        self.season = season
        self.last_n_weeks = last_n_weeks

    def predict(self, fixture: PlayerFixture) -> PlayerFixtureAggregate:
        return PlayerFixtureAggregate(
            fixture=fixture,
            prediction=self._predict(fixture),
        )

    def _predict(self, fixture: PlayerFixture) -> Aggregate:
        pass


class PlayerCSSimpleModel(PlayerFixtureModel):

    def __init__(self, season: Season, team_cs_model: CleanSheetModel, last_n_weeks: int = 5):
        super().__init__(season, last_n_weeks=last_n_weeks)
        self.team_cs_model = team_cs_model

    def _predict(self, fixture: PlayerFixture) -> Aggregate:
        team_cs = self.team_cs_model.predict_for_team(fixture.team_id, fixture.fixture)
        player_mp = self.season.player_stats[fixture.player_id].last(self.last_n_weeks, 'mp')
        p = min(1., player_mp.p / 60.)
        return Aggregate(team_cs.p * p, 1)


class PlayerXGSimpleModel(PlayerFixtureModel):

    def __init__(self, season: Season, team_xg_model: XGModel, last_n_weeks: int = 5):
        super().__init__(season, last_n_weeks=last_n_weeks)
        self.team_xg_model = team_xg_model

    def _predict(self, fixture: PlayerFixture) -> Aggregate:
        team_scale = self.team_xg_model.scale_for_team(fixture.team_id, fixture.fixture)
        player_xg = self.season.player_stats[fixture.player_id].last(self.last_n_weeks, 'xg')
        return Aggregate(player_xg.p * team_scale, 1)


class PlayerXGUltimateModel(PlayerFixtureModel):

    def __init__(self, season: Season, team_xg_model: XGModel):
        super().__init__(season)
        self.team_xg_model = team_xg_model

    def _predict(self, fixture: PlayerFixture) -> Aggregate:
        team_xg = self.team_xg_model.predict_for_team(fixture.team_id, fixture.fixture)
        player_xg_share = self.season.player_stats[fixture.player_id].share_last(self.last_n_weeks, 'xg')
        return Aggregate(team_xg.p * player_xg_share, 1)


class PlayerXASimpleModel(PlayerFixtureModel):

    def __init__(self, season: Season, team_xa_model: XAModel, last_n_weeks: int = 5):
        super().__init__(season, last_n_weeks=last_n_weeks)
        self.team_xa_model = team_xa_model

    def _predict(self, fixture: PlayerFixture) -> Aggregate:
        team_scale = self.team_xa_model.scale_for_team(fixture.team_id, fixture.fixture)
        player_xa = self.season.player_stats[fixture.player_id].last(self.last_n_weeks, 'xa')
        return Aggregate(player_xa.p * team_scale, 1)


class PlayerXAUltimateModel(PlayerFixtureModel):

    prediction_cls = PlayerFixtureXaPrediction

    def __init__(self, season: Season, team_xa_model: XAModel):
        super().__init__(season)
        self.team_xa_model = team_xa_model

    def _predict(self, fixture: PlayerFixture) -> Aggregate:
        team_xa = self.team_xa_model.predict_for_team(fixture.team_id, fixture.fixture)
        player_xa_share = self.season.player_stats[fixture.player_id].share_last(self.last_n_weeks, 'xa')
        return Aggregate(team_xa.p * player_xa_share, 1)

class PlayerDCSimpleModel(PlayerFixtureModel):

    def __init__(self, season: Season, team_dc_model: DCModel, last_n_weeks: int = 5):
        super().__init__(season, last_n_weeks=last_n_weeks)
        self.team_dc_model = team_dc_model

    def _predict(self, fixture: PlayerFixture) -> Aggregate:
        team_scale = self.team_dc_model.scale_for_team(fixture.team_id, fixture.fixture)
        player_dc = self.season.player_stats[fixture.player_id].last(self.last_n_weeks, 'dc')
        return Aggregate(player_dc.p * team_scale, 1)


class PlayerPointsSimpleModel(PlayerFixtureModel):

    def __init__(
            self,
            season: Season,
            cs_model: PlayerFixtureModel,
            xg_model: PlayerFixtureModel,
            xa_model: PlayerFixtureModel,
            dc_model: PlayerFixtureModel,
            last_n_weeks: int = 5,
    ):
        super().__init__(season, last_n_weeks=last_n_weeks)
        self.cs_model = cs_model
        self.xg_model = xg_model
        self.xa_model = xa_model
        self.dc_model = dc_model

    def _predict(self, fixture: PlayerFixture) -> Aggregate:
        player = Players.by_id(fixture.player_id)
        return Aggregate(
            (
                self.cs_model._predict(fixture).p * player.clean_sheet_points +
                self.xg_model._predict(fixture).p * player.goal_points +
                self.xa_model._predict(fixture).p * player.assist_points +
                self.dc_model._predict(fixture).p * player.dc_points
            ),
            1,
        )


class PlayerPointsFormNaiveModel(PlayerFixtureModel):

    def __init__(self, season: Season, last_n_weeks: int = 5):
        super().__init__(season, last_n_weeks=last_n_weeks)

    def _predict(self, fixture: PlayerFixture) -> Aggregate:
        player_points = self.season.player_stats[fixture.player_id].last(self.last_n_weeks, 'pts')
        return Aggregate(player_points.p, 1)


class PlayerPointsFormModel(PlayerFixtureModel):

    def __init__(self, season: Season, team_pts_model: PtsModel, last_n_weeks: int = 5):
        super().__init__(season, last_n_weeks=last_n_weeks)
        self.team_pts_model = team_pts_model

    def _predict(self, fixture: PlayerFixture) -> Aggregate:
        team_scale = self.team_pts_model.scale_for_team(fixture.team_id, fixture.fixture)
        player_pts = self.season.player_stats[fixture.player_id].last(self.last_n_weeks, 'pts')
        return Aggregate(player_pts.p * team_scale, 1)
