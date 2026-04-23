"""
# todo: fix this
Main execution script for FPL predictions using lazy computation pipeline.

Process:
1. Bootstrap data from FPL API
2. Create lazy computation pipeline
3. Generate predictions for target gameweek(s) with automatic caching
4. Evaluate performance:
   - Compare model predictions vs form-based and cost-based selection
   - Report total points across evaluation period

Key features:
- Lazy evaluation: Only compute what's needed
- Automatic caching: Reuse results for same parameters
- Type-safe computation graph

Run with: uv run -m src.fpl.main
"""
import logging
import os

from dotenv import load_dotenv
from asyncio import new_event_loop

from src.fpl.compute.prediction import PredictionPipeline
from src.fpl.models.immutable import Metric, PlayerType, Query
from src.fpl.models.season import Season
from src.fpl.models.stats import StatsQuery
from src.fpl.models.prediction import GameweekPredictions
from src.fpl.forecast.models import SimplePtsModel, PlayerPointsFormModel
from src.fpl.core import build_pipeline
from src.fpl.views import PlayerPredictionView, TeamPredictionView

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def predict(
        pipeline: PredictionPipeline,
        next_gameweek: int,
        horizon: int,
        min_history_gws: int = 3,
        offset: int = 0,
) -> GameweekPredictions:
    target_gameweeks = [next_gameweek + offset + i for i in range(horizon)]
    logging.info(f"\n=== Predictions for GWs {target_gameweeks} ===")
    return pipeline.predict(
        next_gameweek=next_gameweek,
        target_gameweeks=target_gameweeks,
        min_history_gws=min_history_gws,
    )


def get_my_fpl_players(
        predictions: GameweekPredictions,
        next_gameweek: int,
) -> list[PlayerPredictionView]:
    return [
        PlayerPredictionView.build(p, next_gameweek=next_gameweek, history_gws=predictions.min_history_gws)
        for p in predictions.my_fpl_players()
    ]


def get_my_draft_players(
        predictions: GameweekPredictions,
        next_gameweek: int,
) -> list[PlayerPredictionView]:
    return [
        PlayerPredictionView.build(p, next_gameweek=next_gameweek, history_gws=predictions.min_history_gws)
        for p in predictions.my_draft_players()
    ]


def get_available_draft_players(
        predictions: GameweekPredictions,
        next_gameweek: int,
        position: PlayerType | None = None,
) -> list[PlayerPredictionView]:
    return [
        PlayerPredictionView.build(p, next_gameweek=next_gameweek, history_gws=predictions.min_history_gws)
        for p in predictions.top_draft_players(position=position)
        if position is None or p.player.player_type == position
    ]


def top_players_new(
        predictions: GameweekPredictions,
        next_gameweek: int,
        position: PlayerType | None = None,
) -> list[PlayerPredictionView]:
    return [
        PlayerPredictionView.build(p, next_gameweek=next_gameweek, history_gws=predictions.min_history_gws)
        for p in predictions.players_total_predictions(position=position)
    ]


def top_teams_old(
        predictions: GameweekPredictions,
        next_gameweek: int,
) -> list[TeamPredictionView]:
    return [
        TeamPredictionView.build(p, next_gameweek=next_gameweek, history_gws=predictions.min_history_gws)
        for p in predictions.teams_total_cs_desc
    ]


def top_teams_new(
        predictions: GameweekPredictions,
        next_gameweek: int,
) -> list[TeamPredictionView]:
    return [
        TeamPredictionView.build(p, next_gameweek=next_gameweek, history_gws=predictions.min_history_gws)
        for p in predictions.teams_xgc_exp_asc
    ]


