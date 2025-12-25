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

from httpx import AsyncClient

from src.fpl.loader.load import bootstrap
from src.fotmob.load import load_saved_match_details
from src.fpl.compute.prediction import PredictionPipeline
from src.fotmob.rotation.fotmob_adapter import FotmobAdapter, build_gameweek_mapper
from src.fotmob.rotation.rotation_config import RotationConfig
from src.fpl.models.immutable import Query
from src.fpl.models.stats import StatsQuery

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def build_pipeline(next_gameweek: int) -> PredictionPipeline:
    client = AsyncClient()
    logger.info("Loading FPL data...")
    await bootstrap(client, next_gameweek)

    # Read saved FotMob lineups/match details from disk (no fetching here)
    season_dir = "2025-2026"
    logger.info("Loading FotMob data...")
    match_details = load_saved_match_details(season=season_dir)
    total_matches = sum(len(v) for v in match_details.values())
    logger.info(
        f"Loaded {total_matches} match lineups across {len(match_details)} teams from data/{season_dir}/lineups"
    )
    for team_name, matches in list(match_details.items())[:5]:
        sample_ids = [m.match_id for m in matches[:3]]
        logger.info(f"- {team_name}: {len(matches)} matches (first 3 ids: {sample_ids})")

    logger.info("Building rotation config...")
    rotation_config = RotationConfig()
    gw_mapper = build_gameweek_mapper(Query.all_gameweeks())

    logger.info("Building FotMob adapter...")
    fotmob_adapter = FotmobAdapter(match_details, rotation_config, gw_mapper)

    logger.info("Building stats query...")
    StatsQuery.build(next_gameweek)

    logging.info("Creating lazy computation pipeline...")
    pipeline = PredictionPipeline(rotation_adapter=fotmob_adapter)

    return pipeline
