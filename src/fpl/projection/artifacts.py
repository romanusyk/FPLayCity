"""Run artifacts: immutable projections on disk.

The one design decision that matters
------------------------------------
The web app never computes a projection. A CLI writes a run file; the app reads run files.
Everything else in the app follows from that:

- **Comparison becomes trivial.** Two runs are two files, so "what did this change move, and
  did it help" is a diff rather than a rerun.
- **Pages are instant.** Projecting ~590 players takes seconds. A page load should not.
- **Runs are reproducible.** A run records the parameters it used and the snapshots it read,
  so "why did Saka move twelve places" is always answerable after the fact.

This mirrors how the rest of the repo already works: `JsonSnapshotStore` keeps one timestamped
snapshot per resource, and runs are the derived-data sibling of `prior_season/`.

Layout
------
`data/<season>/runs/<game>/<run_id>.json`, one file per run, `run_id` being
`<timestamp>_<method>`. Split by game because draft and FPL are projected separately and their
methods can diverge.

Retention
---------
`prune_runs` keeps the newest N per game. A run that any stored feedback refers to is never
pruned, and what was kept for that reason is logged - a comparison that silently loses its
baseline is worse than a full directory.
"""
from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime

from src.fpl.loader.utils import Season, ensure_dir_exists
from src.fpl.models.immutable import PlayerType
from src.fpl.projection.engine import PlayerProjection
from src.fpl.projection.methods import GAMES, ProjectionMethod
from src.fpl.projection.vorp import ReplacementLevel


logger = logging.getLogger(__name__)


RUN_ID_PATTERN = re.compile(r'^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}-[0-9]{2}-[0-9]{2}_[A-Za-z0-9._-]+$')
"""`<timestamp>_<method>`. Enforced on read so a run id can never escape the runs directory."""

DEFAULT_KEEP = 20


def runs_dir(season: str, game: str) -> str:
    """Directory holding one game's runs for a season.

    Raises:
    - ValueError: for an unknown game, so a typo cannot create a stray directory.
    """
    if game not in GAMES:
        raise ValueError(f"Unknown game '{game}'. Known games: {', '.join(GAMES)}")
    return os.path.join('data', season, 'runs', game)


def build_run_id(method_name: str, created_at: datetime) -> str:
    """`2026-08-16T14-02-11_v1-baseline`. Colons are avoided so the id is a safe filename."""
    return f"{created_at.strftime('%Y-%m-%dT%H-%M-%S')}_{method_name}"


@dataclass(frozen=True)
class RunSummary:
    """Enough of a run to list it without reading the whole file."""

    run_id: str
    season: str
    game: str
    method: str
    created_at: str
    gameweek_from: int
    gameweek_to: int
    player_count: int
    label: str | None

    def as_dict(self) -> dict:
        return {
            'run_id': self.run_id,
            'season': self.season,
            'game': self.game,
            'method': self.method,
            'created_at': self.created_at,
            'gameweek_from': self.gameweek_from,
            'gameweek_to': self.gameweek_to,
            'player_count': self.player_count,
            'label': self.label,
        }


def build_run(
    season: str,
    game: str,
    method: ProjectionMethod,
    projections: list[PlayerProjection],
    replacement: dict[PlayerType, ReplacementLevel],
    vorp_by_player: dict[int, float],
    inputs: dict,
    created_at: datetime,
    slots: dict[PlayerType, int],
    managers: int,
    label: str | None = None,
) -> dict:
    """Assemble the JSON body of a run.

    Parameters:
    - vorp_by_player: player id -> value over replacement, computed by the caller so the same
      projections can be priced against different pools.
    - inputs: provenance - which snapshots this run read. Written verbatim.
    - slots: the slot table replacement level was priced against, and `managers` the league
      size. Both are recorded because the app recomputes replacement level live during a draft,
      and reading them back is what stops that recomputation from silently drifting from the
      run - which it did once, leaving three goalkeepers in the top ten of a live board while
      the stored board had none.
    """
    players = []
    for projection in sorted(projections, key=lambda p: -p.points):
        row = projection.as_dict()
        row['vorp'] = round(vorp_by_player[projection.player_id], 3)
        players.append(row)

    return {
        'run_id': build_run_id(method.name, created_at),
        'created_at': created_at.isoformat(timespec='seconds'),
        'season': season,
        'game': game,
        'label': label,
        'gameweek_from': method.params.gameweek_from,
        'gameweek_to': method.params.gameweek_to,
        'method': method.as_dict(),
        'inputs': inputs,
        'replacement_level': {
            position.name: level.as_dict() for position, level in replacement.items()
        },
        'valuation': {
            'managers': managers,
            'slots': {position.name: count for position, count in slots.items()},
        },
        'players': players,
    }


