"""What a pick costs you, as opposed to what a player is worth.

Two different questions
-----------------------
`src/fpl/projection/vorp.py` answers *is this player valuable*: his points against the free
player who will still be there at the end of the draft. That is the right way to compare a
forward with a midfielder, and it is deliberately stable - it barely moves when a top player is
taken, because taking a top player removes one from the pool and one from the picks still to
come, leaving the last man standing unchanged.

This module answers the other question, the one you actually face on the clock: *if I do not
take him now, what do I lose?* That depends on the players immediately below him and on how long
until your next pick, not on the replacement line.

- `next_best_drop` - the gap to the next player at the same position. A big gap means taking
  him is nearly free of regret at other positions; a small one means the position can wait.
- `wait_costs` - what the best available player at each position is worth now, against what the
  best available will be worth when your turn comes round again. This is the number that says
  "take a forward" or "midfield can wait a round".

The waiting simulation
----------------------
`wait_costs` assumes the picks between now and your next turn go to the highest VORP still on
the board. That is an assumption about other managers, and it is optimistic about their
discipline - a real league reaches for a favourite. It is stated rather than hidden, and the
number it produces is a floor on the cost of waiting: if they draft worse than the board, the
player you want is likelier, not less likely, to survive.

Both functions are pure arithmetic over stored run rows, so they run per request without
re-projecting anything.
"""
from __future__ import annotations


def picks_between_turns(managers: int) -> int:
    """Players taken between two of your picks in a snake draft.

    In a snake the gap alternates - a manager picking early in one round picks late in the next -
    but it averages `2 * (managers - 1)` and that is what the default uses. The exact number is
    the caller's to override, because only you know where you sit in the order.
    """
    if managers < 1:
        raise ValueError(f"managers must be at least 1, got {managers}")
    return 2 * (managers - 1)


def next_best_drop(rows: list[dict]) -> dict[int, float]:
    """Points each available player has over the next available player in his position.

    Parameters:
    - rows: board rows, each with `player_id`, `position`, `points`, and optionally `owner`.
      Rows with an owner are skipped: a drafted player is not a choice.

    Returns:
    - player id -> points gap to the next available player at that position. The last available
      player at a position gets his own points, since the alternative there is nobody.
    """
    drops: dict[int, float] = {}
    by_position: dict[str, list[dict]] = {}
    for row in rows:
        if row.get('owner'):
            continue
        by_position.setdefault(row['position'], []).append(row)

    for pool in by_position.values():
        pool.sort(key=lambda row: -row['points'])
        for index, row in enumerate(pool):
            following = pool[index + 1]['points'] if index + 1 < len(pool) else 0.0
            drops[row['player_id']] = round(row['points'] - following, 3)
    return drops


def wait_costs(rows: list[dict], picks_until_next_turn: int) -> dict[str, dict]:
    """What each position costs you if you spend this pick elsewhere.

    Parameters:
    - rows: board rows with `player_id`, `web_name`, `position`, `points`, `vorp`, and optionally
      `owner`.
    - picks_until_next_turn: how many players will be taken before you pick again.

    Returns:
    - Position -> `{best, best_name, then, then_name, cost, survives}`. `best` is the top
      available player's points now, `then` the top available when you are back, `cost` the
      difference, and `survives` whether the player you would take now is expected to last.

    Raises:
    - ValueError: for a negative pick count, which would mean simulating backwards.
    """
    if picks_until_next_turn < 0:
        raise ValueError(
            f"picks_until_next_turn must be zero or more, got {picks_until_next_turn}"
        )
    available = [row for row in rows if not row.get('owner')]
    # The assumption, in one line: everyone else drafts the board.
    expected_gone = {
        row['player_id']
        for row in sorted(available, key=lambda row: -row['vorp'])[:picks_until_next_turn]
    }

    costs: dict[str, dict] = {}
    positions = {row['position'] for row in available}
    for position in positions:
        pool = sorted(
            (row for row in available if row['position'] == position),
            key=lambda row: -row['points'],
        )
        surviving = [row for row in pool if row['player_id'] not in expected_gone]
        best = pool[0]
        then = surviving[0] if surviving else None
        costs[position] = {
            'best': round(best['points'], 2),
            'best_name': best['web_name'],
            'then': round(then['points'], 2) if then else None,
            'then_name': then['web_name'] if then else None,
            'cost': round(best['points'] - then['points'], 2) if then else None,
            'survives': bool(then and then['player_id'] == best['player_id']),
            'pool_size': len(pool),
        }
    return costs
