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
- `/api/waivers`   - in-season swaps: what a free agent would add to your own squad
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
from src.fpl.projection.methods import DEFAULT_METHOD, DRAFT, FPL, GAMES, METHODS
from src.fpl.projection.vorp import (
    DRAFT_SQUAD_SLOTS,
    DRAFT_STARTING_SLOTS,
    ReplacementLevel,
    tier_breaks,
)
from src.fpl.loader import draft_league, fpl_squad
from src.web import (
    calibration,
    draft_state,
    opportunity,
    transfers as transfers_module,
    waivers as waivers_module,
)
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
        # Newest run *of the default method*, not simply the newest run. Generating a control
        # would otherwise silently become what every default page load shows, and a control is
        # by definition the board you do not want to act on.
        preferred = [s for s in summaries if s.method == DEFAULT_METHOD]
        summaries = preferred or summaries
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


def _board_row(row: dict, gameweeks: list[int], horizons: tuple[int, ...]) -> dict:
    """A slim row plus its horizon columns.

    `points` and every component stay what the run says they are: totals over the *whole* run.
    They cannot be sliced - the artifact stores points per fixture but components only in total -
    so the horizon columns are separate rather than a re-scaling of them, and the page says which
    is which. Per-gameweek here divides by gameweeks in the horizon, not by fixtures played, so a
    blank counts as the zero it is.
    """
    totals = waivers_module.horizon_totals(row, gameweeks, horizons)
    price = row['price']
    slim = dict(_slim(row), vorp=row['vorp'])
    slim['horizon_points'] = totals['points']
    slim['horizon_blanks'] = totals['blanks']
    slim['horizon_per_gameweek'] = {
        horizon: round(points / horizon, 3) for horizon, points in totals['points'].items()
    }
    slim['horizon_per_million'] = {
        horizon: round(points / price, 3) if price else 0.0
        for horizon, points in totals['points'].items()
    }
    return slim


def _squad_block(
    run: dict, rows: list[dict], gameweeks: list[int], horizons: tuple[int, ...], deciding: int,
) -> dict | None:
    """Tag the rows we own and value the squad, for a classic-FPL board.

    Mutates `rows`: each of our fifteen gets a `squad` dict (slot, captaincy, bench) and everyone
    else gets None, which is what the board filters on. Returns the squad header, or None when no
    team is connected or nothing has been published for it yet - both are normal states, and the
    board renders without the filter rather than erroring.

    The valuation is the best legal XI at each horizon, reusing `SquadValuation` - a classic squad
    is 2/5/5/3 and starts eleven under the same formation rules as a draft squad. Two things it is
    deliberately not: it does not double the captain (who you will captain in GW+2 is not known,
    and the stored armband is last gameweek's), and it does not model auto-subs. It is what your
    fifteen are worth if you pick the best eleven each week, which is the number a transfer moves.

    A squad player missing from the run is reported by name rather than raised on: the board must
    still render. Missing anyone means no valuation, because a fourteen-player XI is not a number.
    """
    entry = _fpl_entry(_context().season)
    if entry is None or entry['squad'] is None:
        return None
    squad = entry['squad']
    picks = {pick['element']: pick for pick in squad['picks']}
    by_id = {row['player_id']: row for row in rows}
    for row in rows:
        row['squad'] = picks.get(row['player_id'])

    stored = {row['player_id']: row for row in run['players']}
    missing = [element for element in picks if element not in stored]
    points = None
    if not missing:
        try:
            valuation = waivers_module.SquadValuation(
                [stored[element] for element in picks], gameweeks)
            points = {horizon: round(valuation.baseline(horizon), 2) for horizon in horizons}
        except ValueError as exc:
            # A squad that is not 2/5/5/3 by the time it reaches here is a real inconsistency, but
            # it is not worth a blank board: report it beside the filter.
            logger.warning("Not valuing the squad: %s", exc)
            missing = []
            points = None
    else:
        logger.info(
            "%d squad player(s) have no row in run %s and the squad is not valued: %s",
            len(missing), run['run_id'], missing,
        )
    return {
        'entry_id': squad['entry_id'],
        'entry_name': squad['entry_name'],
        'manager': squad['manager'],
        'gameweek': squad['gameweek'],
        'captured_at': squad['captured_at'],
        'active_chip': squad['active_chip'],
        'size': len(picks),
        'points': points,
        'sort_horizon': deciding,
        'missing': [
            {'element': element, 'web_name': by_id[element]['web_name']}
            if element in by_id else {'element': element, 'web_name': None}
            for element in missing
        ],
    }


