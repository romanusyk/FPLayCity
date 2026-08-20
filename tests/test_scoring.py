"""The scoring function has to agree with FPL, not with itself.

The first test is the one that matters: every stored player-match is re-scored from its raw
fields and compared against the `total_points` FPL awarded. A single mismatch means the
projection is measuring its own arithmetic, so the assertion is exact equality across the whole
dataset rather than a tolerance.
"""
import glob
import json
import os

import pytest

from src.fpl.loader.store import JsonSnapshotStore, SnapshotSpec
from src.fpl.loader.utils import Season
from src.fpl.models.immutable import PlayerType
from src.fpl.projection import poisson
from src.fpl.projection.scoring import (
    clears_defensive_threshold,
    defensive_contribution_threshold,
    appearance_points,
    score_player_match,
)


EVIDENCE_SEASON = Season.s2526


def _stored_matches():
    """Yield `(position, history_row)` for every stored player-match in the evidence season."""
    store = JsonSnapshotStore(SnapshotSpec(base_path=f"data/{EVIDENCE_SEASON}/bootstrap"))
    if store.find_latest() is None:
        pytest.skip(f"No data/{EVIDENCE_SEASON}/bootstrap snapshot to reconcile against")
    positions = {
        element['id']: PlayerType(element['element_type'])
        for element in store.load_latest()['elements']
    }
    paths = glob.glob(os.path.join('data', EVIDENCE_SEASON, 'elements', '*_*.json'))
    if not paths:
        pytest.skip(f"No data/{EVIDENCE_SEASON}/elements snapshots")
    for path in paths:
        with open(path, encoding='utf-8') as handle:
            payload = json.load(handle)
        for row in payload.get('history', []):
            position = positions.get(row['element'])
            if position is None or position is PlayerType.MNG:
                continue
            yield position, row


def test_scoring_reconciles_with_every_stored_player_match():
    checked = 0
    mismatches = []
    for position, row in _stored_matches():
        score = score_player_match(
            position=position,
            minutes=row['minutes'],
            goals_scored=row['goals_scored'],
            assists=row['assists'],
            clean_sheet=bool(row['clean_sheets']),
            goals_conceded=row['goals_conceded'],
            saves=row['saves'],
            defensive_contribution=row['defensive_contribution'],
            bonus=row['bonus'],
            yellow_cards=row['yellow_cards'],
            red_cards=row['red_cards'],
            penalties_saved=row['penalties_saved'],
            penalties_missed=row['penalties_missed'],
            own_goals=row['own_goals'],
        )
        checked += 1
        if score.total != row['total_points']:
            mismatches.append((row['element'], row['round'], score.total, row['total_points']))

    assert checked > 1000, f"Only {checked} player-matches available; the check is not meaningful"
    assert not mismatches, (
        f"{len(mismatches)} of {checked} player-matches do not reconcile. "
        f"First five: {mismatches[:5]}"
    )


def test_appearance_points_are_a_step_function():
    assert appearance_points(0) == 0
    assert appearance_points(1) == 1
    assert appearance_points(59) == 1
    assert appearance_points(60) == 2
    assert appearance_points(90) == 2


def test_clean_sheet_needs_sixty_minutes():
    def score(minutes):
        return score_player_match(
            position=PlayerType.DEF, minutes=minutes, goals_scored=0, assists=0,
            clean_sheet=True, goals_conceded=0, saves=0, defensive_contribution=0, bonus=0,
            yellow_cards=0, red_cards=0, penalties_saved=0, penalties_missed=0, own_goals=0,
        ).clean_sheets

    assert score(59) == 0
    assert score(60) == 4


def test_defensive_thresholds_differ_by_position():
    assert defensive_contribution_threshold(PlayerType.DEF) == 10
    assert defensive_contribution_threshold(PlayerType.MID) == 12
    assert defensive_contribution_threshold(PlayerType.FWD) == 12
    assert defensive_contribution_threshold(PlayerType.GKP) == 0

    assert not clears_defensive_threshold(PlayerType.DEF, 9)
    assert clears_defensive_threshold(PlayerType.DEF, 10)
    assert not clears_defensive_threshold(PlayerType.MID, 11)
    assert clears_defensive_threshold(PlayerType.MID, 12)
    # Goalkeepers cannot earn it however many actions they rack up.
    assert not clears_defensive_threshold(PlayerType.GKP, 99)


def test_managers_cannot_be_scored_as_players():
    with pytest.raises(KeyError, match='separate FPL game mode'):
        score_player_match(
            position=PlayerType.MNG, minutes=90, goals_scored=0, assists=0, clean_sheet=False,
            goals_conceded=0, saves=0, defensive_contribution=0, bonus=0, yellow_cards=0,
            red_cards=0, penalties_saved=0, penalties_missed=0, own_goals=0,
        )


def test_expected_floor_div_is_not_the_naive_division():
    """A keeper averaging 3 saves does not average 1 save point - the floor eats the remainder."""
    naive = 3.0 / 3
    actual = poisson.expected_floor_div(3.0, 3)
    assert actual < naive
    assert round(actual, 3) == 0.665


def test_poisson_tail_matches_a_hand_computation():
    # P(X >= 1) = 1 - e^-1
    assert round(poisson.tail(1, 1.0), 6) == round(1 - 2.718281828 ** -1, 6)
    assert poisson.tail(0, 5.0) == 1.0
    assert poisson.tail(5, 0.0) == 0.0
