"""HTTP routes over stored run artifacts.

Everything here reads files. The one exception is the draft board, which recomputes value over
replacement against the live undrafted pool - that is arithmetic on an already-computed
projection, not a projection, and it has to be live because replacement level moves with every
pick.

Route groups
------------
- `/api/runs`      - what has been generated
- `/api/board`     - a slim, sortable table per game; the draft lens recomputes VORP
- `/api/player`    - one player in full, plus their per-match history
- `/api/compare`   - two runs of the same game, by rank movement
- `/api/feedback`  - record and read disagreements
- `/api/draft`     - live draft state
- `/api/calibration` - scores runs against actual results once gameweeks resolve

Errors are HTTP 4xx with the same message the CLI would print, rather than an empty table. A
board that quietly renders zero rows because a run id was mistyped is the failure mode this
repo exists to avoid.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query as QueryParam
from pydantic import BaseModel, Field

from src.fpl.loader.utils import Season
from src.fpl.models.immutable import PlayerType, Query
from src.fpl.projection import artifacts, feedback as feedback_store
from src.fpl.projection.methods import DRAFT, GAMES, METHODS
from src.fpl.projection.vorp import (
    DRAFT_SQUAD_SLOTS,
    DRAFT_STARTING_SLOTS,
    ReplacementLevel,
    tier_breaks,
)
from src.web import calibration, draft_state, opportunity
from src.web.context import AppContext


logger = logging.getLogger(__name__)

router = APIRouter(prefix='/api')

BOARD_FIELDS = (
    'player_id', 'web_name', 'team', 'position', 'price', 'ownership',
    'points', 'points_per_gameweek', 'points_per_million', 'components', 'flags',
)
"""Columns a board needs. The full row carries ten fixtures of detail nobody sorts by."""

BOARD_INPUTS = (
    ('p_start', ('minutes', 'p_start')),
    ('expected_minutes', ('minutes', 'expected_minutes')),
    ('dc_hit_rate', ('defensive_contribution', 'hit_rate')),
    ('dc_sample', ('defensive_contribution', 'starts')),
)
"""Inputs promoted to top-level board columns, because they are worth sorting by."""


class FeedbackRequest(BaseModel):
    """One recorded disagreement. Mirrors `FeedbackEntry` minus the fields we can look up."""

    run_id: str
    game: str
    player_id: int
    reason: str
    gameweek: int | None = None
    note: str = ''
    your_p_start: float | None = Field(default=None, ge=0.0, le=1.0)
    your_points: float | None = None


class DraftPickRequest(BaseModel):
    player_id: int
    owner: str | None = None
    """'mine', 'other', or null to put the player back on the board."""


def _context() -> AppContext:
    return AppContext.current()


def _resolve_run(game: str, run_id: str | None) -> dict:
    """Load a run, defaulting to the newest for that game.

    Raises:
    - HTTPException 404: when nothing has been generated yet, or the id is unknown.
    """
    season = _context().season
    if game not in GAMES:
        raise HTTPException(400, f"Unknown game '{game}'. Known: {', '.join(GAMES)}")
    if run_id is None:
        summaries = artifacts.list_runs(season, game)
        if not summaries:
            raise HTTPException(
                404,
                f"No {game} runs for {season}. Generate one with: "
                f"uv run -m src.fpl.project --game {game}",
            )
        run_id = summaries[0].run_id
    try:
        return artifacts.load_run(season, game, run_id)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(404, str(exc)) from exc


def _slim(row: dict) -> dict:
    """The board view of one run row: sortable columns only, no fixture detail."""
    slim = {name: row[name] for name in BOARD_FIELDS}
    for column, (group, field) in BOARD_INPUTS:
        slim[column] = row['inputs'][group][field]
    return slim


@router.get('/config')
def config() -> dict:
    """Everything the page needs before it can render anything."""
    context = _context()
    return {
        'season': context.season,
        'games': list(GAMES),
        'next_gameweek': context.next_gameweek,
        'methods': {name: entry.as_dict() for name, entry in METHODS.items()},
        'positions': [position.name for position in PlayerType if position is not PlayerType.MNG],
        'teams': sorted(team.short_name for team in Query.all_teams()),
        'reasons': list(feedback_store.REASONS),
        'draft_roster_slots': {p.name: n for p, n in DRAFT_SQUAD_SLOTS.items()},
        'draft_starting_slots': {p.name: n for p, n in DRAFT_STARTING_SLOTS.items()},
    }


@router.get('/runs')
def runs(game: str | None = None) -> dict:
    """List stored runs, newest first."""
    summaries = artifacts.list_runs(_context().season, game)
    return {'runs': [summary.as_dict() for summary in summaries]}


@router.get('/board')
def board(
    game: str = QueryParam(...),
    run_id: str | None = None,
    live: bool = False,
    picks_until_next_turn: int | None = None,
) -> dict:
    """A sortable table for one game.

    Parameters:
    - live: draft only. Recompute replacement level and VORP against the undrafted pool, tag
      each row with who owns the player, and add the two opportunity-cost figures from
      `src/web/opportunity.py` - the gap to the next player at the position, and what waiting a
      round costs at each position.
    - picks_until_next_turn: how many players get taken before your next pick, for the waiting
      simulation. Defaults to `2 * (managers - 1)`.
    """
    run = _resolve_run(game, run_id)
    rows = [dict(_slim(row), vorp=row['vorp']) for row in run['players']]
    replacement = run['replacement_level']
    state = None
    waiting = None

    if game == DRAFT and live:
        state = draft_state.load(_context().season)
        levels = _live_replacement_levels(run, state.all_taken)
        replacement = {position: level.as_dict() for position, level in levels.items()}
        for row in rows:
            row['vorp'] = round(row['points'] - levels[row['position']].points, 3)
            row['owner'] = state.taken.get(row['player_id'])

        picks = (
            picks_until_next_turn if picks_until_next_turn is not None
            else opportunity.picks_between_turns(state.managers)
        )
        try:
            drops = opportunity.next_best_drop(rows)
            waiting = {
                'picks_until_next_turn': picks,
                'positions': opportunity.wait_costs(rows, picks),
            }
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        for row in rows:
            row['drop_next'] = drops.get(row['player_id'])

    rows.sort(key=lambda row: -row['vorp'] if game == DRAFT else -row['points'])
    ranked = _with_tiers(rows, game)
    return {
        'run': _run_header(run),
        'replacement_level': replacement,
        'waiting': waiting,
        'players': ranked,
        'draft': {
            'taken': len(state.taken) if state else 0,
            'mine': sorted(state.my_players) if state else [],
            'managers': state.managers if state else None,
        } if game == DRAFT else None,
    }


def _run_header(run: dict) -> dict:
    return {
        'run_id': run['run_id'],
        'game': run['game'],
        'season': run['season'],
        'created_at': run['created_at'],
        'label': run.get('label'),
        'method': run['method'],
        'gameweek_from': run['gameweek_from'],
        'gameweek_to': run['gameweek_to'],
        'inputs': run['inputs'],
    }


def _valuation_slots(run: dict) -> dict[PlayerType, int]:
    """The slot table a run priced replacement level against.

    Raises:
    - HTTPException 409: for a run written before `valuation` was recorded. Guessing would risk
      reintroducing exactly the drift this field exists to prevent, so the honest answer is to
      say the run is too old and needs regenerating.
    """
    stored = run.get('valuation', {}).get('slots')
    if not stored:
        raise HTTPException(
            409,
            f"Run {run['run_id']} predates the stored valuation slots, so live replacement "
            f"level cannot be recomputed without risking a mismatch with the stored board. "
            f"Regenerate it: uv run -m src.fpl.project --method {run['method']['name']}",
        )
    return {PlayerType[name]: count for name, count in stored.items()}


def _live_replacement_levels(run: dict, drafted: set[int]) -> dict[str, ReplacementLevel]:
    """Recompute replacement level from a stored run against the undrafted pool.

    Works on the run's rows rather than re-projecting, which is why this can be a request
    handler at all. Same logic as `vorp.replacement_levels`, expressed over plain dicts.

    The slot table comes from the run itself, never from a constant here. This function used to
    keep its own copy, and when the projector switched to starting slots the live board silently
    kept pricing against roster slots - so an idle draft board showed three goalkeepers in the
    top ten that the stored board did not have.
    """
    slots = _valuation_slots(run)
    managers = run.get('valuation', {}).get('managers') or draft_state.load(run['season']).managers
    levels: dict[str, ReplacementLevel] = {}
    for position, per_manager in slots.items():
        pool = [row for row in run['players'] if row['position'] == position.name]
        available = [row for row in pool if row['player_id'] not in drafted]
        if not available:
            raise HTTPException(
                409,
                f"Every projected {position.name} is marked as drafted, so replacement level "
                f"is undefined. Un-mark someone at /api/draft/state.",
            )
        taken_here = len(pool) - len(available)
        remaining = max(0, per_manager * managers - taken_here)
        available.sort(key=lambda row: -row['points'])
        replacement = available[min(remaining, len(available) - 1)]
        levels[position.name] = ReplacementLevel(
            position=position,
            points=replacement['points'],
            player_id=replacement['player_id'],
            web_name=replacement['web_name'],
            remaining_picks=remaining,
            pool_size=len(available),
        )
    return levels


TIER_HORIZON = 80
"""Only the drafted portion of the board is tiered. Below it every gap is noise."""


def _with_tiers(rows: list[dict], game: str) -> list[dict]:
    """Number the rows, and on a draft board group the top of them into tiers.

    Only the draft board gets tiers. In classic FPL you are optimising a budget, not deciding
    when it is safe to let a round pass, so a tier break means nothing there.
    """
    for index, row in enumerate(rows):
        row['rank'] = index + 1
        row['tier'] = None
    if game != DRAFT or not rows:
        return rows
    horizon = min(len(rows), TIER_HORIZON)
    breaks = set(tier_breaks([row['vorp'] for row in rows[:horizon]]))
    tier = 1
    for index, row in enumerate(rows[:horizon]):
        if index in breaks:
            tier += 1
        row['tier'] = tier
    return rows


@router.get('/player')
def player(
    player_id: int = QueryParam(...),
    game: str = QueryParam(DRAFT),
    run_id: str | None = None,
) -> dict:
    """One player in full: components, inputs, sample sizes and per-match history."""
    run = _resolve_run(game, run_id)
    row = next((entry for entry in run['players'] if entry['player_id'] == player_id), None)
    if row is None:
        raise HTTPException(404, f"Player {player_id} is not in run {run['run_id']}.")

    context = _context()
    history = context.histories.get(player_id)
    fpl_player = Query.player(player_id)
    return {
        'run': _run_header(run),
        'player': row,
        'news': fpl_player.news,
        'status': fpl_player.status,
        'set_piece_roles': fpl_player.set_piece_roles,
        'history': _history_rows(history, fpl_player.player_type),
        'history_coverage': history.coverage if history else {},
        'feedback': [
            entry.as_dict() for entry in context.feedback_for(player_id)
        ],
    }


def _history_rows(history, position: PlayerType) -> list[dict]:
    """Per-match rows for the detail strip, newest last.

    Includes whether each match cleared the defensive threshold, because that is the whole
    argument for using a hit rate rather than a mean and it should be visible match by match
    rather than summarised into a percentage you have to take on trust.
    """
    if history is None:
        return []
    return [
        {
            'season': match.season,
            'gameweek': match.gameweek,
            'opponent': match.opponent,
            'was_home': match.was_home,
            'minutes': match.minutes,
            'started': match.started,
            'points': match.total_points,
            'goals': match.goals_scored,
            'assists': match.assists,
            'clean_sheet': bool(match.clean_sheets),
            'defensive_actions': match.defensive_contribution,
            'defensive_hit': match.cleared_defensive_threshold(position),
            'bonus': match.bonus,
            'expected_goals': round(match.expected_goals, 2),
            'expected_assists': round(match.expected_assists, 2),
        }
        for match in history.matches
    ]


@router.get('/compare')
def compare(
    game: str = QueryParam(...),
    a: str = QueryParam(...),
    b: str = QueryParam(...),
    limit: int = 25,
) -> dict:
    """Two runs of the same game, by what moved.

    Sorted by absolute rank change, because a player who moved forty places is the story and a
    player who moved one is not.
    """
    run_a, run_b = _resolve_run(game, a), _resolve_run(game, b)
    sort_key = 'vorp' if game == DRAFT else 'points'

    def ranked(run: dict) -> dict[int, tuple[int, dict]]:
        rows = sorted(run['players'], key=lambda row: -row[sort_key])
        return {row['player_id']: (index + 1, row) for index, row in enumerate(rows)}

    left, right = ranked(run_a), ranked(run_b)
    movers = []
    for player_id, (rank_b, row_b) in right.items():
        if player_id not in left:
            continue
        rank_a, row_a = left[player_id]
        movers.append({
            'player_id': player_id,
            'web_name': row_b['web_name'],
            'team': row_b['team'],
            'position': row_b['position'],
            'rank_a': rank_a,
            'rank_b': rank_b,
            'rank_delta': rank_a - rank_b,
            'points_a': row_a['points'],
            'points_b': row_b['points'],
            'points_delta': round(row_b['points'] - row_a['points'], 2),
        })
    movers.sort(key=lambda entry: -abs(entry['rank_delta']))

    only_in_b = sorted(set(right) - set(left))
    only_in_a = sorted(set(left) - set(right))
    return {
        'a': _run_header(run_a),
        'b': _run_header(run_b),
        'param_diff': _param_diff(run_a['method']['params'], run_b['method']['params']),
        'risers': [entry for entry in movers if entry['rank_delta'] > 0][:limit],
        'fallers': [entry for entry in movers if entry['rank_delta'] < 0][:limit],
        'unchanged': sum(1 for entry in movers if entry['rank_delta'] == 0),
        'only_in_a': only_in_a,
        'only_in_b': only_in_b,
        'mean_abs_rank_delta': (
            round(sum(abs(entry['rank_delta']) for entry in movers) / len(movers), 2)
            if movers else 0.0
        ),
    }


def _param_diff(params_a: dict, params_b: dict) -> dict:
    """Only the parameters that actually differ. The rest is noise in a comparison view."""
    return {
        name: {'a': params_a.get(name), 'b': params_b.get(name)}
        for name in sorted(set(params_a) | set(params_b))
        if params_a.get(name) != params_b.get(name)
    }


@router.get('/feedback')
def read_feedback(gameweek: int | None = None) -> dict:
    entries = feedback_store.load_feedback(_context().season, gameweek)
    return {'feedback': [entry.as_dict() for entry in entries]}


@router.post('/feedback')
def write_feedback(request: FeedbackRequest) -> dict:
    """Record a disagreement, capturing the model's numbers alongside yours.

    Storing the model's value at the time is what makes the entry scoreable later. Without it
    the note is an opinion about a number nobody kept.
    """
    context = _context()
    run = _resolve_run(request.game, request.run_id)
    row = next((entry for entry in run['players'] if entry['player_id'] == request.player_id), None)
    if row is None:
        raise HTTPException(404, f"Player {request.player_id} is not in run {run['run_id']}.")

    entry = feedback_store.FeedbackEntry(
        season=context.season,
        game=request.game,
        run_id=run['run_id'],
        gameweek=request.gameweek or context.next_gameweek,
        player_id=request.player_id,
        web_name=row['web_name'],
        reason=request.reason,
        note=request.note,
        your_p_start=request.your_p_start,
        your_points=request.your_points,
        model_p_start=row['inputs']['minutes']['p_start'],
        model_points=row['points'],
    )
    try:
        path = feedback_store.save_feedback(entry)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    context.invalidate_feedback()
    return {'saved': path, 'entry': entry.as_dict()}


@router.get('/draft/state')
def read_draft_state() -> dict:
    state = draft_state.load(_context().season)
    return state.as_dict()


@router.post('/draft/pick')
def set_draft_pick(request: DraftPickRequest) -> dict:
    """Mark a player taken, or put them back on the board."""
    state = draft_state.load(_context().season)
    try:
        state.set_owner(request.player_id, request.owner)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    draft_state.save(state)
    return state.as_dict()


@router.post('/draft/reset')
def reset_draft_state() -> dict:
    """Clear every pick. Used between mock drafts and after a redraft."""
    state = draft_state.DraftState(season=_context().season)
    draft_state.save(state)
    return state.as_dict()


@router.get('/calibration')
def calibration_report(game: str = QueryParam(DRAFT), run_id: str | None = None) -> dict:
    """Score a run against whatever gameweeks have actually resolved."""
    run = _resolve_run(game, run_id)
    return calibration.score_run(run, _context().histories)