def write_run(body: dict) -> str:
    """Write a run to `data/<season>/runs/<game>/<run_id>.json`.

    Returns:
    - The path written.

    Raises:
    - FileExistsError: if that run id already exists. Runs are immutable; overwriting one
      would break every comparison and every piece of feedback that points at it.
    """
    path = run_path(body['season'], body['game'], body['run_id'])
    if os.path.exists(path):
        raise FileExistsError(
            f"Run {body['run_id']} already exists at {path}. Runs are immutable - generate a "
            f"new one rather than overwriting."
        )
    ensure_dir_exists(path)
    with open(path, 'w', encoding='utf-8') as handle:
        json.dump(body, handle, separators=(',', ':'))
    logger.info(
        "Wrote %s run %s (%d players, %.1f KB)",
        body['game'], body['run_id'], len(body['players']), os.path.getsize(path) / 1024,
    )
    return path


def run_path(season: str, game: str, run_id: str) -> str:
    """Filesystem path for a run.

    Raises:
    - ValueError: for a malformed run id. This is the boundary that keeps a request parameter
      from reaching an arbitrary file.
    """
    if not RUN_ID_PATTERN.match(run_id):
        raise ValueError(
            f"Malformed run id '{run_id}'. Expected <YYYY-MM-DDTHH-MM-SS>_<method>."
        )
    return os.path.join(runs_dir(season, game), f"{run_id}.json")


def load_run(season: str, game: str, run_id: str) -> dict:
    """Read one run.

    Raises:
    - FileNotFoundError: when the run does not exist, listing what does.
    """
    path = run_path(season, game, run_id)
    if not os.path.exists(path):
        available = [summary.run_id for summary in list_runs(season, game)]
        raise FileNotFoundError(
            f"No {game} run '{run_id}' for {season}. Available: {available or 'none'}"
        )
    with open(path, encoding='utf-8') as handle:
        return json.load(handle)


def list_runs(season: str | None = None, game: str | None = None) -> list[RunSummary]:
    """List stored runs, newest first.

    Parameters:
    - game: restrict to one game. None lists both.

    Raises:
    - ValueError: if a stored file is not a readable run. A corrupt artifact must surface
      rather than quietly vanish from the list.
    """
    season = season or Season.CURRENT
    summaries: list[RunSummary] = []
    for one_game in ([game] if game else list(GAMES)):
        directory = runs_dir(season, one_game)
        if not os.path.isdir(directory):
            continue
        for filename in sorted(os.listdir(directory)):
            if not filename.endswith('.json'):
                continue
            path = os.path.join(directory, filename)
            try:
                with open(path, encoding='utf-8') as handle:
                    body = json.load(handle)
                summaries.append(RunSummary(
                    run_id=body['run_id'],
                    season=body['season'],
                    game=body['game'],
                    method=body['method']['name'],
                    created_at=body['created_at'],
                    gameweek_from=body['gameweek_from'],
                    gameweek_to=body['gameweek_to'],
                    player_count=len(body['players']),
                    label=body.get('label'),
                ))
            except (json.JSONDecodeError, KeyError) as exc:
                raise ValueError(
                    f"{path} is not a readable run artifact: {exc}. Delete it or regenerate it."
                ) from exc
    summaries.sort(key=lambda summary: summary.created_at, reverse=True)
    return summaries


def prune_runs(
    season: str,
    game: str,
    keep: int = DEFAULT_KEEP,
    protected: set[str] | None = None,
) -> list[str]:
    """Delete all but the newest `keep` runs for a game.

    Parameters:
    - protected: run ids that must survive regardless of age - in practice, every run some
      stored feedback refers to.

    Returns:
    - The run ids deleted.

    Raises:
    - ValueError: for `keep` below 1. Keeping zero runs would delete the run just written.
    """
    if keep < 1:
        raise ValueError(f"keep must be at least 1, got {keep}")
    protected = protected or set()
    summaries = list_runs(season, game)
    if len(summaries) <= keep:
        return []

    deleted: list[str] = []
    kept_by_protection: list[str] = []
    for summary in summaries[keep:]:
        if summary.run_id in protected:
            kept_by_protection.append(summary.run_id)
            continue
        os.remove(run_path(season, game, summary.run_id))
        deleted.append(summary.run_id)

    if deleted:
        logger.info("Pruned %d old %s run(s): %s", len(deleted), game, ", ".join(deleted))
    if kept_by_protection:
        logger.info(
            "Kept %d old %s run(s) past the limit because feedback refers to them: %s",
            len(kept_by_protection), game, ", ".join(kept_by_protection),
        )
    return deleted
