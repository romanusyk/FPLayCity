"""What a waiver claim is worth, on hand-built squads.

The property being pinned is the one that separates a waiver from a draft pick: value is
*squad* value. Signing a better player than one you own is worth nothing if he would sit behind
the same five, and the tests below are built so that answer is checkable by eye.

Ownership plumbing (who holds whom, and the season-scoped ids behind it) is covered in
`tests/test_draft_league.py`.
"""
import pytest

from src.fpl.loader.draft_league import build_ownership
from src.web.waivers import (
    DEFAULT_HORIZONS,
    HORIZON_LIMIT,
    SquadValuation,
    default_horizons_for,
    horizon_totals,
    parse_horizons,
    points_by_gameweek,
    resolve_sort_horizon,
    span_gameweeks,
    waiver_board,
)


def a_row(player_id: int, position: str, per_gameweek: dict[int, float], name: str | None = None) -> dict:
    """A run row with only the fields the waiver maths reads."""
    return {
        'player_id': player_id,
        'web_name': name or f'p{player_id}',
        'team': 'ARS',
        'position': position,
        'points': round(sum(per_gameweek.values()), 3),
        'flags': [],
        'inputs': {'minutes': {'p_start': 0.8, 'status': 'a', 'status_meaning': 'available'}},
        'fixtures': [
            {'gameweek': gameweek, 'fixture_id': gameweek * 100 + player_id, 'opponent': 'CHE',
             'at_home': True, 'points': points}
            for gameweek, points in sorted(per_gameweek.items())
        ],
    }


def flat(player_id: int, position: str, per_week: float, gameweeks=(2, 3, 4, 5, 6)) -> dict:
    return a_row(player_id, position, {gameweek: per_week for gameweek in gameweeks})


def a_squad(keepers=(6.0, 2.0), defenders=(6.0, 5.0, 4.0, 3.0, 1.0),
            midfielders=(9.0, 8.0, 7.0, 6.0, 1.0), forwards=(9.0, 5.0, 1.0)) -> list[dict]:
    """A legal 2/5/5/3 squad, ids numbered by position block."""
    rows = []
    for index, points in enumerate(keepers):
        rows.append(flat(100 + index, 'GKP', points))
    for index, points in enumerate(defenders):
        rows.append(flat(200 + index, 'DEF', points))
    for index, points in enumerate(midfielders):
        rows.append(flat(300 + index, 'MID', points))
    for index, points in enumerate(forwards):
        rows.append(flat(400 + index, 'FWD', points))
    return rows


GAMEWEEKS = [2, 3, 4, 5, 6]


class TestParseHorizons:

    def test_default_when_nothing_is_asked_for(self):
        assert parse_horizons(None) == DEFAULT_HORIZONS

    def test_it_reads_and_orders_a_list(self):
        assert parse_horizons('5,1,3') == (1, 3, 5)

    def test_more_than_three_is_refused(self):
        with pytest.raises(ValueError, match='At most 3'):
            parse_horizons('1,2,3,4')

    def test_past_the_projection_is_refused_rather_than_clamped(self):
        """The user's example: 1, 3 and 12 is not a 1, 3 and 10 request."""
        with pytest.raises(ValueError, match=f'1-{HORIZON_LIMIT}'):
            parse_horizons('1,3,12')

    def test_zero_and_negative_are_refused(self):
        for raw in ('0', '-3', '1,0'):
            with pytest.raises(ValueError, match=f'1-{HORIZON_LIMIT}'):
                parse_horizons(raw)

    def test_duplicates_are_refused(self):
        with pytest.raises(ValueError, match='distinct'):
            parse_horizons('3,3')

    def test_nonsense_is_refused(self):
        with pytest.raises(ValueError, match='whole numbers'):
            parse_horizons('1,three')


