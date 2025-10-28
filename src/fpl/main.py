"""
Main execution script for FPL predictions and model evaluation.

Process:
1. Bootstrap data from FPL API
2. Initialize prediction models (CS, xG, xA, DC, points)
3. Replay season gameweek-by-gameweek:
   - Build historical statistics
   - Make predictions for each gameweek
   - Compare predicted vs actual points
4. Evaluate performance:
   - Build optimal squads by position using predictions
   - Compare vs form-based and cost-based selection
   - Report total points across evaluation period

Key models used:
- UltimateCleanSheetModel: FDR-weighted clean sheet predictions
- SimpleXGModel/SimpleXAModel: Form + FDR scaled predictions
- PlayerPointsSimpleModel: Component-based player point predictions
- PlayerPointsFormModel: Recent form scaled by difficulty

Run with: uv run -m src.fpl.main
"""
import logging
from asyncio import new_event_loop

from httpx import AsyncClient

from src.fpl.forecast.loss import AvgDiffLoss, MAELoss
from src.fpl.forecast.models import (
    UltimateCleanSheetModel, SimpleXGModel, SimpleXAModel, SimpleDCModel, SimplePtsModel,
    PlayerXGUltimateModel, PlayerXAUltimateModel,
    PlayerCSSimpleModel, PlayerXGSimpleModel, PlayerXASimpleModel, PlayerDCSimpleModel,
    PlayerPointsSimpleModel, PlayerPointsFormNaiveModel, PlayerPointsFormModel,
)
from src.fpl.loader.load import bootstrap
from src.fpl.models.immutable import (
    Fixtures,
    PlayerFixtures,
    PlayerType, Players,
)
from src.fpl.models.prediction import (
    PlayerFixtureCsPrediction,
    PlayerFixtureXgPrediction,
    PlayerFixtureXaPrediction,
    PlayerFixtureDcPrediction,
    GameweekPredictions,
)
from src.fpl.models.season import Season

logging.basicConfig(level=logging.INFO)


class XG_BY_FDR:

    season_24_25_up_to_gw_33 = {
        1: 1.86, # was added in the end of the season
        2: 1.86,
        3: 1.39,
        4: 1.18,
        5: 0.89,
    }

    main = season_24_25_up_to_gw_33