@router.get('/config')
def config() -> dict:
    """Everything the page needs before it can render anything."""
    context = _context()
    return {
        'season': context.season,
        'games': list(GAMES),
        'next_gameweek': context.next_gameweek,
        'methods': {name: entry.as_dict() for name, entry in METHODS.items()},
        'default_method': DEFAULT_METHOD,
        'positions': [position.name for position in PlayerType if position is not PlayerType.MNG],
        'teams': sorted(team.short_name for team in Query.all_teams()),
        'reasons': list(feedback_store.REASONS),
        'draft_roster_slots': {p.name: n for p, n in DRAFT_SQUAD_SLOTS.items()},
        'draft_starting_slots': {p.name: n for p, n in DRAFT_STARTING_SLOTS.items()},
        'waivers': {
            'horizon_limit': waivers_module.HORIZON_LIMIT,
            'max_horizons': waivers_module.MAX_HORIZONS,
            'default_horizons': list(waivers_module.DEFAULT_HORIZONS),
            'league': _league_config(context.season),
        },
        'fpl_entry': _fpl_entry(context.season),
    }


def _fpl_entry(season: str) -> dict | None:
    """Our classic-FPL team and the squad we hold, or None when none is connected.

    Two separate absences, both normal, both reported rather than raised: no team connected (a
    fresh checkout) and a team with no published picks yet (before GW1 has started). The FPL board
    offers its squad filter only when both are present, and says which command to run when they
    are not. A *broken* config still raises - a typo must not read as "no team".
    """
    config = fpl_squad.load_config_or_none()
    if config is None:
        return None
    entry = dict(config.as_dict(), squad=None, note=None)
    try:
        squad = fpl_squad.load_squad(season, config=config)
    except FileNotFoundError as exc:
        entry['note'] = str(exc)
        return entry
    except ValueError as exc:
        raise HTTPException(500, str(exc)) from exc
    entry['squad'] = squad.as_dict()
    return entry


