"""What a waiver claim is worth: your squad's points, before and after the swap.

The question is not the same as the draft question
--------------------------------------------------
On the draft board every pick is a free choice, so a player is worth his points against the
replacement nobody will want (`src/fpl/projection/vorp.py`). A waiver is a *swap inside a fixed
squad*: fifteen players, eleven of whom score each gameweek, and the game only lets you exchange
a player for an unsigned player **in the same position** (FPL Draft rules, "Transactions
(unsigned players)"). Two consequences run through this whole module:

- **Value is squad value, not player value.** Signing the tenth-best midfielder in the game
  helps nothing if he would sit behind your other five. So a claim is priced as
  `best legal XI after the swap` minus `best legal XI before it`, summed over the horizon. A
  bench upgrade prices at almost zero, which is the honest answer.
- **The choice is same-position.** The candidate pool for each player you hold is the free
  agents in his position, and nothing else. Cross-position moves need a trade, which this page
  does not model.

The XI is re-chosen every gameweek, because that is what you actually do at each deadline. That
is what makes a blank gameweek and a suspension cost the right amount: the player scores nothing
that week and your bench covers him, exactly as it would in the game.

Horizons
--------
A run stores per-fixture points, so any horizon up to the run's length is a sum, not a
projection - which is why three horizons cost the same as one and none of them re-runs a model.
`HORIZON_LIMIT` caps them at ten gameweeks because that is as far as the fixture-level projection
goes, and because a ten-week view of a waiver is already mostly noise.

`parse_horizons`, `span_gameweeks`, `resolve_sort_horizon`, `horizon_totals` and
`default_horizons_for` are the shared definition: `/api/board` uses them too, so the FPL board and
this page cannot drift into two meanings of "3 GW".

The same swap is scored at every requested horizon so the trade-off is visible: a claim that
wins the next gameweek and loses the next five is a decision, not a suggestion.

Assumptions, stated
-------------------
- **Points are the projection's, unchanged.** Injuries and doubts are already priced in
  `src/fpl/projection/minutes.py`; nothing here re-judges them.
- **Every claim is assumed to succeed.** Waiver priority decides who wins a contested claim, and
  this module reports your priority but does not discount by it.
- **One claim at a time.** Each row is scored against your current squad, so two rows that drop
  the same player cannot both be taken at those numbers.
"""
from __future__ import annotations

import logging

from src.fpl.loader.draft_league import LeagueOwnership


logger = logging.getLogger(__name__)


HORIZON_LIMIT = 10
"""Longest horizon in gameweeks. The run projects ten fixtures ahead; beyond that is fiction."""

MAX_HORIZONS = 3
"""Horizons shown side by side. Three is what fits a table row and a decision."""

DEFAULT_HORIZONS = (1, 3, 5)

SQUAD_SHAPE = {'GKP': 2, 'DEF': 5, 'MID': 5, 'FWD': 3}
"""A draft squad, always. Waivers are like-for-like, so this never drifts mid-season."""

XI_SIZE = 11
XI_MIN = {'GKP': 1, 'DEF': 3, 'MID': 0, 'FWD': 1}
"""Formation rules: one keeper, at least three defenders, at least one forward."""

DEFAULT_LIMIT = 60


MINE = 'mine'
RIVAL = 'rival'
FREE = 'free'
LOCKED = 'locked'
UNKNOWN = 'unknown'
"""How a player relates to your squad. `LOCKED` is unowned but not claimable this deadline, and
`UNKNOWN` means the ownership snapshot has no row for him at all - older than the run, usually."""


def owner_kind(ownership: LeagueOwnership, element: int) -> str:
    """Classify one player: yours, a rival's, free, locked, or not in the ownership map."""
    if element not in ownership.owner_by_element:
        return UNKNOWN
    owner = ownership.owner_by_element[element]
    if owner is None:
        return FREE if ownership.is_claimable(element) else LOCKED
    return MINE if owner == ownership.my_entry.entry_id else RIVAL


