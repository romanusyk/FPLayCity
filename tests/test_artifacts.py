"""Run artifacts, feedback and draft state - the three things the app persists.

Every test runs against a temporary working directory, so nothing here touches the real
`data/`. The behaviour being pinned is mostly about refusing to lose things: runs cannot be
overwritten, pruning cannot delete a run someone left feedback on, and a corrupt draft state
raises rather than silently starting from empty.
"""
import json
import os

import pytest

from src.fpl.models.immutable import PlayerType
from src.fpl.projection import artifacts
from src.fpl.projection.feedback import (
    FeedbackEntry,
    load_feedback,
    runs_with_feedback,
    save_feedback,
)
from src.fpl.projection.methods import ProjectionMethod, ProjectionParams
from src.fpl.projection.vorp import DRAFT_STARTING_SLOTS, ReplacementLevel
from src.web import draft_state


SEASON = '2026-2027'


@pytest.fixture(autouse=True)
def temp_data_dir(tmp_path, monkeypatch):
    """Every artifact path is relative to the working directory, so move it."""
    monkeypatch.chdir(tmp_path)
    return tmp_path


class FakeProjection:
    def __init__(self, player_id, points):
        self.player_id = player_id
        self.points = points
        self.web_name = f'p{player_id}'

    def as_dict(self):
        return {'player_id': self.player_id, 'web_name': self.web_name, 'points': self.points}


def a_run(method_name='v1-baseline', created_at=None, game='draft', label=None) -> dict:
    from datetime import datetime

    method = ProjectionMethod(method_name, 'notes', ProjectionParams())
    projections = [FakeProjection(1, 50.0), FakeProjection(2, 20.0)]
    return artifacts.build_run(
        season=SEASON, game=game, method=method, projections=projections,
        replacement=({PlayerType.FWD: ReplacementLevel(PlayerType.FWD, 20.0, 2, 'p2', 12, 30)}),
        vorp_by_player={1: 30.0, 2: 0.0},
        inputs={'bootstrap': 'bootstrap_2026-08-15T13:36:26.json'},
        created_at=created_at or datetime(2026, 8, 16, 14, 2, 11),
        slots=DRAFT_STARTING_SLOTS,
        managers=4,
        label=label,
    )


class TestRunArtifacts:

    def test_round_trip(self):
        body = a_run()
        path = artifacts.write_run(body)
        assert os.path.exists(path)
        loaded = artifacts.load_run(SEASON, 'draft', body['run_id'])
        assert loaded == body
        assert loaded['players'][0]['vorp'] == 30.0

    def test_the_valuation_basis_is_recorded(self):
        """The live board reads these back rather than keeping its own copy."""
        body = a_run()
        assert body['valuation'] == {'managers': 4, 'slots': {'GKP': 1, 'DEF': 4, 'MID': 4, 'FWD': 2}}

    def test_players_are_stored_best_first(self):
        body = a_run()
        assert [row['player_id'] for row in body['players']] == [1, 2]

    def test_runs_are_immutable(self):
        body = a_run()
        artifacts.write_run(body)
        with pytest.raises(FileExistsError, match='immutable'):
            artifacts.write_run(body)

    def test_listing_is_newest_first_and_per_game(self):
        from datetime import datetime

        for day in (14, 15, 16):
            artifacts.write_run(a_run(created_at=datetime(2026, 8, day, 12, 0, 0)))
        artifacts.write_run(a_run(game='fpl'))

        draft_runs = artifacts.list_runs(SEASON, 'draft')
        assert len(draft_runs) == 3
        assert draft_runs[0].created_at > draft_runs[-1].created_at
        assert len(artifacts.list_runs(SEASON, 'fpl')) == 1
        assert len(artifacts.list_runs(SEASON)) == 4

    def test_a_malformed_run_id_never_reaches_the_filesystem(self):
        with pytest.raises(ValueError, match='Malformed run id'):
            artifacts.run_path(SEASON, 'draft', '../../../etc/passwd')

    def test_an_unknown_game_is_rejected(self):
        with pytest.raises(ValueError, match='Unknown game'):
            artifacts.runs_dir(SEASON, 'fantasy-cricket')

    def test_a_corrupt_artifact_raises_rather_than_disappearing(self):
        artifacts.write_run(a_run())
        stray = os.path.join(artifacts.runs_dir(SEASON, 'draft'), 'not-a-run.json')
        with open(stray, 'w', encoding='utf-8') as handle:
            handle.write('{"nope": true}')
        with pytest.raises(ValueError, match='not a readable run artifact'):
            artifacts.list_runs(SEASON, 'draft')

    def test_pruning_keeps_the_newest(self):
        from datetime import datetime

        ids = []
        for day in range(10, 16):
            body = a_run(created_at=datetime(2026, 8, day, 12, 0, 0))
            artifacts.write_run(body)
            ids.append(body['run_id'])

        deleted = artifacts.prune_runs(SEASON, 'draft', keep=2)
        remaining = {summary.run_id for summary in artifacts.list_runs(SEASON, 'draft')}
        assert len(remaining) == 2
        assert set(ids[-2:]) == remaining
        assert set(deleted) == set(ids[:-2])

    def test_pruning_never_deletes_a_run_with_feedback(self):
        from datetime import datetime

        ids = []
        for day in range(10, 16):
            body = a_run(created_at=datetime(2026, 8, day, 12, 0, 0))
            artifacts.write_run(body)
            ids.append(body['run_id'])

        artifacts.prune_runs(SEASON, 'draft', keep=2, protected={ids[0]})
        remaining = {summary.run_id for summary in artifacts.list_runs(SEASON, 'draft')}
        assert ids[0] in remaining, 'a run someone reviewed must survive'
        assert len(remaining) == 3

    def test_keeping_zero_runs_is_rejected(self):
        with pytest.raises(ValueError, match='keep must be at least 1'):
            artifacts.prune_runs(SEASON, 'draft', keep=0)