def _league_config(season: str) -> dict | None:
    """The connected draft league, or None when there is not one yet.

    A missing league is a normal state - it is what a fresh checkout looks like - so it is
    reported rather than raised, and the waivers page turns it into instructions. A *broken*
    config still raises: silently treating it as absent would hide a typo in real ids.
    """
    try:
        return draft_league.load_config(season).as_dict()
    except FileNotFoundError:
        return None
    except ValueError as exc:
        raise HTTPException(500, str(exc)) from exc


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
    horizons: str | None = None,
    sort_horizon: int | None = None,
) -> dict:
    """A sortable table for one game.

    Parameters:
    - live: draft only. Recompute replacement level and VORP against the undrafted pool, tag
      each row with who owns the player, and add the two opportunity-cost figures from
      `src/web/opportunity.py` - the gap to the next player at the position, and what waiting a
      round costs at each position.
    - picks_until_next_turn: how many players get taken before your next pick, for the waiting
      simulation. Defaults to `2 * (managers - 1)`.
    - horizons: up to three comma-separated gameweek counts, e.g. `1,3,5`, each of which must fit
      inside the run. Every row carries its points at each of them, so a one-week punt and a
      five-week hold are visible in the same line. Parsed by the same rules as the waivers page
      (`src/web/waivers.py`) - one definition of what "3 GW" means, for both screens.
    - sort_horizon: which of them ranks a classic-FPL board, and drives its per-gameweek and
      per-million columns. Defaults to the middle one requested. The draft board still ranks on
      VORP, which is priced over the whole run and has no horizon.

    Errors:
    - 400 for an unusable horizon, or a run too short for one.
    """
    run = _resolve_run(game, run_id)
    try:
        wanted = (
            waivers_module.default_horizons_for(run) if not horizons
            else waivers_module.parse_horizons(horizons)
        )
        gameweeks = waivers_module.span_gameweeks(run, wanted)
        deciding = waivers_module.resolve_sort_horizon(wanted, sort_horizon)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    rows = [_board_row(row, gameweeks, wanted) for row in run['players']]
    replacement = run['replacement_level']
    state = None
    waiting = None
    if game == FPL:
        # Explicitly None rather than absent, so a board with no team connected filters the same
        # way as one where a player simply is not ours.
        for row in rows:
            row['squad'] = None
    squad = _squad_block(run, rows, gameweeks, wanted, deciding) if game == FPL else None

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

    # Explicit tie-break: two players on the same VORP must land in the same order in the stored
    # board and the live one, and float dust is not an ordering. A classic-FPL board ranks on the
    # deciding horizon rather than the run total, so the number in the '#' column and the column
    # you are deciding on always agree.
    rows.sort(key=lambda row: (
        -row['vorp'] if game == DRAFT else -row['horizon_points'][deciding],
        -row['points'], row['player_id'],
    ))
    ranked = _with_tiers(rows, game)
    return {
        'run': _run_header(run),
        'horizons': list(wanted),
        'sort_horizon': deciding,
        'gameweeks': gameweeks,
        'squad': squad,
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
        'history': _history_rows(history, fpl_player.player_type, context.season),
        'history_coverage': history.coverage if history else {},
        'feedback': [
            entry.as_dict() for entry in context.feedback_for(player_id)
        ],
    }


def _history_rows(history, position: PlayerType, season: str) -> list[dict]:
    """Per-match rows for the detail strip and the game log, newest last.

    Includes whether each match cleared the defensive threshold, because that is the whole
    argument for using a hit rate rather than a mean and it should be visible match by match
    rather than summarised into a percentage you have to take on trust.

    The scoreline is attached only for matches in the loaded season. Fixture ids are season-scoped
    like every other FPL id, so looking last season's up against this season's fixtures would
    silently return the wrong match rather than nothing.
    """
    if history is None:
        return []
    rows = []
    for match in history.matches:
        row = {
            'season': match.season,
            'gameweek': match.gameweek,
            'kickoff': match.kickoff_time[:10] if match.kickoff_time else None,
            'opponent': match.opponent,
            'was_home': match.was_home,
            'minutes': match.minutes,
            'started': match.started,
            'points': match.total_points,
            'goals': match.goals_scored,
            'assists': match.assists,
            'clean_sheet': bool(match.clean_sheets),
            'goals_conceded': match.goals_conceded,
            'saves': match.saves,
            'cards': match.yellow_cards + match.red_cards,
            'own_goals': match.own_goals,
            'defensive_actions': match.defensive_contribution,
            'defensive_hit': match.cleared_defensive_threshold(position),
            'bonus': match.bonus,
            'bps': match.bps,
            'expected_goals': round(match.expected_goals, 2),
            'expected_assists': round(match.expected_assists, 2),
            'expected_goals_conceded': round(match.expected_goals_conceded, 2),
            'team_score': None,
            'opponent_score': None,
        }
        if match.season == season:
            fixture = Query.fixture(match.fixture_id)
            if fixture.finished:
                mine, theirs = (fixture.home, fixture.away) if match.was_home else (fixture.away, fixture.home)
                row['team_score'] = mine.score
                row['opponent_score'] = theirs.score
        rows.append(row)
    return rows


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