class TestHorizonsAgainstARun:
    """The rules the boards and the waivers page share. One definition of what "3 GW" means."""

    @staticmethod
    def a_run(gameweek_from: int, gameweek_to: int) -> dict:
        return {'game': 'fpl', 'gameweek_from': gameweek_from, 'gameweek_to': gameweek_to}

    def test_the_span_is_the_longest_horizon_from_the_run_start(self):
        assert span_gameweeks(self.a_run(2, 11), (1, 3, 5)) == [2, 3, 4, 5, 6]

    def test_a_horizon_past_the_run_is_refused_with_the_command_that_fixes_it(self):
        with pytest.raises(ValueError, match='regenerate it|Regenerate it'):
            span_gameweeks(self.a_run(2, 4), (1, 3, 5))

    def test_a_horizon_that_exactly_fills_the_run_is_allowed(self):
        assert span_gameweeks(self.a_run(2, 4), (1, 3)) == [2, 3, 4]

    def test_the_middle_horizon_decides_by_default(self):
        assert resolve_sort_horizon((1, 3, 5), None) == 3
        assert resolve_sort_horizon((2, 4), None) == 4, 'the upper middle of an even list'

    def test_deciding_on_a_horizon_that_is_not_shown_is_refused(self):
        with pytest.raises(ValueError, match='not one of'):
            resolve_sort_horizon((1, 3, 5), 4)

    def test_defaults_shrink_to_a_short_run_rather_than_erroring(self):
        """Nothing was asked for, so nothing can be refused - but nothing is invented either."""
        assert default_horizons_for(self.a_run(2, 11)) == DEFAULT_HORIZONS
        assert default_horizons_for(self.a_run(2, 5)) == (1, 3)
        assert default_horizons_for(self.a_run(2, 2)) == (1,)


class TestHorizonTotals:

    def test_points_are_summed_over_the_window_and_blanks_counted(self):
        row = a_row(1, 'MID', {2: 5.0, 4: 3.0, 6: 2.0})
        totals = horizon_totals(row, [2, 3, 4, 5, 6], (1, 3, 5))
        assert totals['points'] == {1: 5.0, 3: 8.0, 5: 10.0}
        assert totals['blanks'] == {1: 0, 3: 1, 5: 2}, 'GW3 and GW5 are blanks, not missing rows'

    def test_a_double_gameweek_counts_twice_in_one_horizon(self):
        row = a_row(1, 'MID', {})
        row['fixtures'] = [
            {'gameweek': 2, 'fixture_id': 1, 'opponent': 'CHE', 'at_home': True, 'points': 4.0},
            {'gameweek': 2, 'fixture_id': 2, 'opponent': 'BOU', 'at_home': False, 'points': 3.0},
        ]
        totals = horizon_totals(row, [2, 3], (1,))
        assert totals['points'] == {1: 7.0} and totals['blanks'] == {1: 0}


class TestPointsByGameweek:

    def test_a_double_gameweek_is_summed(self):
        row = a_row(1, 'MID', {})
        row['fixtures'] = [
            {'gameweek': 3, 'fixture_id': 1, 'opponent': 'CHE', 'at_home': True, 'points': 4.0},
            {'gameweek': 3, 'fixture_id': 2, 'opponent': 'BOU', 'at_home': False, 'points': 3.0},
        ]
        assert points_by_gameweek(row) == {3: 7.0}

    def test_a_blank_gameweek_is_absent_rather_than_zero(self):
        row = a_row(1, 'MID', {2: 5.0, 4: 5.0})
        assert points_by_gameweek(row) == {2: 5.0, 4: 5.0}


class TestSquadValuation:

    def test_the_baseline_is_the_best_legal_xi_each_gameweek(self):
        valuation = SquadValuation(a_squad(), GAMEWEEKS)
        # The optimiser picks a shape, not the top eleven. Here that is GKP 6, DEF 6+5+4+3 = 18,
        # MID 9+8+7+6 = 30, FWD 9+5 = 14: the fifth midfielder outscores the third forward but
        # cannot displace him without breaking the one-forward minimum.
        assert valuation.baseline(1) == pytest.approx(6 + 18 + 30 + 14)
        assert valuation.baseline(3) == pytest.approx(3 * (6 + 18 + 30 + 14))

    def test_a_squad_that_is_not_2_5_5_3_is_refused(self):
        squad = a_squad()[:-1]
        with pytest.raises(ValueError, match='expected'):
            SquadValuation(squad, GAMEWEEKS)

    def test_a_bench_player_is_marked_as_not_starting(self):
        valuation = SquadValuation(a_squad(), GAMEWEEKS)
        assert valuation.starts(300, 3) == 3, 'the best midfielder starts every week'
        assert valuation.starts(304, 3) == 0, 'the fifth midfielder never does'


