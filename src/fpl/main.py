import logging
from asyncio import new_event_loop

from httpx import AsyncClient

from src.fpl.forecast.loss import AvgDiffLoss
from src.fpl.forecast.models import (
    UltimateCleanSheetModel, SimpleXGModel, SimpleXAModel,
    PlayerXGUltimateModel, PlayerXAUltimateModel,
)
from src.fpl.loader.load import bootstrap
from src.fpl.models.immutable import (
    Fixtures,
    PlayerFixtures,
    PlayerType,
)
from src.fpl.models.prediction import (
    PlayerFixtureXgPrediction,
    PlayerFixtureXaPrediction,
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

    horizon = 1
    season = Season()
    total_loss = 0.

    for target_gameweek in range(2, 39):
        season.play(Fixtures.get_list(gameweek=target_gameweek - 1))
        if target_gameweek < 38:
            continue

        cs_model = UltimateCleanSheetModel(season)
        xg_model = SimpleXGModel(season)
        xa_model = SimpleXAModel(season)
        player_xg_model = PlayerXGUltimateModel(season, xg_model)
        player_xa_model = PlayerXAUltimateModel(season, xa_model)

        gw_predictions = GameweekPredictions(season)
        for gw in range(target_gameweek, target_gameweek + horizon):
            for fixture in Fixtures.get_list(gameweek=gw):
                gw_predictions.add_team_prediction(cs_model.predict(fixture))
                for pf in PlayerFixtures.by_fixture(fixture.fixture_id):
                    gw_predictions.add_player_xg_prediction(PlayerFixtureXgPrediction(player_xg_model.predict(pf)))
                    gw_predictions.add_player_xa_prediction(PlayerFixtureXaPrediction(player_xa_model.predict(pf)))
        continue
        loss = AvgDiffLoss()
        target_gameweek_loss = 0.
        labels = []
        predictions = []
        for fixture in Fixtures.get_list(gameweek=target_gameweek):
            fixture_prediction = cs_model.predict(fixture)
            labels.append(int(fixture.away.score == 0))
            labels.append(int(fixture.home.score == 0))
            predictions.append(fixture_prediction.home_prediction.p)
            predictions.append(fixture_prediction.away_prediction.p)
        target_gameweek_loss += loss.score(labels, predictions)
        logging.info(f'{target_gameweek_loss=} for {target_gameweek=}')
        total_loss += target_gameweek_loss

    logging.info(f'{total_loss=}')


if __name__ == '__main__':
    client = AsyncClient()
    new_event_loop().run_until_complete(main(client))
