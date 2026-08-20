"""End-to-end HTTP tests against the real stored snapshots.

These run in the repo's working directory because the app loads `data/<season>/` at startup,
and they are read-only: no run is written, no feedback is saved. Where a route needs a run that
may not exist on a given machine, the test skips rather than inventing one - writing artifacts
into the real data directory from a test would be worse than a gap in coverage.

Offline persistence behaviour is covered in `tests/test_artifacts.py`, which uses a temporary
directory and does not need any of this.
"""
import pytest

from src.fpl.loader.store import JsonSnapshotStore, SnapshotSpec
from src.fpl.loader.utils import Season
from src.fpl.projection import artifacts


fastapi_testclient = pytest.importorskip('fastapi.testclient')


@pytest.fixture(scope='module')
def client():
    """One app for the module. Startup loads snapshots and builds the history join."""
    if JsonSnapshotStore(SnapshotSpec(base_path=f'data/{Season.CURRENT}/bootstrap')).find_latest() is None:
        pytest.skip(f'No data/{Season.CURRENT}/bootstrap snapshot')
    from src.web.serve import create_app

    with fastapi_testclient.TestClient(create_app(Season.CURRENT, next_gameweek=1)) as test_client:
        yield test_client


@pytest.fixture(scope='module')
def draft_run_id():
    runs = artifacts.list_runs(Season.CURRENT, 'draft')
    if not runs:
        pytest.skip('No draft runs; generate one with: uv run -m src.fpl.project --game draft')
    return runs[0].run_id


def test_index_is_served(client):
    response = client.get('/')
    assert response.status_code == 200
    assert 'FPLayCity' in response.text


def test_config_lists_both_games_and_the_method_registry(client):
    body = client.get('/api/config').json()
    assert body['season'] == Season.CURRENT
    assert body['games'] == ['draft', 'fpl']
    assert 'v1-baseline' in body['methods']
    assert len(body['teams']) == 20
    assert 'MNG' not in body['positions'], 'managers are a different game'


def test_runs_can_be_filtered_by_game(client):
    body = client.get('/api/runs?game=draft').json()
    assert all(run['game'] == 'draft' for run in body['runs'])


def test_board_is_sorted_and_carries_its_inputs(client, draft_run_id):
    body = client.get(f'/api/board?game=draft&run_id={draft_run_id}').json()
    players = body['players']
    assert players, 'a run with no players is not a board'
    assert players[0]['rank'] == 1
    assert [row['vorp'] for row in players] == sorted((row['vorp'] for row in players), reverse=True)
    assert set(body['replacement_level']) == {'GKP', 'DEF', 'MID', 'FWD'}
    for row in players[:5]:
        assert 'p_start' in row and 0.0 <= row['p_start'] <= 1.0
        assert 'dc_sample' in row, 'sample size must travel with the number'
        assert 'fixtures' not in row, 'the board view must stay slim'


def test_draft_board_is_tiered_and_the_fpl_board_is_not(client, draft_run_id):
    draft = client.get(f'/api/board?game=draft&run_id={draft_run_id}').json()
    assert draft['players'][0]['tier'] == 1

    fpl_runs = artifacts.list_runs(Season.CURRENT, 'fpl')
    if not fpl_runs:
        pytest.skip('No fpl runs')
    fpl = client.get('/api/board?game=fpl').json()
    assert fpl['players'][0]['rank'] == 1
    assert fpl['players'][0]['tier'] is None
    assert fpl['draft'] is None


def test_live_board_with_no_picks_matches_the_stored_board(client, draft_run_id):
    """The regression that shipped: two copies of the slot table, quietly disagreeing.

    With nothing drafted, recomputing replacement level must reproduce exactly what the
    projector stored. When the live path kept its own slot constant it did not, and an idle
    draft board showed three goalkeepers in the top ten that the stored board did not have.
    """
    client.post('/api/draft/reset', json={})
    stored = client.get(f'/api/board?game=draft&run_id={draft_run_id}&live=false').json()
    live = client.get(f'/api/board?game=draft&run_id={draft_run_id}&live=true').json()

    for position, level in stored['replacement_level'].items():
        assert live['replacement_level'][position]['points'] == pytest.approx(level['points']), (
            f'{position} replacement level drifted between the stored and live boards'
        )
    stored_top = [row['player_id'] for row in stored['players'][:20]]
    live_top = [row['player_id'] for row in live['players'][:20]]
    assert stored_top == live_top


def test_the_live_board_prices_a_single_pick_as_well_as_the_season(client, draft_run_id):
    """VORP answers "is he valuable", the two waiting figures answer "what does this pick cost".

    Both must be present live, and absent when the board is a stored snapshot - a number that
    depends on who is still available cannot be served off a file.
    """
    client.post('/api/draft/reset', json={})
    live = client.get(f'/api/board?game=draft&run_id={draft_run_id}&live=true').json()
    assert live['waiting']['picks_until_next_turn'] == 6, 'four managers, snake order'
    for position, entry in live['waiting']['positions'].items():
        assert entry['cost'] is None or entry['cost'] >= 0
        assert entry['best'] >= (entry['then'] or 0)
    assert all(row['drop_next'] is not None for row in live['players'] if not row['owner'])

    stored = client.get(f'/api/board?game=draft&run_id={draft_run_id}&live=false').json()
    assert stored['waiting'] is None
    assert 'drop_next' not in stored['players'][0]


