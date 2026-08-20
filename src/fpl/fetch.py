"""CLI entry point for pulling FPL API data onto disk.

Usage:
    uv run -m src.fpl.fetch                 # refresh the current season's snapshots
    uv run -m src.fpl.fetch --baseline      # additionally snapshot last season's player totals

`--baseline` is the pre-season operation: until the new season kicks off, `bootstrap-static`
still reports each player's previous-season totals, so this captures them before they are
reset. See `src/fpl/loader/baseline.py` for what is captured and why.

The gameweek to fetch manager picks up to comes from `NEXT_GAMEWEEK` when it is set, and is
otherwise derived from the stored fixtures. No `.env` is needed to get started.
"""
import argparse
import asyncio
import logging

import httpx
from dotenv import load_dotenv

from src.fpl.loader.load import capture_prior_season, load
from src.fpl.loader.utils import Season, resolve_next_gameweek

logging.basicConfig(level=logging.INFO)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fetch and cache FPL API data")
    parser.add_argument(
        "--season",
        default=Season.CURRENT,
        help=f"Season directory to load into (default: {Season.CURRENT})",
    )
    parser.add_argument(
        "--freshness",
        type=int,
        default=1,
        help="Days before a cached snapshot is refetched (default: 1)",
    )
    parser.add_argument(
        "--baseline",
        action="store_true",
        help="Also snapshot the previous season's per-player totals from the live API",
    )
    parser.add_argument(
        "--baseline-only",
        action="store_true",
        help="Snapshot the previous season's totals and skip the regular load",
    )
    parser.add_argument(
        "--next-gameweek",
        type=int,
        help=(
            "Gameweek to fetch manager picks up to. Defaults to NEXT_GAMEWEEK from the "
            "environment, or the earliest unfinished gameweek in the stored fixtures."
        ),
    )
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    load_dotenv()

    async with httpx.AsyncClient(timeout=30.0) as client:
        if args.baseline or args.baseline_only:
            path = await capture_prior_season(client, season=args.season, freshness=args.freshness)
            logging.info("Prior-season baseline written to %s", path)
        if args.baseline_only:
            return

        next_gameweek = args.next_gameweek or resolve_next_gameweek(args.season)
        await load(client, next_gameweek, freshness=args.freshness, season=args.season)


if __name__ == "__main__":
    asyncio.run(main())
