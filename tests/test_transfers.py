"""Classic-FPL transfer valuation: what a swap adds to your own fifteen.

The metric is the waivers one and is tested there. What is pinned here is everything the classic
game adds on top, because each of the three is a way to put an illegal or worthless transfer on a
board that looks right: a budget, a three-per-club cap, and the 4-point hit.

The load-bearing pair is "upgrading a starter is worth the difference" against "upgrading a player
who never starts is worth nothing" - the same property the waivers tests protect, restated here
because the affordability filter could otherwise quietly reduce the board to bench swaps.

Everything runs on hand-built rows. Nothing reads `data/`.
"""
import pytest

from src.fpl.loader.fpl_squad import FplSquad, SquadPick
from src.web import transfers


HORIZONS = (1, 3)
GAMEWEEKS = [4, 5, 6]

SHAPE = (
    ['GKP'] * 2 + ['DEF'] * 5 + ['MID'] * 5 + ['FWD'] * 3
)


def make_row(player_id, position, points, price=5.0, team='ZZZ', name=None):
    """One run row. `points` is per-gameweek over GAMEWEEKS."""
    return {
        'player_id': player_id,
        'web_name': name or f'p{player_id}',
        'team': team,
        'position': position,
        'price': price,
        'ownership': 1.0,
        'flags': [],
        'inputs': {'minutes': {'p_start': 0.9, 'status': 'a', 'status_meaning': 'available'}},
        'fixtures': [
            {'gameweek': gameweek, 'points': value}
            for gameweek, value in zip(GAMEWEEKS, points)
        ],
    }


def make_squad(elements, bank=0.0, gameweek=3):
    return FplSquad(
        entry_id=1, entry_name='T', manager='M', season='2026-2027', gameweek=gameweek,
        captured_at='2026-09-11T00:00:00', active_chip=None, bank=bank, squad_value=100.0,
        picks=tuple(
            SquadPick(element=element, position=position, slot=slot,
                      is_captain=False, is_vice_captain=False, multiplier=1)
            for slot, (element, position) in enumerate(zip(elements, SHAPE), start=1)
        ),
    )


@pytest.fixture
def world():
    """A squad of fifteen plus a pool, all at 5.0 unless a test says otherwise.

    The squad is deliberately graded: within each position the earlier ids score more, so which
    players make the XI - and therefore which upgrades matter - is predictable.
    """
    # A distinct club each, so the three-per-club cap is inert unless a test arranges for it.
    rows = []
    for index, position in enumerate(SHAPE, start=1):
        rows.append(make_row(index, position, [6.0 - index * 0.2] * 3, team=f'C{index:02d}'))
    squad_rows = list(rows)
    run = {
        'run_id': 'r1', 'game': 'fpl', 'gameweek_from': 4, 'gameweek_to': 13,
        'players': squad_rows,
    }
    return run, squad_rows


def board_for(run, squad, **kwargs):
    return transfers.transfer_board(run, squad, HORIZONS, **kwargs)


def test_upgrading_a_starter_is_worth_the_difference(world):
    run, rows = world
    # p3 is the best defender and certainly starts; a defender two points better per week.
    run['players'] = rows + [make_row(99, 'DEF', [8.0, 8.0, 8.0])]
    squad = make_squad([row['player_id'] for row in rows])
    board = board_for(run, squad)
    top = board['candidates'][0]
    assert top['player_id'] == 99
    assert top['gain'][1] > 1.0


def test_upgrading_a_player_who_never_starts_is_worth_nothing(world):
    """The fifth forward of a squad that starts two cannot improve the XI by being slightly better.

    This is the property the whole module exists for: a transfer is squad value, not player value.
    """
    run, rows = world
    bench_level = 6.0 - 15 * 0.2
    run['players'] = rows + [make_row(99, 'FWD', [bench_level + 0.05] * 3)]
    squad = make_squad([row['player_id'] for row in rows])
    board = board_for(run, squad)
    gains = {row['player_id']: row['gain'][1] for row in board['candidates']}
    # He is 0.05 better than the man he replaces and neither of them starts, so the XI is
    # unchanged and the gain is zero - not 0.05.
    assert gains.get(99, 0.0) == 0.0