class TestSwapGain:

    def test_upgrading_a_starter_is_worth_the_difference(self):
        valuation = SquadValuation(a_squad(), GAMEWEEKS)
        gains = valuation.swap_gain(300, flat(999, 'MID', 12.0), (1, 3))
        assert gains[1] == pytest.approx(3.0), '12 replaces the 9 he displaces'
        assert gains[3] == pytest.approx(9.0)

    def test_upgrading_a_bench_player_is_worth_almost_nothing(self):
        """The whole reason a waiver is not a draft pick."""
        valuation = SquadValuation(a_squad(), GAMEWEEKS)
        # 2.5 is below the fourth defender, so no legal formation finds room for him. Anything
        # above that does: the optimiser drops to three defenders and plays five midfielders.
        bench = valuation.swap_gain(304, flat(999, 'MID', 2.5), (1,))
        starter = valuation.swap_gain(300, flat(999, 'MID', 2.5), (1,))
        assert bench[1] == pytest.approx(0.0), 'he still would not make the XI'
        assert starter[1] < 0, 'and dropping the best midfielder for him loses points'

    def test_a_signing_who_never_plays_cannot_help(self):
        valuation = SquadValuation(a_squad(), GAMEWEEKS)
        gains = valuation.swap_gain(304, a_row(999, 'MID', {}), (1, 3))
        assert gains[3] == pytest.approx(0.0)

    def test_horizons_can_disagree_about_the_same_swap(self):
        """A one-week hit for a five-week gain is the trade-off the page exists to show."""
        valuation = SquadValuation(a_squad(), GAMEWEEKS)
        candidate = a_row(999, 'MID', {2: 0.0, 3: 12.0, 4: 12.0, 5: 12.0, 6: 12.0})
        gains = valuation.swap_gain(300, candidate, (1, 5))
        assert gains[1] < 0, 'he blanks in the first gameweek'
        assert gains[5] > 0, 'and is the better player over five'


def an_ownership(squad: list[dict], free: list[dict], locked: tuple[int, ...] = ()):
    """One four-manager league: our fifteen, three filler squads, and a free-agent pool."""
    entries = [
        {'entry_id': 1, 'entry_name': 'Mine', 'player_first_name': 'A', 'player_last_name': 'B',
         'waiver_pick': 2},
    ]
    element_status = [{'element': row['player_id'], 'owner': 1, 'status': 'o',
                       'in_accepted_trade': False} for row in squad]
    filler = 5000
    for entry_id in (2, 3, 4):
        entries.append({'entry_id': entry_id, 'entry_name': f'Rival {entry_id}',
                        'player_first_name': 'R', 'player_last_name': str(entry_id),
                        'waiver_pick': entry_id})
        for _ in range(15):
            element_status.append({'element': filler, 'owner': entry_id, 'status': 'o',
                                   'in_accepted_trade': False})
            filler += 1
    for row in free:
        element_status.append({
            'element': row['player_id'], 'owner': None,
            'status': 'l' if row['player_id'] in locked else 'a', 'in_accepted_trade': False,
        })
    return build_ownership(
        season='2026-2027',
        my_entry_id=1,
        details={'league': {'id': 77, 'name': 'Test League', 'transaction_mode': 'waivers'},
                 'league_entries': entries},
        element_status={'element_status': element_status},
        fetched_at='2026-08-26T12:00:00',
    )


def a_run(squad: list[dict], free: list[dict], gameweek_from: int = 2, gameweek_to: int = 11) -> dict:
    return {
        'run_id': '2026-08-26T12-00-00_v3-role-trust',
        'game': 'draft',
        'season': '2026-2027',
        'created_at': '2026-08-26T12:00:00',
        'label': None,
        'method': {'name': 'v3-role-trust', 'params': {}},
        'inputs': {},
        'gameweek_from': gameweek_from,
        'gameweek_to': gameweek_to,
        'players': squad + free,
    }


