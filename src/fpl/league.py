"""CLI entry point for connecting a draft league and refreshing who owns whom.

Usage:
    ./run.sh -m src.fpl.league --entry 12345      # find the league this team plays in, then fetch
    ./run.sh -m src.fpl.league --entry 12345 --league 10519   # when the team is in several
    ./run.sh -m src.fpl.league --refresh          # re-fetch ownership for the connected league
    ./run.sh -m src.fpl.league --show             # print who owns whom, no network

Your entry id is the number in the URL of your team page on draft.premierleague.com. It is
**re-issued every season**, which is why it is stored per season in `data/<season>/draft_league.json`
rather than written into source - see `src/fpl/loader/draft_league.py`.

Run this before the waiver deadline. Ownership changes the moment somebody else's claim clears,
and `#/waivers` in the review app reads the snapshot this writes.
"""
from __future__ import annotations

import argparse
import asyncio
import logging

import httpx

from src.fpl.loader.draft_league import (
    LeagueConfig,
    build_ownership,
    discover_leagues,
    fetch_league,
    load_config,
    load_ownership,
    save_config,
)
from src.fpl.loader.load import load_from_snapshots
from src.fpl.loader.utils import Season
from src.fpl.models.immutable import Query

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Connect a draft league and refresh its ownership")
    parser.add_argument('--season', default=Season.CURRENT,
                        help=f"Season directory. Default {Season.CURRENT}.")
    parser.add_argument('--entry', type=int, help="Your draft entry id, from your team page URL.")
    parser.add_argument('--league', type=int,
                        help="League id, needed only when your entry is in more than one league.")
    parser.add_argument('--refresh', action='store_true',
                        help="Re-fetch ownership for the league already connected.")
    parser.add_argument('--show', action='store_true',
                        help="Print the stored ownership map and exit. No network.")
    return parser.parse_args()


def print_ownership(season: str) -> None:
    """Print each squad and the size of the free-agent pool."""
    ownership = load_ownership(season)
    load_from_snapshots(season)
    print(f"{ownership.league_name} (league {ownership.league_id}), {ownership.transaction_mode} mode")
    print(f"fetched {ownership.fetched_at}")
    for entry in ownership.entries.values():
        squad = sorted(ownership.squad_of(entry.entry_id))
        label = f"{entry.entry_name} ({entry.manager})" + (" <- you" if entry.is_mine else "")
        print(f"\n{label}, waiver pick {entry.waiver_pick}")
        for element in squad:
            name = Query.player(element).web_name
            print(f"  {name}")
    claimable = [element for element in ownership.owner_by_element if ownership.is_claimable(element)]
    print(f"\nclaimable now: {len(claimable)}")
    if ownership.unknown_statuses:
        print(f"unmodelled status codes: {ownership.unknown_statuses}")


async def main() -> None:
    args = parse_args()

    if args.show:
        print_ownership(args.season)
        return

    async with httpx.AsyncClient(timeout=30.0) as client:
        if args.refresh and args.entry is None:
            config = load_config(args.season)
            league_id, entry_id = config.league_id, config.entry_id
        elif args.entry is not None:
            entry, league_ids = await discover_leagues(client, args.entry)
            logger.info(
                "Entry %d is '%s' (%s %s), in league(s) %s",
                args.entry, entry['name'], entry.get('player_first_name'),
                entry.get('player_last_name'), league_ids or 'none',
            )
            if args.league is not None:
                league_id = args.league
                if league_ids and league_id not in league_ids:
                    raise SystemExit(
                        f"Entry {args.entry} is not in league {league_id}. It is in {league_ids}."
                    )
            elif len(league_ids) == 1:
                league_id = league_ids[0]
            else:
                raise SystemExit(
                    f"Entry {args.entry} is in {len(league_ids)} leagues ({league_ids}). "
                    f"Re-run with --league <id> to say which one."
                )
            entry_id = args.entry
        else:
            raise SystemExit("Nothing to do. Pass --entry <id> to connect, or --refresh, or --show.")

        details, element_status, fetched_at = await fetch_league(client, league_id, season=args.season)

    ownership = build_ownership(
        season=args.season,
        my_entry_id=entry_id,
        details=details,
        element_status=element_status,
        fetched_at=fetched_at,
    )
    mine = ownership.my_entry
    save_config(LeagueConfig(
        season=args.season,
        league_id=ownership.league_id,
        entry_id=mine.entry_id,
        entry_name=mine.entry_name,
        manager=mine.manager,
        league_name=ownership.league_name,
        transaction_mode=ownership.transaction_mode,
    ))
    claimable = sum(1 for element in ownership.owner_by_element if ownership.is_claimable(element))
    logger.info(
        "%s: %d squads, %d players owned, %d claimable now",
        ownership.league_name, len(ownership.entries),
        sum(1 for owner in ownership.owner_by_element.values() if owner is not None), claimable,
    )


if __name__ == '__main__':
    asyncio.run(main())