async def main(client: AsyncClient):
    await bootstrap(client)

    next_gameweek = 9
    min_history_gws = 3

    horizon = 3
    season = Season()
    total_team_loss = 0.
    total_player_loss = 0.

    total_points = 0
    total_naive_points = 0
    total_cost_points = 0
    total_weeks = 0

    cs_model = UltimateCleanSheetModel(season)
    xg_model = SimpleXGModel(season)
    xa_model = SimpleXAModel(season)
    dc_model = SimpleDCModel(season)
    pts_model = SimplePtsModel(season)
    player_cs_model = PlayerCSSimpleModel(season, cs_model, min_history_gws)
    player_xg_model = PlayerXGSimpleModel(season, xg_model, min_history_gws)
    player_xa_model = PlayerXASimpleModel(season, xa_model, min_history_gws)
    player_dc_model = PlayerDCSimpleModel(season, dc_model, min_history_gws)
    # player_xg_model = PlayerXGUltimateModel(season, xg_model)
    # player_xa_model = PlayerXAUltimateModel(season, xa_model)
    player_points_model = PlayerPointsSimpleModel(
        season=season,
        cs_model=player_cs_model,
        xg_model=player_xg_model,
        xa_model=player_xa_model,
        dc_model=player_dc_model,
        last_n_weeks=min_history_gws,
    )
    # player_points_naive_model = PlayerPointsFormNaiveModel(season, min_history_gws)
    player_points_naive_model = PlayerPointsFormModel(season, pts_model, min_history_gws)

    for target_gameweek in range(2, next_gameweek + 1):
        season.play(Fixtures.get_list(gameweek=target_gameweek - 1))
        if target_gameweek == next_gameweek:
            gw_predictions = GameweekPredictions(season)
            for gw in range(target_gameweek, target_gameweek + horizon):
                for fixture in Fixtures.get_list(gameweek=gw):
                    gw_predictions.add_team_prediction(cs_model.predict(fixture))
                    for pf in PlayerFixtures.by_fixture(fixture.fixture_id):
                        gw_predictions.add_player_cs_prediction(PlayerFixtureCsPrediction(player_cs_model.predict(pf)))
                        gw_predictions.add_player_xg_prediction(PlayerFixtureXgPrediction(player_xg_model.predict(pf)))
                        gw_predictions.add_player_xa_prediction(PlayerFixtureXaPrediction(player_xa_model.predict(pf)))
                        gw_predictions.add_player_dc_prediction(PlayerFixtureDcPrediction(player_dc_model.predict(pf)))
            logging.info(f'Predictions for the given {horizon=} are Hot to Go!')

        if target_gameweek > min_history_gws and target_gameweek < next_gameweek:
            gw_predictions = GameweekPredictions(season)
            form_predictions = []
            by_cost = []
            for fixture in Fixtures.get_list(gameweek=target_gameweek):
                gw_predictions.add_team_prediction(cs_model.predict(fixture))
                for pf in PlayerFixtures.by_fixture(fixture.fixture_id):
                    gw_predictions.add_player_cs_prediction(
                        PlayerFixtureCsPrediction(player_cs_model.predict(pf))
                        )
                    gw_predictions.add_player_xg_prediction(
                        PlayerFixtureXgPrediction(player_xg_model.predict(pf))
                        )
                    gw_predictions.add_player_xa_prediction(
                        PlayerFixtureXaPrediction(player_xa_model.predict(pf))
                        )
                    gw_predictions.add_player_dc_prediction(
                        PlayerFixtureDcPrediction(player_dc_model.predict(pf))
                        )
                    form_predictions.append(player_points_naive_model.predict(pf))
                    if season.player_stats[pf.player_id].last(min_history_gws, 'mp').p > 60 and season.player_stats[pf.player_id].last(1, 'mp').p > 30:
                        by_cost.append(pf)
            gw_points = 0
            gw_naive_points = 0
            gw_cost_points = 0
            for pos, count in (
                (PlayerType.GKP, 2),
                (PlayerType.DEF, 5),
                (PlayerType.MID, 5),
                (PlayerType.FWD, 3),
            ):
                season.pos = pos
                predictions = gw_predictions.players_points_desc[:count]
                pos_points = 0
                for pr in predictions:
                    pos_points += pr.actual_points
                pos_naive_points = 0
                prs = sorted(
                    filter(
                        lambda p: Players.get_one(player_id=p.fixture.player_id).player_type == pos,
                        form_predictions,
                    ),
                    key=lambda p: -p.prediction.p,
                )[:count]
                for pr in prs:
                    pos_naive_points += pr.fixture.total_points
                pos_cost_points = 0
                prs_cost = sorted(
                    filter(
                        lambda pf: Players.get_one(player_id=pf.player_id).player_type == pos,
                        by_cost,
                    ),
                    key=lambda pf: -pf.value,
                )[:count]
                for pr in prs_cost:
                    pos_cost_points += pr.total_points

                logging.info(f'Gameweek {target_gameweek} {pos.name} points: {pos_points} vs form points: {pos_naive_points} vs cost points: {pos_cost_points}')
                gw_points += pos_points
                gw_naive_points += pos_naive_points
                gw_cost_points += pos_cost_points
            season.pos = None
            logging.info(f'Gameweek {target_gameweek} total points: {gw_points} vs form points: {gw_naive_points} vs cost points: {gw_cost_points}')
            total_points += gw_points
            total_naive_points += gw_naive_points
            total_cost_points += gw_cost_points
            total_weeks += 1

            # loss = MAELoss()
            # labels = []
            # predictions = []
            # for fixture in Fixtures.get_list(gameweek=target_gameweek):
            #     for pf in PlayerFixtures.by_fixture(fixture.fixture_id):
            #         if not pf.minutes or pf.minutes < 10:
            #             continue
            #         labels.append(pf.total_points)
            #         pred = player_points_model.predict(pf)
            #         predictions.append(pred.prediction.p)
            # target_gameweek_loss = loss.score(labels, predictions)
            # logging.info(f'{target_gameweek_loss=} for {target_gameweek=}')
            # total_player_loss += target_gameweek_loss

            # loss = AvgDiffLoss()
            # target_gameweek_loss = 0.
            # labels = []
            # predictions = []
            # for fixture in Fixtures.get_list(gameweek=target_gameweek):
            #     fixture_prediction = cs_model.predict(fixture)
            #     labels.append(int(fixture.away.score == 0))
            #     labels.append(int(fixture.home.score == 0))
            #     predictions.append(fixture_prediction.home_prediction.p)
            #     predictions.append(fixture_prediction.away_prediction.p)
            # target_gameweek_loss += loss.score(labels, predictions)
            # logging.info(f'{target_gameweek_loss=} for {target_gameweek=}')
            # total_team_loss += target_gameweek_loss

    logging.info(f'Total points: {total_points / total_weeks} ({total_points} / {total_weeks})')
    logging.info(f'Total form points: {total_naive_points / total_weeks} ({total_naive_points} / {total_weeks})')
    logging.info(f'Total cost points: {total_cost_points / total_weeks} ({total_cost_points} / {total_weeks})')
    logging.info(f'{total_player_loss=}')
    logging.info(f'{total_team_loss=}')


if __name__ == '__main__':
    client = AsyncClient()
    new_event_loop().run_until_complete(main(client))