class TestWaiverBoard:

    def test_it_ranks_by_the_sort_horizon_and_reports_every_horizon(self):
        squad = a_squad()
        free = [flat(901, 'MID', 12.0), flat(902, 'MID', 10.0), flat(903, 'FWD', 2.0)]
        board = waiver_board(a_run(squad, free), an_ownership(squad, free),
                             horizons=(1, 3), sort_horizon=3)
        assert [row['player_id'] for row in board['candidates']] == [901, 902, 903]
        top = board['candidates'][0]
        assert top['out']['player_id'] == 304, 'drop the bench midfielder, not the one displaced'
        # +9 a week, not the +3 the displaced midfielder suggests: signing him lets the XI
        # switch to three defenders and five midfielders, so the whole shape improves.
        assert top['gain'] == {1: pytest.approx(9.0), 3: pytest.approx(27.0)}
        assert board['considered']['improving'] == 2, 'the third candidate would never start'

    def test_the_suggested_drop_is_always_the_same_position(self):
        """Waivers are like-for-like; a cross-position swap is a trade the game will not accept."""
        squad = a_squad()
        free = [flat(901, 'FWD', 12.0)]
        board = waiver_board(a_run(squad, free), an_ownership(squad, free), horizons=(3,))
        assert board['candidates'][0]['out']['position'] == 'FWD'

    def test_locked_players_are_excluded_unless_asked_for(self):
        squad = a_squad()
        free = [flat(901, 'MID', 12.0), flat(902, 'MID', 11.0)]
        ownership = an_ownership(squad, free, locked=(901,))
        run = a_run(squad, free)
        default = waiver_board(run, ownership, horizons=(3,))
        assert [row['player_id'] for row in default['candidates']] == [902]
        assert default['considered']['locked'] == 1

        with_locked = waiver_board(run, ownership, horizons=(3,), include_locked=True)
        assert [row['player_id'] for row in with_locked['candidates']] == [901, 902]
        assert with_locked['candidates'][0]['claimable'] is False

    def test_a_horizon_longer_than_the_run_is_refused_with_the_fix(self):
        squad = a_squad()
        free = [flat(901, 'MID', 12.0)]
        run = a_run(squad, free, gameweek_from=2, gameweek_to=4)
        with pytest.raises(ValueError, match='--gw-to 6'):
            waiver_board(run, an_ownership(squad, free), horizons=(5,))

    def test_a_squad_player_missing_from_the_run_is_refused(self):
        squad = a_squad()
        free = [flat(901, 'MID', 12.0)]
        run = a_run(squad[:-1], free)
        with pytest.raises(ValueError, match='no projection'):
            waiver_board(run, an_ownership(squad, free), horizons=(3,))

    def test_the_squad_panel_reports_its_own_value_and_who_starts(self):
        squad = a_squad()
        free = [flat(901, 'MID', 12.0)]
        board = waiver_board(a_run(squad, free), an_ownership(squad, free), horizons=(1, 3))
        assert board['squad']['points'][1] == pytest.approx(68.0)
        panel = {row['player_id']: row for row in board['squad']['players']}
        assert panel[304]['starts'] == {1: 0, 3: 0}, 'the fifth midfielder is a bench player'
        assert panel[300]['starts'] == {1: 1, 3: 3}


class TestPlayerList:
    """The browsing view: every projected player, priced at each horizon, tagged by owner.

    Separate from `candidates` on purpose. That list answers "what should I do" and is ranked by
    swap gain; this one answers "who is out there" and is ranked by points, so a squad can be
    picked by eye.
    """

    def a_board(self, **kwargs):
        squad = a_squad()
        free = [flat(901, 'MID', 12.0), flat(902, 'MID', 11.0)]
        rivals = [flat(5000 + index, 'MID', 9.0) for index in range(0)]
        ownership = an_ownership(squad, free, **kwargs)
        return waiver_board(a_run(squad, free + rivals), ownership, horizons=(1, 3)), squad, free

    def test_every_projected_player_is_listed_not_just_the_claimable_ones(self):
        board, squad, free = self.a_board()
        assert len(board['players']) == len(squad) + len(free)
        assert board['considered']['unknown_owner'] == 0

    def test_it_is_ranked_by_points_at_the_deciding_horizon(self):
        board, _, _ = self.a_board()
        points = [row['points'][board['sort_horizon']] for row in board['players']]
        assert points == sorted(points, reverse=True)

    def test_each_player_carries_how_he_relates_to_your_squad(self):
        board, _, _ = self.a_board()
        kinds = {row['player_id']: row['owner_kind'] for row in board['players']}
        assert kinds[300] == 'mine'
        assert kinds[901] == 'free'

    def test_a_locked_player_is_distinguished_from_a_free_one(self):
        """He shows as unowned on the site but cannot be signed until the next deadline."""
        board, _, _ = self.a_board(locked=(901,))
        kinds = {row['player_id']: row['owner_kind'] for row in board['players']}
        assert kinds[901] == 'locked' and kinds[902] == 'free'

    def test_your_own_players_carry_their_start_counts(self):
        """So the list doubles as a team sheet: who starts, who is only cover."""
        board, _, _ = self.a_board()
        listed = {row['player_id']: row for row in board['players']}
        assert listed[300]['starts'] == {1: 1, 3: 3}
        assert listed[304]['starts'] == {1: 0, 3: 0}
        assert 'starts' not in listed[901], 'a player you do not own has no place in your XI'
