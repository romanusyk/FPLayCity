"""
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
from asyncio import new_event_loop

from httpx import AsyncClient

from src.fpl.loader.load import bootstrap
from src.fpl.loader.fotmob import load_saved_match_details
from src.fpl.compute.prediction import PredictionPipeline
from src.fpl.models.immutable import PlayerType, Query
from src.fpl.models.season import Season
from src.fpl.forecast.models import SimplePtsModel, PlayerPointsFormModel

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
    logging.info("Loading FPL data...")
    await bootstrap(client)
    
    # Read saved FotMob lineups/match details from disk (no fetching here)
    season_dir = "2025-2026"
    match_details = load_saved_match_details(season=season_dir)
    total_matches = sum(len(v) for v in match_details.values())
    logging.info(f"Loaded {total_matches} match lineups across {len(match_details)} teams from data/{season_dir}/lineups")
    for team_name, matches in list(match_details.items())[:5]:
        sample_ids = [m.match_id for m in matches[:3]]
        logging.info(f"- {team_name}: {len(matches)} matches (first 3 ids: {sample_ids})")
    
    next_gameweek = 11
    min_history_gws = 5
    horizon = 3
    
    logging.info("Creating lazy computation pipeline...")
    pipeline = PredictionPipeline()
    
    logging.info(f"\n=== Predictions for GWs {next_gameweek} to {next_gameweek + horizon - 1} ===")
    predictions = pipeline.predict(
        next_gameweek=next_gameweek,
        target_gameweek=next_gameweek,
        horizon=horizon,
        min_history_gws=min_history_gws
    )
    
    logging.info(f"Cache info: {pipeline.cache_info}")
    logging.info(f"\nTop 10 players by predicted points:")
    for i, player in enumerate(predictions.players_total_points_desc[:10], 1):
        logging.info(f"{i}. {player}")
    
    logging.info(f"\nTop 5 teams by predicted clean sheets:")
    for i, team_prediction in enumerate(predictions.teams_total_cs_desc[:5], 1):
        logging.info(f"{i}. {team_prediction.team}: {team_prediction.cs_prediction} CS predicted")
    
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
    client = AsyncClient()
    new_event_loop().run_until_complete(main(client))
