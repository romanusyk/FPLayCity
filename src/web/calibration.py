"""Score a run against what actually happened.

Why this page exists
--------------------
`src/fpl/forecast/loss.py` has MAE and log loss, and `src/fpl/main.py` compares the total
points of selected squads. That measures the pipeline end to end and cannot tell you *which*
component is wrong - a good total can hide a minutes model that is badly calibrated and an
attack model that is compensating.

So each component is scored separately, against a stated naive baseline. A model change that
does not beat its baseline should not ship.

Before the season starts
------------------------
There is nothing to score, and this returns `resolved_gameweeks: 0` and says so rather than
rendering an empty chart. An empty chart looks like a result.

Baselines
---------
- `p_start`: Brier score against "everyone starts with probability equal to the league-wide
  start rate". Beating that is a low bar and failing it is diagnostic.
- points: MAE against "every player scores the position average".
"""
from __future__ import annotations

import logging

from src.fpl.models.immutable import PlayerType
from src.fpl.projection.history import PlayerHistory


logger = logging.getLogger(__name__)


def score_run(run: dict, histories: dict[int, PlayerHistory]) -> dict:
    """Compare a run's projections with the gameweeks that have since resolved.

    Parameters:
    - run: a loaded run artifact.
    - histories: per-match history, which gains rows as the season is re-fetched.

    Returns:
    - A report with per-gameweek and per-component scores, or an explicit "nothing resolved
      yet" when the horizon has not been played.
    """
    season = run['season']
    first, last = run['gameweek_from'], run['gameweek_to']

    actuals: dict[int, dict[int, dict]] = {}
    for player_id, history in histories.items():
        for match in history.in_season(season):
            if first <= match.gameweek <= last:
                actuals.setdefault(match.gameweek, {})[player_id] = match

    resolved = sorted(actuals)
    if not resolved:
        return {
            'run_id': run['run_id'],
            'season': season,
            'gameweek_from': first,
            'gameweek_to': last,
            'resolved_gameweeks': 0,
            'message': (
                f"No gameweek between {first} and {last} has resolved yet for {season}, so "
                f"there is nothing to score. Re-run `uv run -m src.fpl.fetch` after each "
                f"gameweek and this fills in."
            ),
            'components': {},
            'gameweeks': [],
        }

    rows = {row['player_id']: row for row in run['players']}
    per_gameweek = []
    brier_total = brier_baseline_total = brier_count = 0.0
    points_error = points_baseline_error = points_count = 0.0

    league_start_rate = _league_start_rate(actuals)
    position_means = _position_mean_points(actuals, rows)

    for gameweek in resolved:
        gw_brier = gw_points_error = gw_count = 0.0
        for player_id, match in actuals[gameweek].items():
            row = rows.get(player_id)
            if row is None:
                continue
            fixture = next(
                (entry for entry in row['fixtures'] if entry['gameweek'] == gameweek), None
            )
            if fixture is None:
                continue
            p_start = row['inputs']['minutes']['p_start']
            started = 1.0 if match.started else 0.0
            gw_brier += (p_start - started) ** 2
            brier_baseline_total += (league_start_rate - started) ** 2

            projected = fixture['points']
            gw_points_error += abs(projected - match.total_points)
            points_baseline_error += abs(
                position_means[row['position']] - match.total_points
            )
            gw_count += 1

        if not gw_count:
            continue
        brier_total += gw_brier
        points_error += gw_points_error
        brier_count += gw_count
        points_count += gw_count
        per_gameweek.append({
            'gameweek': gameweek,
            'players': int(gw_count),
            'p_start_brier': round(gw_brier / gw_count, 4),
            'points_mae': round(gw_points_error / gw_count, 3),
        })

    return {
        'run_id': run['run_id'],
        'season': season,
        'gameweek_from': first,
        'gameweek_to': last,
        'resolved_gameweeks': len(per_gameweek),
        'message': None,
        'components': {
            'p_start': {
                'metric': 'Brier score (lower is better)',
                'model': round(brier_total / brier_count, 4) if brier_count else None,
                'baseline': round(brier_baseline_total / brier_count, 4) if brier_count else None,
                'baseline_description': f'everyone starts with p={league_start_rate:.3f}',
            },
            'points': {
                'metric': 'MAE per player-match (lower is better)',
                'model': round(points_error / points_count, 3) if points_count else None,
                'baseline': round(points_baseline_error / points_count, 3) if points_count else None,
                'baseline_description': 'every player scores their position average',
            },
        },
        'gameweeks': per_gameweek,
    }


def _league_start_rate(actuals: dict[int, dict]) -> float:
    """Share of player-matches that were starts. The naive p_start baseline."""
    total = sum(len(players) for players in actuals.values())
    starts = sum(1 for players in actuals.values() for match in players.values() if match.started)
    return starts / total if total else 0.0


def _position_mean_points(actuals: dict[int, dict], rows: dict[int, dict]) -> dict[str, float]:
    """Mean actual points per position. The naive points baseline."""
    totals: dict[str, list[float]] = {}
    for players in actuals.values():
        for player_id, match in players.items():
            row = rows.get(player_id)
            if row is None:
                continue
            totals.setdefault(row['position'], []).append(match.total_points)
    means = {
        position: sum(values) / len(values) for position, values in totals.items() if values
    }
    for position in PlayerType:
        means.setdefault(position.name, 0.0)
    return means
