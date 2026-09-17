"""What a classic-FPL transfer is worth, priced against your own fifteen.

Why this is not the waivers screen
----------------------------------
`src/web/waivers.py` answers the draft question: of the players nobody owns, who most improves my
squad? The metric there - re-pick the best legal XI before and after the swap, sum the difference
over the horizon - is exactly right here too, and is reused rather than reimplemented.

What is different is everything around it, and all three differences make the classic answer
*narrower*:

- **Money, not ownership.** A draft free agent is free. A classic signing must fit
  `selling price of the man you drop + whatever is in the bank`, which with an empty bank means
  every "upgrade" is strictly sideways.
- **Three per club.** A draft league has no such rule. Owning three City players bars a fourth
  however good he is, so the cap is applied to the squad *after* the outgoing player leaves - the
  fourth City player is legal if the third is the one being sold.
- **A transfer past your free allowance costs 4 points.** The waivers screen has no equivalent, and
  it changes conclusions rather than decorating them: most swaps this module finds are worth less
  than a hit, so `net` is reported beside `gain` and is the number to decide on.

What is the same is the part people get wrong: value is *squad* value. A swap that upgrades a
player who never makes your XI is worth about zero, and the best player to drop is often not the
one the signing displaces, because the XI reshapes around a good buy. Anything scoring a transfer
as `new player's points - old player's points` has skipped the only part that matters.

Assumptions, stated
-------------------
- **Selling price is approximated by current price.** FPL publishes purchase and selling prices
  only on the authenticated `my-team` endpoint; the public API this repo reads does not carry
  them. A player who has risen since you bought him sells for less than he now costs (the game
  keeps half the rise), so this *overstates* your budget by up to £0.1-0.2 per player. It cannot
  rescue a marginal swap, but it can put a just-affordable one on the list that is not. Every
  response says so in `budget.selling_price_basis`, and the screen repeats it.
- **Free transfers are an input, not a fact.** The count is not public either. It defaults to 1
  and the caller may set it; `net` is `gain` minus 4 points for each transfer past it.
- **One transfer at a time.** Each row is scored against your current squad, so two rows that drop
  the same player cannot both be taken at those numbers, and a pair of transfers is not the sum of
  two rows.
- **Points are the projection's, unchanged.** Injuries and doubts are already priced in
  `src/fpl/projection/minutes.py`; nothing here re-judges them. A player flagged `doubtful` is
  therefore a legitimate row - his projection already carries the doubt.
- **No captaincy, no auto-subs.** The valuation is the best legal XI each week, undoubled. Who you
  captain in three gameweeks is not knowable, and doubling one player would flatter every swap
  involving him.

The bank is never guessed
-------------------------
`FplSquad.bank` is `None` when the stored payload carried no `entry_history`. That is not treated
as £0.0: zero is itself a meaningful budget, and the tightest one, so a missing bank read as zero
would silently rule out every upgrade and look like a considered answer. `transfer_board` raises
instead, naming the refresh that fixes it.
"""
from __future__ import annotations

import logging

from src.fpl.loader.fpl_squad import FplSquad
from src.web.waivers import (
    DEFAULT_LIMIT,
    SQUAD_SHAPE,
    SquadValuation,
    horizon_totals,
    resolve_sort_horizon,
    span_gameweeks,
)


logger = logging.getLogger(__name__)


HIT_COST = 4.0
"""Points deducted for each transfer beyond the free allowance."""

MAX_PER_CLUB = 3
"""Classic-FPL squad rule. A draft league has no equivalent, which is why this lives here."""

DEFAULT_FREE_TRANSFERS = 1
"""What a manager has in a normal week. Not published, so it is an input - see the module doc."""

SELLING_PRICE_BASIS = 'current_price'
"""How `out.price` was obtained. Named in every response so the approximation travels with it."""