def test_the_wait_horizon_can_be_overridden(client, draft_run_id):
    """Only you know where you sit in the draft order."""
    client.post('/api/draft/reset', json={})
    near = client.get(f'/api/board?game=draft&live=true&picks_until_next_turn=1').json()
    far = client.get(f'/api/board?game=draft&live=true&picks_until_next_turn=20').json()
    assert near['waiting']['picks_until_next_turn'] == 1
    costs_near = [e['cost'] or 0 for e in near['waiting']['positions'].values()]
    costs_far = [e['cost'] or 0 for e in far['waiting']['positions'].values()]
    assert sum(costs_far) > sum(costs_near), 'waiting longer must cost more, not less'


def test_a_negative_wait_horizon_is_rejected(client):
    response = client.get('/api/board?game=draft&live=true&picks_until_next_turn=-3')
    assert response.status_code == 400


def test_a_run_records_what_it_priced_replacement_against(client, draft_run_id):
    """Without this the live board cannot know which slot table to use."""
    body = artifacts.load_run(Season.CURRENT, 'draft', draft_run_id)
    assert body['valuation']['managers'] >= 2
    assert set(body['valuation']['slots']) == {'GKP', 'DEF', 'MID', 'FWD'}
    assert body['valuation']['slots']['GKP'] == 1, 'starting slots, not roster slots'


def test_board_defaults_to_the_newest_run(client, draft_run_id):
    body = client.get('/api/board?game=draft').json()
    assert body['run']['run_id'] == draft_run_id


def test_unknown_run_id_is_a_404_not_an_empty_board(client):
    response = client.get('/api/board?game=draft&run_id=2026-01-01T00-00-00_nope')
    assert response.status_code == 404
    assert 'nope' in response.json()['detail']


def test_a_traversal_attempt_in_the_run_id_is_rejected(client):
    response = client.get('/api/board?game=draft&run_id=../../../../etc/passwd')
    assert response.status_code in (400, 404)


def test_unknown_game_is_rejected(client):
    assert client.get('/api/board?game=cricket').status_code == 400


def test_player_detail_explains_every_number(client, draft_run_id):
    board = client.get(f'/api/board?game=draft&run_id={draft_run_id}').json()
    player_id = board['players'][0]['player_id']
    body = client.get(f'/api/player?player_id={player_id}&game=draft&run_id={draft_run_id}').json()

    player = body['player']
    assert player['player_id'] == player_id
    assert set(player['components']) >= {'appearance', 'goals', 'clean_sheets', 'bonus'}
    assert player['inputs']['minutes']['sample_starts'] >= 0
    assert player['inputs']['defensive_contribution']['starts'] >= 0
    assert len(player['fixtures']) >= 1
    assert sum(player['components'].values()) == pytest.approx(player['points'], abs=0.02)


def test_player_history_reports_its_own_coverage(client, draft_run_id):
    board = client.get(f'/api/board?game=draft&run_id={draft_run_id}').json()
    with_history = next(
        (row for row in board['players'] if row['dc_sample'] > 10), board['players'][0]
    )
    body = client.get(f'/api/player?player_id={with_history["player_id"]}&game=draft').json()
    assert body['history'], 'a player with a defensive sample must have match rows'
    assert body['history_coverage'], 'coverage must be stated, not assumed to be a full season'
    row = body['history'][0]
    assert isinstance(row['opponent'], str), 'opponent must be a club name, not a season-local id'
    assert isinstance(row['defensive_hit'], bool)


def test_unknown_player_is_a_404(client, draft_run_id):
    response = client.get(f'/api/player?player_id=999999&game=draft&run_id={draft_run_id}')
    assert response.status_code == 404


def test_compare_reports_what_moved_and_why(client):
    runs = artifacts.list_runs(Season.CURRENT, 'draft')
    if len(runs) < 2:
        pytest.skip('Need two draft runs to compare')
    a, b = runs[1].run_id, runs[0].run_id
    body = client.get(f'/api/compare?game=draft&a={a}&b={b}').json()

    assert body['a']['run_id'] == a and body['b']['run_id'] == b
    assert body['mean_abs_rank_delta'] >= 0
    for entry in body['risers']:
        assert entry['rank_delta'] > 0
    for entry in body['fallers']:
        assert entry['rank_delta'] < 0
    if body['risers'] and len(body['risers']) > 1:
        assert body['risers'][0]['rank_delta'] >= body['risers'][1]['rank_delta']


def test_comparing_a_run_with_itself_moves_nothing(client, draft_run_id):
    body = client.get(f'/api/compare?game=draft&a={draft_run_id}&b={draft_run_id}').json()
    assert body['mean_abs_rank_delta'] == 0
    assert body['risers'] == [] and body['fallers'] == []
    assert body['param_diff'] == {}


def test_calibration_says_so_when_nothing_has_resolved(client, draft_run_id):
    body = client.get(f'/api/calibration?game=draft&run_id={draft_run_id}').json()
    if body['resolved_gameweeks'] == 0:
        assert 'nothing to score' in body['message']
        assert body['gameweeks'] == []
    else:
        assert body['components']['p_start']['model'] is not None
        assert body['components']['p_start']['baseline'] is not None