async def main():
    load_dotenv()
    next_gameweek = int(os.getenv("NEXT_GAMEWEEK"))
    if not next_gameweek:
        raise ValueError("NEXT_GAMEWEEK environment variable is not set")
    min_history_gws = 5
    horizon = 8

    pipeline = await build_pipeline(next_gameweek)

    predictions = predict(pipeline, next_gameweek, horizon, min_history_gws)
    all_players = top_players_new(predictions, next_gameweek)
    all_players_gkps = top_players_new(predictions, next_gameweek, PlayerType.GKP)
    all_players_defs = top_players_new(predictions, next_gameweek, PlayerType.DEF)
    all_players_mids = top_players_new(predictions, next_gameweek, PlayerType.MID)
    all_players_fwds = top_players_new(predictions, next_gameweek, PlayerType.FWD)
    my_fpl_players = get_my_fpl_players(predictions, next_gameweek)
    my_draft_players = get_my_draft_players(predictions, next_gameweek)
    available_draft_gkps = get_available_draft_players(predictions, next_gameweek, PlayerType.GKP)
    available_draft_defs = get_available_draft_players(predictions, next_gameweek, PlayerType.DEF)
    available_draft_mids = get_available_draft_players(predictions, next_gameweek, PlayerType.MID)
    available_draft_fwds = get_available_draft_players(predictions, next_gameweek, PlayerType.FWD)
    teams_old = top_teams_old(predictions, next_gameweek)
    teams_new = top_teams_new(predictions, next_gameweek)

    logging.info(f"\n=== Backtesting from GW {min_history_gws + 1} to {next_gameweek - 1} ===")
    
    total_points = 0
    total_naive_points = 0
    total_cost_points = 0
    total_weeks = 0
    
    season = Season()
    for gw in range(1, min_history_gws + 1):
        season.play(Query.fixtures_by_gameweek(gw))
    
    for target_gameweek in range(min_history_gws + 1, next_gameweek):
        gw_predictions = pipeline.predict(
            next_gameweek=target_gameweek,
            target_gameweek=target_gameweek,
            min_history_gws=min_history_gws
        )
        
        pts_model = SimplePtsModel(season)
        form_model = PlayerPointsFormModel(season, pts_model, min_history_gws)
        
        form_predictions = []
        by_cost = []
        for fixture in Query.fixtures_by_gameweek(target_gameweek):
            for pf in Query.player_fixtures_by_fixture(fixture.fixture_id):
                form_predictions.append((pf, form_model.predict(pf)))
                if (season.player_stats[pf.player_id].last(min_history_gws, 'mp').p > 60 and
                        season.player_stats[pf.player_id].last(1, 'mp').p > 30):
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
            pos_predictions = [
                p for p in gw_predictions.players_total_points_desc
                if p.player.player_type == pos
            ][:count]
            pos_points = sum(p.actual_points for p in pos_predictions)
            
            pos_form = sorted(
                [(pf, p) for pf, p in form_predictions if Query.player(pf.player_id).player_type == pos],
                key=lambda e: -e[1].p,
            )[:count]
            pos_naive_points = sum(pf.total_points for pf, p in pos_form)
            
            pos_cost = sorted(
                [pf for pf in by_cost if Query.player(pf.player_id).player_type == pos],
                key=lambda pf: -pf.value)[:count]
            pos_cost_points = sum(pf.total_points for pf in pos_cost)
            
            logging.info(f'GW{target_gameweek} {pos.name}: {pos_points:.0f} (model) vs '
                        f'{pos_naive_points:.0f} (form) vs {pos_cost_points:.0f} (cost)')
            
            gw_points += pos_points
            gw_naive_points += pos_naive_points
            gw_cost_points += pos_cost_points
        
        logging.info(f'GW{target_gameweek} TOTAL: {gw_points:.0f} (model) vs '
                    f'{gw_naive_points:.0f} (form) vs {gw_cost_points:.0f} (cost)')
        
        total_points += gw_points
        total_naive_points += gw_naive_points
        total_cost_points += gw_cost_points
        total_weeks += 1
        
        season.play(Query.fixtures_by_gameweek(target_gameweek))
    
    logging.info(f'\n=== Backtesting Summary ({total_weeks} gameweeks) ===')
    logging.info(f'Model avg: {total_points / total_weeks:.1f} pts/gw ({total_points:.0f} total)')
    logging.info(f'Form avg:  {total_naive_points / total_weeks:.1f} pts/gw ({total_naive_points:.0f} total)')
    logging.info(f'Cost avg:  {total_cost_points / total_weeks:.1f} pts/gw ({total_cost_points:.0f} total)')
    logging.info(f'\nFinal cache size: {pipeline.cache_info}')


if __name__ == '__main__':
    new_event_loop().run_until_complete(main())