def parse_horizons(raw: str | None) -> tuple[int, ...]:
    """Read `1,3,5` into a validated, ordered tuple of horizons.

    Raises:
    - ValueError: on anything unusable, naming the rule broken. A horizon silently clamped to 10
      would answer a different question than the one asked.
    """
    if not raw:
        return DEFAULT_HORIZONS
    parts = [part.strip() for part in raw.split(',') if part.strip()]
    try:
        horizons = [int(part) for part in parts]
    except ValueError as exc:
        raise ValueError(f"Horizons must be whole numbers of gameweeks, got '{raw}'.") from exc
    if not horizons:
        raise ValueError("No horizons given.")
    if len(horizons) > MAX_HORIZONS:
        raise ValueError(
            f"At most {MAX_HORIZONS} horizons can be compared at once, got {len(horizons)}."
        )
    for horizon in horizons:
        if horizon < 1 or horizon > HORIZON_LIMIT:
            raise ValueError(
                f"Horizon {horizon} is outside 1-{HORIZON_LIMIT} gameweeks. The projection only "
                f"reaches {HORIZON_LIMIT} fixtures ahead, so a longer one would be invented."
            )
    if len(set(horizons)) != len(horizons):
        raise ValueError(f"Horizons must be distinct, got {horizons}.")
    return tuple(sorted(horizons))


def points_by_gameweek(row: dict) -> dict[int, float]:
    """One run row's projected points per gameweek, doubles summed and blanks absent."""
    totals: dict[int, float] = {}
    for fixture in row['fixtures']:
        gameweek = fixture['gameweek']
        totals[gameweek] = totals.get(gameweek, 0.0) + fixture['points']
    return totals


def horizon_gameweeks(gameweek_from: int, horizon: int) -> list[int]:
    return list(range(gameweek_from, gameweek_from + horizon))


def default_horizons_for(run: dict) -> tuple[int, ...]:
    """`DEFAULT_HORIZONS`, minus any the run is too short to answer.

    A caller that names its horizons gets them checked and refused if they do not fit; a caller
    that names none is not asking for anything, and a five-gameweek default must not turn a
    three-gameweek run into an error page. Nothing is invented: the horizons that survive are the
    ones the run has fixtures for.
    """
    length = run['gameweek_to'] - run['gameweek_from'] + 1
    fitting = tuple(horizon for horizon in DEFAULT_HORIZONS if horizon <= length)
    return fitting or (length,)


def span_gameweeks(run: dict, horizons: tuple[int, ...]) -> list[int]:
    """Every gameweek the longest requested horizon covers, checked against the run.

    Shared by the waiver table and the boards, so "3 GW" cannot come to mean two different
    windows on two pages.

    Raises:
    - ValueError: when the run stops short of the longest horizon, naming the command that
      regenerates it. A horizon silently truncated to the run would print a five-gameweek total
      that covers three.
    """
    longest = max(horizons)
    gameweek_from, gameweek_to = run['gameweek_from'], run['gameweek_to']
    if gameweek_from + longest - 1 > gameweek_to:
        raise ValueError(
            f"A {longest}-gameweek horizon from GW{gameweek_from} needs projections up to "
            f"GW{gameweek_from + longest - 1}, but this run stops at GW{gameweek_to}. "
            f"Regenerate it: ./run.sh -m src.fpl.project --game {run['game']} "
            f"--gw-from {gameweek_from} --gw-to {gameweek_from + longest - 1}"
        )
    return horizon_gameweeks(gameweek_from, longest)