@router.get('/waivers')
def waiver_board(
    run_id: str | None = None,
    horizons: str | None = None,
    sort_horizon: int | None = None,
    include_locked: bool = False,
    limit: int = waivers_module.DEFAULT_LIMIT,
) -> dict:
    """Rank free agents by what they would add to your own draft squad.

    Parameters:
    - horizons: up to three comma-separated gameweek counts, e.g. `1,3,5`. Each must fit inside
      the run. Omitted, they fall back to the defaults the run can answer.
    - sort_horizon: which of them ranks the table. Defaults to the middle one.
    - include_locked: also rank players who cannot be claimed until the next deadline.

    Errors:
    - 409 when no draft league is connected, or it has never been fetched. The message carries
      the command that fixes it, because a page that renders an empty waiver table is worse than
      one that says what to run.
    - 400 for an unusable horizon, or a run too short for it. Horizons are validated before
      anything is read, so a bad request says so whether or not a league is connected.
    """
    try:
        wanted = waivers_module.parse_horizons(horizons)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    run = _resolve_run(DRAFT, run_id)
    if not horizons:
        wanted = waivers_module.default_horizons_for(run)
    try:
        ownership = draft_league.load_ownership(_context().season)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(409, str(exc)) from exc
    try:
        board = waivers_module.waiver_board(
            run=run,
            ownership=ownership,
            horizons=wanted,
            sort_horizon=sort_horizon,
            include_locked=include_locked,
            limit=limit,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    board['run'] = _run_header(run)
    board['next_gameweek'] = _context().next_gameweek
    return board


@router.get('/transfers')
def transfer_board(
    run_id: str | None = None,
    horizons: str | None = None,
    sort_horizon: int | None = None,
    free_transfers: int = transfers_module.DEFAULT_FREE_TRANSFERS,
    limit: int = waivers_module.DEFAULT_LIMIT,
) -> dict:
    """Rank affordable classic-FPL transfers by what they add to your own fifteen.

    The FPL-side counterpart of `/waivers`, and deliberately the same metric - best legal XI
    before and after the swap - narrowed by the three things the classic game adds: a budget, a
    three-per-club cap, and a 4-point hit past your free transfers. See `src/web/transfers.py`.

    Parameters:
    - horizons: up to three comma-separated gameweek counts, e.g. `1,3,5`.
    - sort_horizon: which of them ranks the table. Defaults to the middle one.
    - free_transfers: how many transfers cost nothing this week. Not published by the API, so it
      is yours to set; it moves `net`, never `gain`.

    Errors:
    - 409 when no classic team is connected, or nothing has been published for it yet. The message
      carries the command that fixes it.
    - 400 for an unusable horizon, a run too short for it, or a squad that cannot be priced.
    """
    try:
        wanted = waivers_module.parse_horizons(horizons)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    run = _resolve_run(FPL, run_id)
    if not horizons:
        wanted = waivers_module.default_horizons_for(run)
    config = fpl_squad.load_config_or_none()
    if config is None:
        raise HTTPException(409, (
            "No classic-FPL team connected, so there is nothing to price a transfer against. "
            "Connect yours with: ./run.sh -m src.fpl.squad --entry <your entry id>"
        ))
    try:
        squad = fpl_squad.load_squad(_context().season, config=config)
    except FileNotFoundError as exc:
        raise HTTPException(409, str(exc)) from exc
    try:
        board = transfers_module.transfer_board(
            run=run,
            squad=squad,
            horizons=wanted,
            sort_horizon=sort_horizon,
            free_transfers=free_transfers,
            limit=limit,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    board['run'] = _run_header(run)
    board['next_gameweek'] = _context().next_gameweek
    return board


@router.get('/calibration')
def calibration_report(game: str = QueryParam(DRAFT), run_id: str | None = None) -> dict:
    """Score a run against whatever gameweeks have actually resolved."""
    run = _resolve_run(game, run_id)
    return calibration.score_run(run, _context().histories)
