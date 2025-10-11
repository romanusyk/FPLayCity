from src.fpl.aggregate import Aggregate, swa, wa
from src.fpl.models.immutable import Fixture, PlayerFixture
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


class PlayerFixtureModel:

    def __init__(self, season: Season):
        self.season = season

    def predict(self, fixture: PlayerFixture) -> PlayerFixtureAggregate:
        return PlayerFixtureAggregate(
            fixture=fixture,
            prediction=self._predict(fixture),
        )

    def _predict(self, fixture: PlayerFixture) -> Aggregate:
        pass


class PlayerCSSimpleModel(PlayerFixtureModel):

    def __init__(self, season: Season, team_cs_model: CleanSheetModel):
        super().__init__(season)
        self.team_cs_model = team_cs_model

    def _predict(self, fixture: PlayerFixture) -> Aggregate:
        team_cs = self.team_cs_model.predict_for_team(fixture.team_id, fixture.fixture)
        player_mp = self.season.player_stats[fixture.player_id].mp_last_5
        p = min(1., player_mp.p / 60.)
        return Aggregate(team_cs.p * p, 1)


class PlayerXGSimpleModel(PlayerFixtureModel):

    def __init__(self, season: Season, team_xg_model: XGModel):
        super().__init__(season)
        self.team_xg_model = team_xg_model

    def _predict(self, fixture: PlayerFixture) -> Aggregate:
        team_scale = self.team_xg_model.scale_for_team(fixture.team_id, fixture.fixture)
        player_xg = self.season.player_stats[fixture.player_id].xg_last_5
        return Aggregate(player_xg.p * team_scale, 1)


class PlayerXGUltimateModel(PlayerFixtureModel):

    def __init__(self, season: Season, team_xg_model: XGModel):
        super().__init__(season)
        self.team_xg_model = team_xg_model

    def _predict(self, fixture: PlayerFixture) -> Aggregate:
        team_xg = self.team_xg_model.predict_for_team(fixture.team_id, fixture.fixture)
        player_xg_share = self.season.player_stats[fixture.player_id].share_last(5, 'xg')
        return Aggregate(team_xg.total * player_xg_share, team_xg.count)


class PlayerXASimpleModel(PlayerFixtureModel):

    def __init__(self, season: Season, team_xa_model: XAModel):
        super().__init__(season)
        self.team_xa_model = team_xa_model

    def _predict(self, fixture: PlayerFixture) -> Aggregate:
        team_scale = self.team_xa_model.scale_for_team(fixture.team_id, fixture.fixture)
        player_xa = self.season.player_stats[fixture.player_id].xa_last_5
        return Aggregate(player_xa.p * team_scale, 1)


class PlayerXAUltimateModel(PlayerFixtureModel):

    prediction_cls = PlayerFixtureXaPrediction

    def __init__(self, season: Season, team_xa_model: XAModel):
        super().__init__(season)
        self.team_xa_model = team_xa_model

    def _predict(self, fixture: PlayerFixture) -> Aggregate:
        team_xa = self.team_xa_model.predict_for_team(fixture.team_id, fixture.fixture)
        player_xa_share = self.season.player_stats[fixture.player_id].share_last(5, 'xa')
        return Aggregate(team_xa.total * player_xa_share, team_xa.count)

class PlayerDCSimpleModel(PlayerFixtureModel):

    def __init__(self, season: Season, team_dc_model: DCModel):
        super().__init__(season)
        self.team_dc_model = team_dc_model

    def _predict(self, fixture: PlayerFixture) -> Aggregate:
        team_scale = self.team_dc_model.scale_for_team(fixture.team_id, fixture.fixture)
        player_dc = self.season.player_stats[fixture.player_id].dc_last_5
        return Aggregate(player_dc.p * team_scale, 1)
