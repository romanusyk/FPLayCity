"""What a pick costs, as opposed to what a player is worth.

Hand-built boards, so the arithmetic is checkable by eye. The distinction being pinned is that
these numbers move with every pick, which is exactly what replacement level does not do - see
`TestValueOverReplacement.test_taking_the_best_forwards_does_not_move_replacement` in
`tests/test_projection_models.py` for the other half of that pair.
"""
import pytest

from src.web.opportunity import next_best_drop, picks_between_turns, wait_costs


def row(player_id: int, position: str, points: float, owner: str | None = None) -> dict:
    return {
        'player_id': player_id, 'web_name': f'p{player_id}', 'position': position,
        'points': points, 'vorp': points - 20.0, 'owner': owner,
    }


BOARD = [
    row(1, 'FWD', 46.0), row(2, 'FWD', 30.0), row(3, 'FWD', 28.0),
    row(4, 'MID', 45.0), row(5, 'MID', 44.0), row(6, 'MID', 43.0),
]


class TestNextBestDrop:

    def test_it_is_the_gap_to_the_next_player_in_the_position(self):
        drops = next_best_drop(BOARD)
        assert drops[1] == pytest.approx(16.0), 'the cliff below the best forward'
        assert drops[4] == pytest.approx(1.0), 'midfield is flat at the top'

    def test_a_flat_position_scores_low_even_when_its_players_score_high(self):
        """The whole point: 45 points behind 44 points is not worth a first pick."""
        drops = next_best_drop(BOARD)
        assert drops[4] < drops[1]

    def test_the_last_player_at_a_position_is_measured_against_nobody(self):
        drops = next_best_drop([row(1, 'GKP', 30.0)])
        assert drops[1] == pytest.approx(30.0)

    def test_drafted_players_are_not_choices(self):
        drops = next_best_drop([row(1, 'FWD', 46.0, owner='mine'), row(2, 'FWD', 30.0)])
        assert 1 not in drops
        assert drops[2] == pytest.approx(30.0), 'the gap now measures against an empty pool'

    def test_a_pick_below_widens_the_gap_and_a_pick_above_does_not(self):
        """Which way this points is the whole content of the metric.

        Taking the player *below* someone makes him more urgent - the fallback got worse.
        Taking the player *above* him does not: his own alternative is unchanged, he has merely
        become the best available.
        """
        before = next_best_drop(BOARD)[2]
        below_gone = next_best_drop(
            [row(3, 'FWD', 28.0, owner='other')] + [r for r in BOARD if r['player_id'] != 3]
        )[2]
        above_gone = next_best_drop(
            [row(1, 'FWD', 46.0, owner='other')] + [r for r in BOARD if r['player_id'] != 1]
        )[2]
        assert below_gone > before
        assert above_gone == pytest.approx(before)


class TestWaitCosts:

    def test_it_prices_the_player_you_would_actually_get(self):
        costs = wait_costs(BOARD, picks_until_next_turn=2)
        # The two highest VORP rows are p1 (26.0) and p4 (25.0), so both go.
        assert costs['FWD']['best_name'] == 'p1'
        assert costs['FWD']['then_name'] == 'p2'
        assert costs['FWD']['cost'] == pytest.approx(16.0)
        assert costs['MID']['then_name'] == 'p5'
        assert costs['MID']['cost'] == pytest.approx(1.0)

    def test_nothing_is_lost_when_you_pick_again_immediately(self):
        costs = wait_costs(BOARD, picks_until_next_turn=0)
        assert costs['FWD']['cost'] == 0.0
        assert costs['FWD']['survives'] is True

    def test_a_position_can_be_emptied_by_waiting(self):
        costs = wait_costs([row(1, 'GKP', 30.0)], picks_until_next_turn=4)
        assert costs['GKP']['then'] is None
        assert costs['GKP']['cost'] is None, 'no player left is not a cost of zero'

    def test_already_drafted_players_are_excluded_from_the_simulation(self):
        board = [row(1, 'FWD', 46.0, owner='other'), row(2, 'FWD', 30.0), row(3, 'FWD', 28.0)]
        costs = wait_costs(board, picks_until_next_turn=1)
        assert costs['FWD']['best_name'] == 'p2'
        assert costs['FWD']['then_name'] == 'p3'

    def test_waiting_backwards_is_rejected(self):
        with pytest.raises(ValueError, match='zero or more'):
            wait_costs(BOARD, picks_until_next_turn=-1)


class TestPicksBetweenTurns:

    def test_a_four_manager_snake_costs_six_picks(self):
        assert picks_between_turns(4) == 6

    def test_a_solo_league_never_waits(self):
        assert picks_between_turns(1) == 0

    def test_zero_managers_is_rejected(self):
        with pytest.raises(ValueError, match='at least 1'):
            picks_between_turns(0)
