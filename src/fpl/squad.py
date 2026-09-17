"""CLI entry point for connecting our classic-FPL team and refreshing its picks.

Usage:
    ./run.sh -m src.fpl.squad --entry 12345   # connect our team (once, ever)
    ./run.sh -m src.fpl.squad --refresh       # re-fetch the latest published picks
    ./run.sh -m src.fpl.squad --show          # print the squad, offline

Your entry id is the number in the URL of your team page on fantasy.premierleague.com
(`fantasy.premierleague.com/entry/<id>/event/<gw>`). Unlike a draft entry id it is permanent, so
`--entry` is a once-ever command rather than a once-a-season one - but it is still stored in
`data/fpl_entry.json` rather than written into source, because a hardcoded id that turns out to be
somebody else's team never fails loudly. It happened here; see `src/fpl/loader/fpl_squad.py`.

`--entry` fetches the entry before saving anything and prints the team and manager name it found,
so a mistyped digit shows up as a stranger's name in the terminal.

FPL publishes a squad only *after* its gameweek deadline, so `--refresh` fetches the latest
*started* gameweek. Any transfer you have made for the upcoming one is not public and cannot be
read; `#/fpl` in the review app says which gameweek the filter is based on for that reason.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
from datetime import datetime

import httpx

from src.fpl.loader.fpl_squad import (
    FplEntryConfig,
    fetch_entry,
    fetch_picks,
    load_config,
    load_squad,
    save_config,
    stored_gameweeks,
)
from src.fpl.loader.load import load_from_snapshots
from src.fpl.loader.utils import Season, resolve_next_gameweek
from src.fpl.models.immutable import Query

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Connect our classic-FPL team and refresh its picks")
    parser.add_argument('--season', default=Season.CURRENT,
                        help=f"Season directory for the picks. Default {Season.CURRENT}.")
    parser.add_argument('--entry', type=int,
                        help="Your classic-FPL entry id, from your team page URL.")
    parser.add_argument('--refresh', action='store_true',
                        help="Re-fetch the latest published picks for the connected team.")
    parser.add_argument('--show', action='store_true',
                        help="Print the stored squad and exit. No network.")
    parser.add_argument('--gameweek', type=int,
                        help="Which gameweek to fetch or show. Default: the latest available.")
    return parser.parse_args()


def print_squad(season: str, gameweek: int | None) -> None:
    """Print the fifteen, in slot order, with the captain and the bench marked."""
    squad = load_squad(season, gameweek)
    load_from_snapshots(season)
    print(f"{squad.entry_name} ({squad.manager}), entry {squad.entry_id}")
    print(f"squad as at GW{squad.gameweek}, captured {squad.captured_at}"
          + (f", chip {squad.active_chip}" if squad.active_chip else ""))
    print(f"stored gameweeks: {stored_gameweeks(season, squad.entry_id)}")
    for pick in squad.picks:
        player = Query.player(pick.element)
        marks = ''.join([
            ' (C)' if pick.is_captain else '',
            ' (V)' if pick.is_vice_captain else '',
            '  [bench]' if pick.is_bench else '',
        ])
        print(f"  {pick.slot:>2}  {pick.position}  {player.web_name}{marks}")


async def main() -> None:
    args = parse_args()

    if args.show:
        print_squad(args.season, args.gameweek)
        return

    if args.entry is None and not args.refresh:
        raise SystemExit("Nothing to do. Pass --entry <id> to connect, or --refresh, or --show.")

    async with httpx.AsyncClient(timeout=30.0) as client:
        if args.entry is not None:
            entry = await fetch_entry(client, args.entry)
            manager = f"{entry.get('player_first_name', '')} {entry.get('player_last_name', '')}".strip()
            logger.info(
                "Entry %d is '%s', managed by %s (%s points overall, started GW%s). If that is not "
                "your team, re-run with the right id - nothing else identifies it.",
                args.entry, entry['name'], manager or 'unknown',
                entry.get('summary_overall_points'), entry.get('started_event'),
            )
            config = FplEntryConfig(
                entry_id=args.entry,
                entry_name=entry['name'],
                manager=manager or 'unknown',
                connected_at=datetime.now().isoformat(timespec='seconds'),
            )
            save_config(config)
        else:
            config = load_config()

        # FPL publishes picks only once a gameweek has started, so the newest squad that exists is
        # the one for the gameweek before the next deadline. Walking down from there rather than
        # guessing keeps a 404 (nothing published yet) distinguishable from a fetch failure.
        latest_started = resolve_next_gameweek() - 1
        if args.gameweek is not None:
            wanted = [args.gameweek]
        elif latest_started < 1:
            logger.info(
                "No gameweek has started yet, so no squad is published. Run this again after the "
                "GW1 deadline."
            )
            return
        else:
            wanted = list(range(1, latest_started + 1))

        for gameweek in wanted:
            await fetch_picks(client, config.entry_id, gameweek, season=args.season, freshness=1)

    squad = load_squad(args.season, args.gameweek)
    logger.info(
        "%s: %d players stored for GW%d (%d on the bench)",
        squad.entry_name, len(squad.picks), squad.gameweek,
        sum(1 for pick in squad.picks if pick.is_bench),
    )


if __name__ == '__main__':
    asyncio.run(main())