def an_entry(**overrides) -> FeedbackEntry:
    defaults = dict(
        season=SEASON, game='draft', run_id='2026-08-16T14-02-11_v1-baseline', gameweek=1,
        player_id=4, web_name='Gabriel', reason='nailed_starter', note='plays every week',
        your_p_start=0.92, model_p_start=0.79, model_points=47.4,
    )
    defaults.update(overrides)
    return FeedbackEntry(**defaults)


class TestFeedback:

    def test_round_trip_keeps_both_sides_of_the_disagreement(self):
        save_feedback(an_entry())
        stored = load_feedback(SEASON)
        assert len(stored) == 1
        assert stored[0].your_p_start == 0.92
        assert stored[0].model_p_start == 0.79, 'the model value is what makes this scoreable'

    def test_entries_are_append_only(self):
        save_feedback(an_entry(created_at='2026-08-16T10:00:00', reason='nailed_starter'))
        save_feedback(an_entry(created_at='2026-08-17T10:00:00', reason='injury_doubt'))
        stored = load_feedback(SEASON)
        assert [entry.reason for entry in stored] == ['injury_doubt', 'nailed_starter']

    def test_filtering_by_gameweek(self):
        save_feedback(an_entry(gameweek=1))
        save_feedback(an_entry(gameweek=2, created_at='2026-08-17T10:00:00'))
        assert len(load_feedback(SEASON, gameweek=2)) == 1

    def test_an_unknown_reason_is_rejected(self):
        with pytest.raises(ValueError, match='Unknown reason'):
            save_feedback(an_entry(reason='vibes'))

    def test_a_probability_outside_zero_to_one_is_rejected(self):
        with pytest.raises(ValueError, match=r'must be in \[0, 1\]'):
            save_feedback(an_entry(your_p_start=1.4))

    def test_runs_with_feedback_reports_what_pruning_must_protect(self):
        save_feedback(an_entry(run_id='2026-08-16T14-02-11_v1-baseline'))
        assert runs_with_feedback(SEASON) == {'2026-08-16T14-02-11_v1-baseline'}

    def test_no_feedback_directory_is_not_an_error(self):
        assert load_feedback(SEASON) == []


class TestDraftState:

    def test_empty_by_default(self):
        state = draft_state.load(SEASON)
        assert state.taken == {}
        assert state.my_players == set()

    def test_round_trip_keeps_integer_keys(self):
        state = draft_state.load(SEASON)
        state.set_owner(411, 'mine')
        state.set_owner(233, 'other')
        draft_state.save(state)

        reloaded = draft_state.load(SEASON)
        assert reloaded.taken == {411: 'mine', 233: 'other'}
        assert reloaded.my_players == {411}
        assert reloaded.all_taken == {411, 233}

    def test_setting_owner_to_none_puts_a_player_back(self):
        state = draft_state.load(SEASON)
        state.set_owner(411, 'mine')
        state.set_owner(411, None)
        assert state.taken == {}

    def test_an_unknown_owner_is_rejected(self):
        with pytest.raises(ValueError, match='Unknown owner'):
            draft_state.load(SEASON).set_owner(411, 'the other guy')

    def test_a_corrupt_state_file_raises(self):
        path = draft_state.state_path(SEASON)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w', encoding='utf-8') as handle:
            handle.write('{ this is not json')
        with pytest.raises(ValueError, match='not a readable draft state'):
            draft_state.load(SEASON)