def test_an_unaffordable_upgrade_is_excluded_and_counted(world):
    run, rows = world
    run['players'] = rows + [make_row(99, 'DEF', [9.0] * 3, price=12.0)]
    squad = make_squad([row['player_id'] for row in rows], bank=0.0)
    board = board_for(run, squad)
    assert 99 not in {row['player_id'] for row in board['candidates']}
    assert board['considered']['unaffordable'] == 5  # one per defender he could have replaced


def test_the_bank_pays_for_an_upgrade(world):
    """The same player, the same squad - only the bank differs. That must decide it."""
    run, rows = world
    run['players'] = rows + [make_row(99, 'DEF', [9.0] * 3, price=7.0)]
    elements = [row['player_id'] for row in rows]
    broke = board_for(run, make_squad(elements, bank=0.0))
    funded = board_for(run, make_squad(elements, bank=2.0))
    assert 99 not in {row['player_id'] for row in broke['candidates']}
    assert 99 in {row['player_id'] for row in funded['candidates']}


def test_three_per_club_bars_a_fourth(world):
    run, rows = world
    for row in [r for r in rows if r['position'] == 'MID'][:3]:
        row['team'] = 'BBB'
    run['players'] = rows + [make_row(99, 'MID', [9.0] * 3, team='BBB')]
    squad = make_squad([row['player_id'] for row in rows])
    board = board_for(run, squad)
    sellable = {row['out']['player_id'] for row in board['candidates'] if row['player_id'] == 99}
    # He is legal only when the man sold is one of that club's three.
    assert sellable and sellable <= {r['player_id'] for r in rows if r['team'] == 'BBB'}
    assert board['considered']['club_limit'] == 2


def test_a_hit_is_subtracted_from_net_but_not_gain(world):
    run, rows = world
    run['players'] = rows + [make_row(99, 'DEF', [8.0] * 3)]
    squad = make_squad([row['player_id'] for row in rows])
    free = board_for(run, squad, free_transfers=1)['candidates'][0]
    hit = board_for(run, squad, free_transfers=0)['candidates'][0]
    assert free['gain'] == hit['gain']
    assert hit['net'][1] == pytest.approx(free['net'][1] - transfers.HIT_COST)


def test_one_row_per_incoming_player(world):
    """A player shown twice with two different sales reads as two decisions. It is one."""
    run, rows = world
    run['players'] = rows + [make_row(99, 'DEF', [8.0] * 3)]
    squad = make_squad([row['player_id'] for row in rows])
    board = board_for(run, squad)
    names = [row['player_id'] for row in board['candidates']]
    assert len(names) == len(set(names))
    row = next(row for row in board['candidates'] if row['player_id'] == 99)
    assert row['legal_sales'] == 5


def test_the_sale_shown_is_the_best_one(world):
    """The pair must maximise gain at the deciding horizon, not merely be legal."""
    run, rows = world
    defenders = [row for row in rows if row['position'] == 'DEF']
    run['players'] = rows + [make_row(99, 'DEF', [8.0] * 3)]
    squad = make_squad([row['player_id'] for row in rows])
    board = board_for(run, squad)
    row = next(row for row in board['candidates'] if row['player_id'] == 99)
    assert row['out']['player_id'] == defenders[-1]['player_id']


def test_a_missing_bank_raises_rather_than_assuming_zero(world):
    """Zero is the tightest budget there is, so it must never stand in for "unknown"."""
    run, rows = world
    squad = make_squad([row['player_id'] for row in rows], bank=None)
    with pytest.raises(ValueError, match='no bank'):
        board_for(run, squad)


def test_a_squad_player_missing_from_the_run_raises(world):
    run, rows = world
    run['players'] = rows[:-1]
    squad = make_squad([row['player_id'] for row in rows])
    with pytest.raises(ValueError, match='no projection'):
        board_for(run, squad)


def test_negative_free_transfers_are_refused(world):
    run, rows = world
    squad = make_squad([row['player_id'] for row in rows])
    with pytest.raises(ValueError, match='cannot be negative'):
        board_for(run, squad, free_transfers=-1)


def test_the_selling_price_approximation_travels_with_the_answer(world):
    """A budget built on an approximation must say so wherever it is read."""
    run, rows = world
    squad = make_squad([row['player_id'] for row in rows])
    board = board_for(run, squad)
    assert board['budget']['selling_price_basis'] == transfers.SELLING_PRICE_BASIS
    assert 'current' in board['budget']['note']
