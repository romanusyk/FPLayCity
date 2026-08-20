"""Generate a projection run artifact.

    uv run -m src.fpl.project                          # both games, default method
    uv run -m src.fpl.project --game draft             # draft only
    uv run -m src.fpl.project --method v0-raw-dc       # a control, to compare against
    uv run -m src.fpl.project --gw-from 5 --gw-to 14   # a different horizon
    uv run -m src.fpl.project --list-methods

Reads only what is already on disk - no network. Fetch first if the snapshots are stale:

    uv run -m src.fpl.fetch
    uv run -m src.fotmob.load

Each invocation writes one immutable file per game under `data/<season>/runs/<game>/` and then
prunes to the newest `--keep`. Nothing is ever overwritten, so a run referenced by a comparison
or by stored feedback stays exactly as it was.
"""
from __future__ import annotations

import argparse
import logging
import os
from datetime import datetime

from src.fpl.loader.load import load_from_snapshots
from src.fpl.loader.store import JsonSnapshotStore, SnapshotSpec
from src.fpl.loader.utils import Season
from src.fpl.projection.artifacts import (
    DEFAULT_KEEP,
    build_run,
    prune_runs,
    write_run,
)
from src.fpl.projection.engine import ProjectionEngine
from src.fpl.projection.feedback import runs_with_feedback
from src.fpl.projection.methods import (
    DEFAULT_METHOD,
    DRAFT,
    GAMES,
    METHODS,
    ProjectionMethod,
    method as lookup_method,
)
from src.fpl.projection.vorp import (
    DEFAULT_MANAGERS,
    DRAFT_STARTING_SLOTS,
    replacement_levels,
    value_over_replacement,
)


logging.basicConfig(level=logging.INFO, format='%(levelname)s %(name)s: %(message)s')
logger = logging.getLogger(__name__)


def _snapshot_provenance(season: str) -> dict:
    """Record which stored snapshots this run read, so it can be reproduced later."""
    provenance: dict[str, str | None] = {}
    for name, base in (
        ('bootstrap', f'data/{season}/bootstrap'),
        ('fixtures', f'data/{season}/fixtures'),
        ('prior_season', f'data/{season}/prior_season/{Season.previous(season)}'),
    ):
        latest = JsonSnapshotStore(SnapshotSpec(base_path=base)).find_latest()
        provenance[name] = os.path.basename(latest[1]) if latest else None
    lineups = f'data/{season}/lineups'
    provenance['lineup_files'] = str(sum(
        len(files) for _, _, files in os.walk(lineups)
    )) if os.path.isdir(lineups) else '0'
    return provenance


def generate(
    game: str,
    method: ProjectionMethod,
    season: str,
    managers: int,
    keep: int,
    label: str | None,
    protected_runs: set[str] | None = None,
) -> str:
    """Project one game and write the artifact.

    Returns:
    - The path written.
    """
    engine = ProjectionEngine(method.params, season)
    projections = engine.project_all()

    slots = DRAFT_STARTING_SLOTS
    levels = replacement_levels(projections, managers=managers, slots=slots)
    vorp = {p.player_id: value_over_replacement(p, levels) for p in projections}

    body = build_run(
        season=season,
        game=game,
        method=method,
        projections=projections,
        replacement=levels,
        vorp_by_player=vorp,
        inputs=_snapshot_provenance(season),
        created_at=datetime.now(),
        slots=slots,
        managers=managers,
        label=label,
    )
    path = write_run(body)
    prune_runs(season, game, keep=keep, protected=protected_runs)

    top = sorted(projections, key=lambda p: -vorp[p.player_id])[:5]
    logger.info(
        "%s top 5 by VORP: %s", game,
        ", ".join(f"{p.web_name} ({p.position.name} {p.points:.0f}pts, +{vorp[p.player_id]:.0f})"
                  for p in top),
    )
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--season', default=Season.CURRENT, help=f'Season directory. Default {Season.CURRENT}.')
    parser.add_argument('--game', choices=GAMES, action='append',
                        help='Game to project. Repeatable. Default: both.')
    parser.add_argument('--method', default=DEFAULT_METHOD,
                        help=f'Method name. Default {DEFAULT_METHOD}.')
    parser.add_argument('--draft-method', help='Override the method for the draft run only.')
    parser.add_argument('--fpl-method', help='Override the method for the FPL run only.')
    parser.add_argument('--gw-from', type=int, help='First gameweek of the horizon.')
    parser.add_argument('--gw-to', type=int, help='Last gameweek of the horizon.')
    parser.add_argument('--managers', type=int, default=DEFAULT_MANAGERS,
                        help=f'Managers in the draft league. Default {DEFAULT_MANAGERS}.')
    parser.add_argument('--keep', type=int, default=DEFAULT_KEEP,
                        help=f'Runs to retain per game. Default {DEFAULT_KEEP}.')
    parser.add_argument('--label', help='Short human-readable note stored with the run.')
    parser.add_argument('--list-methods', action='store_true', help='Print the method registry and exit.')
    args = parser.parse_args()

    if args.list_methods:
        for name in sorted(METHODS):
            entry = METHODS[name]
            print(f"{name}\n    {entry.notes}\n    {entry.params}\n")
        return

    load_from_snapshots(args.season)

    overrides = {}
    if args.gw_from is not None:
        overrides['gameweek_from'] = args.gw_from
    if args.gw_to is not None:
        overrides['gameweek_to'] = args.gw_to

    games = args.game or list(GAMES)
    per_game_method = {DRAFT: args.draft_method, 'fpl': args.fpl_method}
    for game in games:
        chosen = lookup_method(per_game_method.get(game) or args.method)
        if overrides:
            chosen = ProjectionMethod(chosen.name, chosen.notes, chosen.params.replace(**overrides))
        logger.info("Projecting %s with method %s over GW%d-%d",
                    game, chosen.name, chosen.params.gameweek_from, chosen.params.gameweek_to)
        generate(
            game=game,
            method=chosen,
            season=args.season,
            managers=args.managers,
            keep=args.keep,
            label=args.label,
            protected_runs=runs_with_feedback(args.season),
        )


if __name__ == '__main__':
    main()