def resolve_sort_horizon(horizons: tuple[int, ...], sort_horizon: int | None) -> int:
    """Which horizon decides the ranking. Defaults to the middle one requested.

    Raises:
    - ValueError: when it is not one of the requested horizons. Ranking by a horizon whose column
      is not on screen is a table nobody can check.
    """
    if sort_horizon is None:
        return horizons[len(horizons) // 2]
    if sort_horizon not in horizons:
        raise ValueError(
            f"Sort horizon {sort_horizon} is not one of the requested {list(horizons)}."
        )
    return sort_horizon


def horizon_totals(row: dict, gameweeks: list[int], horizons: tuple[int, ...]) -> dict:
    """One run row's projected points, and its blank gameweeks, at each horizon.

    `gameweeks` is the span from `span_gameweeks`, so `gameweeks[:horizon]` is that horizon's
    window. Blanks are counted rather than skipped: a gameweek his club does not play is a real
    zero, and on a board it is the difference between a low projection and a missing fixture.
    """
    per_gameweek = points_by_gameweek(row)
    return {
        'points': {
            horizon: round(sum(per_gameweek.get(gameweek, 0.0)
                               for gameweek in gameweeks[:horizon]), 2)
            for horizon in horizons
        },
        'blanks': {
            horizon: sum(1 for gameweek in gameweeks[:horizon] if gameweek not in per_gameweek)
            for horizon in horizons
        },
    }


def _prefix(points: list[float]) -> list[float]:
    """Cumulative sums of a descending list: `prefix[k]` is the best k of them."""
    running = 0.0
    sums = [0.0]
    for value in points:
        running += value
        sums.append(running)
    return sums


def _xi_points(prefix: dict[str, list[float]]) -> tuple[float, dict[str, int]]:
    """Best legal XI from one squad in one gameweek, given per-position prefix sums.

    Returns the points and how many of each position started, which is what turns a total back
    into "your fifth defender started two of these five gameweeks".
    """
    best = float('-inf')
    shape: dict[str, int] = {}
    keeper = prefix['GKP'][1]
    max_def = len(prefix['DEF']) - 1
    max_mid = len(prefix['MID']) - 1
    max_fwd = len(prefix['FWD']) - 1
    for defenders in range(XI_MIN['DEF'], max_def + 1):
        for forwards in range(XI_MIN['FWD'], max_fwd + 1):
            midfielders = XI_SIZE - 1 - defenders - forwards
            if midfielders < XI_MIN['MID'] or midfielders > max_mid:
                continue
            total = (
                keeper
                + prefix['DEF'][defenders]
                + prefix['MID'][midfielders]
                + prefix['FWD'][forwards]
            )
            if total > best:
                best = total
                shape = {'GKP': 1, 'DEF': defenders, 'MID': midfielders, 'FWD': forwards}
    if not shape:
        raise ValueError(
            "No legal XI can be built from this squad. Expected "
            f"{SQUAD_SHAPE} and the formation rules to be satisfiable."
        )
    return best, shape


class SquadValuation:
    """A fifteen-player squad, valued gameweek by gameweek and swap by swap.

    Built once per request. Holds each player's per-gameweek points and the per-gameweek prefix
    sums of the untouched squad, so scoring one swap only rebuilds the position it touches.
    """

    def __init__(self, rows: list[dict], gameweeks: list[int]):
        """
        Parameters:
        - rows: run rows for the fifteen players in the squad.
        - gameweeks: the horizon, longest first requested horizon included.

        Raises:
        - ValueError: if the squad is not 2/5/5/3. Every draft squad is, so a mismatch means the
          ownership map and the run disagree about somebody's position and the whole valuation
          would be quietly wrong.
        """
        self.gameweeks = gameweeks
        self.rows = {row['player_id']: row for row in rows}
        self.points = {row['player_id']: points_by_gameweek(row) for row in rows}
        self.by_position: dict[str, list[int]] = {position: [] for position in SQUAD_SHAPE}
        for row in rows:
            if row['position'] not in self.by_position:
                raise ValueError(f"{row['web_name']} has position '{row['position']}', which is not a squad position.")
            self.by_position[row['position']].append(row['player_id'])
        shape = {position: len(ids) for position, ids in self.by_position.items()}
        if shape != SQUAD_SHAPE:
            raise ValueError(
                f"Squad is {shape}, expected {SQUAD_SHAPE}. A draft squad always holds that "
                f"shape, so this means the ownership snapshot and the run disagree - re-fetch "
                f"ownership with ./run.sh -m src.fpl.league --refresh and regenerate the run."
            )
        self._base_prefix = {
            gameweek: {
                position: _prefix(sorted(
                    (self.points[player_id].get(gameweek, 0.0) for player_id in ids),
                    reverse=True,
                ))
                for position, ids in self.by_position.items()
            }
            for gameweek in gameweeks
        }
        self._base_per_gameweek: dict[int, float] = {}
        self._base_shape: dict[int, dict[str, int]] = {}
        for gameweek, prefix in self._base_prefix.items():
            points, shape = _xi_points(prefix)
            self._base_per_gameweek[gameweek] = points
            self._base_shape[gameweek] = shape
        self._starters: dict[int, set[int]] = {
            gameweek: {
                player_id
                for position, ids in self.by_position.items()
                for player_id in sorted(
                    ids, key=lambda other: -self.points[other].get(gameweek, 0.0)
                )[:self._base_shape[gameweek][position]]
            }
            for gameweek in gameweeks
        }

    def baseline(self, horizon: int) -> float:
        """Points the squad as it stands scores over the first `horizon` gameweeks."""
        return sum(self._base_per_gameweek[gameweek] for gameweek in self.gameweeks[:horizon])

    def starts(self, player_id: int, horizon: int) -> int:
        """Gameweeks out of `horizon` in which this player makes the best XI."""
        return sum(
            1 for gameweek in self.gameweeks[:horizon] if player_id in self._starters[gameweek]
        )

    def swap_gain(self, out_id: int, candidate: dict, horizons: tuple[int, ...]) -> dict[int, float]:
        """Points gained per horizon by signing `candidate` and dropping `out_id`.

        Same position only - the game allows nothing else - so only that position's prefix sums
        are rebuilt, per gameweek.
        """
        position = self.rows[out_id]['position']
        kept = [player_id for player_id in self.by_position[position] if player_id != out_id]
        incoming = points_by_gameweek(candidate)
        gains: dict[int, float] = {}
        running = 0.0
        longest = max(horizons)
        wanted = set(horizons)
        for index, gameweek in enumerate(self.gameweeks[:longest], start=1):
            values = [self.points[player_id].get(gameweek, 0.0) for player_id in kept]
            values.append(incoming.get(gameweek, 0.0))
            values.sort(reverse=True)
            prefix = dict(self._base_prefix[gameweek])
            prefix[position] = _prefix(values)
            after, _ = _xi_points(prefix)
            running += after - self._base_per_gameweek[gameweek]
            if index in wanted:
                gains[index] = round(running, 3)
        return gains


def _slim_player(row: dict, valuation: SquadValuation | None, horizons: tuple[int, ...],
                 gameweeks: list[int]) -> dict:
    """The columns a waiver table needs about one player."""
    totals = horizon_totals(row, gameweeks, horizons)
    slim = {
        'player_id': row['player_id'],
        'web_name': row['web_name'],
        'team': row['team'],
        'position': row['position'],
        'p_start': row['inputs']['minutes']['p_start'],
        'status': row['inputs']['minutes']['status'],
        'status_meaning': row['inputs']['minutes']['status_meaning'],
        'flags': row['flags'],
        'points': totals['points'],
        'blanks': totals['blanks'],
    }
    if valuation is not None:
        slim['starts'] = {horizon: valuation.starts(row['player_id'], horizon) for horizon in horizons}
    return slim


def waiver_board(
    run: dict,
    ownership: LeagueOwnership,
    horizons: tuple[int, ...] = DEFAULT_HORIZONS,
    sort_horizon: int | None = None,
    include_locked: bool = False,
    limit: int = DEFAULT_LIMIT,
) -> dict:
    """Rank every claimable free agent by what he would add to your squad.

    Parameters:
    - run: a stored run artifact, read as-is. Its `gameweek_from` is the start of every horizon.
    - ownership: who holds whom, from `src/fpl/loader/draft_league.py`.
    - horizons: up to three, each 1-`HORIZON_LIMIT` gameweeks.
    - sort_horizon: the horizon rows are ranked by. Defaults to the middle one requested.
    - include_locked: also rank players locked until the next deadline, marked as such. They
      cannot be claimed now, so they are excluded by default.
    - limit: *claim* rows returned. Everything dropped is counted in the returned `considered`
      block. Rows tie-break on the candidate's own points, so the part of the pool that changes
      nothing is at least ordered by who is worth watching. The `players` list is never truncated:
      it is there to be browsed, and a silently short board is the failure this repo exists to
      avoid.

    Returns a dict with the squad valuation, the ranked candidates, the whole projected pool
    under `players` (every player, with his points at each horizon and who owns him - the browsing
    view, no swap arithmetic), and what was left out.

    Raises:
    - ValueError: if a horizon runs past the run, if the squad is not 2/5/5/3, or if a player we
      own has no row in the run. All three would otherwise produce a table that looks fine.
    """
    gameweek_from = run['gameweek_from']
    gameweeks = span_gameweeks(run, horizons)
    sort_horizon = resolve_sort_horizon(horizons, sort_horizon)
    by_id = {row['player_id']: row for row in run['players']}

    my_squad = sorted(ownership.my_squad)
    missing = [player_id for player_id in my_squad if player_id not in by_id]
    if missing:
        raise ValueError(
            f"{len(missing)} player(s) in your squad have no projection in run "
            f"{run['run_id']} (element ids {missing}). Regenerate the run so the whole squad can "
            f"be valued: ./run.sh -m src.fpl.project --game {run['game']}"
        )
    valuation = SquadValuation([by_id[player_id] for player_id in my_squad], gameweeks)

    unprojected = 0
    pool: list[dict] = []
    locked_count = 0
    for element, owner in ownership.owner_by_element.items():
        if owner is not None:
            continue
        claimable = ownership.is_claimable(element)
        if not claimable:
            locked_count += 1
            if not include_locked:
                continue
        row = by_id.get(element)
        if row is None:
            unprojected += 1
            continue
        pool.append({'row': row, 'claimable': claimable})
    if unprojected:
        logger.info(
            "%d unowned element(s) have no row in run %s and cannot be ranked. They are "
            "registered in the game but were not projected - usually a player added since the "
            "run was generated.", unprojected, run['run_id'],
        )

    candidates = []
    for entry in pool:
        row = entry['row']
        position = row['position']
        best_out = None
        best_gains: dict[int, float] = {}
        for out_id in valuation.by_position[position]:
            gains = valuation.swap_gain(out_id, row, horizons)
            if best_out is None or gains[sort_horizon] > best_gains[sort_horizon]:
                best_out, best_gains = out_id, gains
        candidate = _slim_player(row, None, horizons, gameweeks)
        candidate['claimable'] = entry['claimable']
        candidate['ownership'] = ownership.owner_label(row['player_id'])
        candidate['out'] = _slim_player(by_id[best_out], valuation, horizons, gameweeks)
        candidate['gain'] = best_gains
        candidates.append(candidate)

    candidates.sort(key=lambda row: (-row['gain'][sort_horizon], -row['points'][sort_horizon]))
    positive = [row for row in candidates if row['gain'][sort_horizon] > 0]
    shown = candidates[:limit]
    for index, row in enumerate(shown, start=1):
        row['rank'] = index

    unknown_owner = 0
    players = []
    for row in run['players']:
        kind = owner_kind(ownership, row['player_id'])
        if kind == UNKNOWN:
            unknown_owner += 1
        in_squad = row['player_id'] in valuation.rows
        listed = _slim_player(row, valuation if in_squad else None, horizons, gameweeks)
        listed['owner_kind'] = kind
        listed['ownership'] = ownership.owner_label(row['player_id'])
        players.append(listed)
    players.sort(key=lambda listed: -listed['points'][sort_horizon])
    if unknown_owner:
        logger.info(
            "%d projected player(s) are absent from the ownership snapshot and are listed as "
            "owner unknown. Refresh it: ./run.sh -m src.fpl.league --refresh", unknown_owner,
        )

    return {
        'gameweek_from': gameweek_from,
        'gameweeks': gameweeks,
        'horizons': list(horizons),
        'sort_horizon': sort_horizon,
        'league': ownership.as_dict(),
        'squad': {
            'points': {horizon: round(valuation.baseline(horizon), 2) for horizon in horizons},
            'players': [
                _slim_player(by_id[player_id], valuation, horizons, gameweeks)
                for player_id in sorted(
                    my_squad,
                    key=lambda player_id: (
                        list(SQUAD_SHAPE).index(by_id[player_id]['position']),
                        -sum(points_by_gameweek(by_id[player_id]).get(gameweek, 0.0)
                             for gameweek in gameweeks[:sort_horizon]),
                    ),
                )
            ],
        },
        'candidates': shown,
        'players': players,
        'considered': {
            'unknown_owner': unknown_owner,
            'pool': len(pool),
            'ranked': len(candidates),
            'improving': len(positive),
            'shown': len(shown),
            'locked': locked_count,
            'unprojected': unprojected,
            'include_locked': include_locked,
        },
    }