def _club_counts(rows: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        counts[row['team']] = counts.get(row['team'], 0) + 1
    return counts


def net_after_hits(gain: float, transfers: int, free_transfers: int) -> float:
    """`gain` minus the points a hit costs, for `transfers` made with `free_transfers` in hand."""
    return gain - HIT_COST * max(0, transfers - free_transfers)


def transfer_board(
    run: dict,
    squad: FplSquad,
    horizons: tuple[int, ...],
    sort_horizon: int | None = None,
    free_transfers: int = DEFAULT_FREE_TRANSFERS,
    limit: int = DEFAULT_LIMIT,
) -> dict:
    """Rank every affordable, legal single transfer by what it adds to your fifteen.

    Parameters:
    - run: a stored run artifact, read as-is. Its `gameweek_from` starts every horizon.
    - squad: our fifteen, from `src/fpl/loader/fpl_squad.py`. Must carry a bank.
    - horizons: up to three gameweek counts, each fitting inside the run.
    - sort_horizon: which one ranks the table. Defaults to the middle one requested.
    - free_transfers: how many transfers cost nothing. Drives `net`, not `gain`.
    - limit: swap rows returned. Everything dropped is counted in `considered`.

    Returns the squad valuation, the ranked swaps, and what was excluded and why. A swap whose
    gain is zero or negative is still counted but not shown: the question is what to *do*.

    Raises:
    - ValueError: when the squad has no bank (see the module doc), when a horizon runs past the
      run, when the squad is not 2/5/5/3, or when a player we own has no row in the run. Each of
      those would otherwise produce a table that looks fine and is wrong.
    """
    if squad.bank is None:
        raise ValueError(
            f"The stored GW{squad.gameweek} picks for entry {squad.entry_id} carry no bank, so no "
            f"transfer can be priced. Re-fetch them with: ./run.sh -m src.fpl.squad --refresh"
        )
    if free_transfers < 0:
        raise ValueError(f"free_transfers is {free_transfers}; it cannot be negative.")

    gameweeks = span_gameweeks(run, horizons)
    sort_horizon = resolve_sort_horizon(horizons, sort_horizon)
    by_id = {row['player_id']: row for row in run['players']}

    owned = [pick.element for pick in squad.picks]
    missing = [element for element in owned if element not in by_id]
    if missing:
        raise ValueError(
            f"{len(missing)} player(s) in your squad have no projection in run {run['run_id']} "
            f"(element ids {missing}). Regenerate the run so the whole squad can be valued: "
            f"./run.sh -m src.fpl.project --game {run['game']}"
        )
    squad_rows = [by_id[element] for element in owned]
    valuation = SquadValuation(squad_rows, gameweeks)

    considered = {
        'pool': sum(1 for row in run['players'] if row['player_id'] not in set(owned)),
        'pairs': 0,
        'owned': len(owned),
        'unaffordable': 0,
        'club_limit': 0,
        'scored': 0,
        'improving': 0,
        'shown': 0,
    }
    """`pool` and `scored` count players; `pairs`, `unaffordable` and `club_limit` count
    (player, man-you-sell) *pairs*, of which there are up to five per player at one position.
    Naming them apart matters: "511 unaffordable" would read as half the league if it were
    players. `scored` is how many had at least one legal sale and were priced."""
    owned_ids = set(owned)
    by_position: dict[str, list[dict]] = {}
    for row in squad_rows:
        by_position.setdefault(row['position'], []).append(row)
    # Club counts after each possible sale, computed once: the fourth City player is legal only
    # when the man leaving is one of the three already there.
    clubs_without = {
        row['player_id']: _club_counts([other for other in squad_rows
                                        if other['player_id'] != row['player_id']])
        for row in squad_rows
    }

    candidates: list[dict] = []
    for row in run['players']:
        if row['player_id'] in owned_ids:
            continue
        legal = []
        for out_row in by_position.get(row['position'], []):
            considered['pairs'] += 1
            if row['price'] > round(out_row['price'] + squad.bank, 1) + 1e-9:
                considered['unaffordable'] += 1
                continue
            if clubs_without[out_row['player_id']].get(row['team'], 0) >= MAX_PER_CLUB:
                considered['club_limit'] += 1
                continue
            legal.append(out_row)
        if not legal:
            continue
        considered['scored'] += 1
        # One row per player to buy, paired with the sale that gains most at the deciding horizon.
        # The other horizons then score that same pair rather than each finding its own best sale,
        # so a row reads as one decision.
        best_out, best_gain = None, None
        for out_row in legal:
            gain = valuation.swap_gain(out_row['player_id'], row, horizons)
            if best_gain is None or gain[sort_horizon] > best_gain[sort_horizon]:
                best_out, best_gain = out_row, gain
        if best_gain[sort_horizon] <= 0:
            continue
        considered['improving'] += 1
        budget = round(best_out['price'] + squad.bank, 1)
        candidates.append({
            'player_id': row['player_id'],
            'web_name': row['web_name'],
            'team': row['team'],
            'position': row['position'],
            'price': row['price'],
            'ownership': row.get('ownership'),
            'p_start': row['inputs']['minutes']['p_start'],
            'status': row['inputs']['minutes']['status'],
            'status_meaning': row['inputs']['minutes']['status_meaning'],
            'flags': row['flags'],
            'points': horizon_totals(row, gameweeks, horizons)['points'],
            'gain': best_gain,
            'net': {horizon: round(net_after_hits(points, 1, free_transfers), 3)
                    for horizon, points in best_gain.items()},
            'spare': round(budget - row['price'], 1),
            'legal_sales': len(legal),
            'out': {
                'player_id': best_out['player_id'],
                'web_name': best_out['web_name'],
                'team': best_out['team'],
                'position': best_out['position'],
                'price': best_out['price'],
                'starts': {horizon: valuation.starts(best_out['player_id'], horizon)
                           for horizon in horizons},
                'points': horizon_totals(best_out, gameweeks, horizons)['points'],
            },
        })

    candidates.sort(key=lambda row: (-row['gain'][sort_horizon], -row['points'][sort_horizon]))
    for rank, row in enumerate(candidates, start=1):
        row['rank'] = rank
    shown = candidates[:limit]
    considered['shown'] = len(shown)
    if considered['improving'] > len(shown):
        logger.info(
            "%d improving transfers found, showing the best %d by the %d-gameweek horizon.",
            considered['improving'], len(shown), sort_horizon,
        )

    return {
        'gameweek_from': run['gameweek_from'],
        'gameweeks': gameweeks,
        'horizons': list(horizons),
        'sort_horizon': sort_horizon,
        'free_transfers': free_transfers,
        'hit_cost': HIT_COST,
        'budget': {
            'bank': squad.bank,
            'squad_value': squad.squad_value,
            'selling_price_basis': SELLING_PRICE_BASIS,
            'note': (
                "Selling prices are approximated by current prices: the public API does not "
                "publish what you paid. A player who has risen sells for less than he costs, so "
                "the budget shown is an upper bound."
            ),
        },
        'squad': {
            'entry_id': squad.entry_id,
            'entry_name': squad.entry_name,
            'manager': squad.manager,
            'gameweek': squad.gameweek,
            'captured_at': squad.captured_at,
            'active_chip': squad.active_chip,
            'shape': SQUAD_SHAPE,
            'points': {horizon: round(valuation.baseline(horizon), 2) for horizon in horizons},
            'players': [
                {
                    'player_id': row['player_id'],
                    'web_name': row['web_name'],
                    'team': row['team'],
                    'position': row['position'],
                    'price': row['price'],
                    'flags': row['flags'],
                    'status_meaning': row['inputs']['minutes']['status_meaning'],
                    'p_start': row['inputs']['minutes']['p_start'],
                    'points': horizon_totals(row, gameweeks, horizons)['points'],
                    'starts': {
                        horizon: valuation.starts(row['player_id'], horizon)
                        for horizon in horizons
                    },
                }
                for row in squad_rows
            ],
        },
        'candidates': shown,
        'considered': considered,
    }
