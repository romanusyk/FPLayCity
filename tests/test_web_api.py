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


@pytest.fixture(scope='module')
def fpl_run_id():
    runs = artifacts.list_runs(Season.CURRENT, 'fpl')
    if not runs:
        pytest.skip('No fpl runs; generate one with: uv run -m src.fpl.project --game fpl')
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


def test_board_defaults_to_the_newest_run_of_the_default_method(client, draft_run_id):
    """Newest *of the default method*: a control run must not hijack the default board."""
    config = client.get('/api/config').json()
    runs = client.get('/api/runs', params={'game': 'draft'}).json()['runs']
    expected = next(
        (run['run_id'] for run in runs if run['method'] == config['default_method']),
        draft_run_id,
    )
    body = client.get('/api/board?game=draft').json()
    assert body['run']['run_id'] == expected


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


def test_config_advertises_the_waiver_limits(client):
    body = client.get('/api/config').json()['waivers']
    assert body['horizon_limit'] == 10
    assert len(body['default_horizons']) == len(set(body['default_horizons'])) <= body['max_horizons']


def test_a_horizon_past_the_projection_is_a_400_whatever_the_league_state(client):
    response = client.get('/api/waivers', params={'horizons': '1,3,12'})
    assert response.status_code == 400
    assert '1-10' in response.json()['detail']


def test_waivers_either_serves_a_board_or_says_how_to_connect_a_league(client, draft_run_id):
    """Both outcomes are correct states; an empty table would not be."""
    response = client.get('/api/waivers', params={'run_id': draft_run_id, 'horizons': '1,3,5'})
    if response.status_code == 409:
        assert 'src.fpl.league' in response.json()['detail']
        return
    assert response.status_code == 200, response.text
    body = response.json()
    assert body['horizons'] == [1, 3, 5] and body['sort_horizon'] == 3
    assert len(body['squad']['players']) == 15
    for row in body['candidates']:
        assert row['out']['position'] == row['position'], 'waivers are like-for-like'
        assert row['claimable'] is True


def test_the_default_run_is_the_default_method_not_merely_the_newest(client):
    """A freshly generated control must not become the board every page load opens on."""
    config = client.get('/api/config').json()
    default_method = config['default_method']
    runs = client.get('/api/runs', params={'game': 'draft'}).json()['runs']
    if not any(run['method'] == default_method for run in runs):
        pytest.skip(f'No {default_method} draft run stored')
    served = client.get('/api/board', params={'game': 'draft'}).json()['run']
    assert served['method']['name'] == default_method


def test_waivers_lists_the_whole_pool_alongside_the_ranked_claims(client, draft_run_id):
    """The browsing view. Never truncated - `limit` applies to claims only."""
    response = client.get('/api/waivers',
                          params={'run_id': draft_run_id, 'horizons': '1,3,5', 'limit': 5})
    if response.status_code == 409:
        pytest.skip('No draft league connected')
    body = response.json()
    run = artifacts.load_run(Season.CURRENT, 'draft', draft_run_id)
    assert len(body['candidates']) == 5
    assert len(body['players']) == len(run['players'])
    kinds = {row['owner_kind'] for row in body['players']}
    assert kinds <= {'mine', 'rival', 'free', 'locked', 'unknown'}
    assert sum(1 for row in body['players'] if row['owner_kind'] == 'mine') == 15
    for row in body['players']:
        assert set(row['points']) == {'1', '3', '5'}


def test_transfers_either_serve_a_board_or_say_how_to_connect_a_team(client, fpl_run_id):
    """Both outcomes are correct states; an empty table would not be."""
    response = client.get('/api/transfers', params={'run_id': fpl_run_id, 'horizons': '1,3,5'})
    if response.status_code == 409:
        assert 'src.fpl.squad' in response.json()['detail']
        return
    assert response.status_code == 200, response.text
    body = response.json()
    assert body['horizons'] == [1, 3, 5] and body['sort_horizon'] == 3
    assert len(body['squad']['players']) == 15
    assert body['budget']['selling_price_basis'] == 'current_price'
    owned = {row['player_id'] for row in body['squad']['players']}
    seen = set()
    for row in body['candidates']:
        assert row['out']['position'] == row['position'], 'a classic squad keeps its 2/5/5/3 shape'
        assert row['player_id'] not in owned, 'you cannot buy a player you already own'
        assert row['out']['player_id'] in owned, 'you can only sell your own'
        assert row['player_id'] not in seen, 'one row per incoming player'
        seen.add(row['player_id'])
        assert row['gain'][str(body['sort_horizon'])] > 0


def test_a_hit_moves_net_and_leaves_gain_alone(client, fpl_run_id):
    """The hit is the difference between "an improvement" and "worth doing"."""
    params = {'run_id': fpl_run_id, 'horizons': '1,3,5'}
    free = client.get('/api/transfers', params=dict(params, free_transfers=1))
    if free.status_code == 409:
        pytest.skip('No classic-FPL team connected')
    paid = client.get('/api/transfers', params=dict(params, free_transfers=0))
    assert free.status_code == paid.status_code == 200
    free_body, paid_body = free.json(), paid.json()
    if not free_body['candidates']:
        pytest.skip('No improving transfer to price')
    a, b = free_body['candidates'][0], paid_body['candidates'][0]
    assert a['player_id'] == b['player_id'] and a['gain'] == b['gain']
    for horizon, value in a['net'].items():
        assert b['net'][horizon] == pytest.approx(value - free_body['hit_cost'])


def test_a_transfer_horizon_past_the_projection_is_a_400(client):
    response = client.get('/api/transfers', params={'horizons': '1,3,12'})
    assert response.status_code == 400
    assert '1-10' in response.json()['detail']


def test_the_fpl_board_carries_points_at_every_requested_horizon(client):
    """The same three horizons as the waivers page, on the classic board."""
    if not artifacts.list_runs(Season.CURRENT, 'fpl'):
        pytest.skip('No fpl runs')
    body = client.get('/api/board', params={'game': 'fpl', 'horizons': '1,3,5'}).json()
    assert body['horizons'] == [1, 3, 5] and body['sort_horizon'] == 3
    assert len(body['gameweeks']) == 5
    assert body['gameweeks'][0] == body['run']['gameweek_from']

    run = artifacts.load_run(Season.CURRENT, 'fpl', body['run']['run_id'])
    stored = {row['player_id']: row for row in run['players']}
    for row in body['players'][:20]:
        fixtures = stored[row['player_id']]['fixtures']
        for horizon in (1, 3, 5):
            window = body['gameweeks'][:horizon]
            expected = sum(f['points'] for f in fixtures if f['gameweek'] in window)
            assert row['horizon_points'][str(horizon)] == pytest.approx(expected, abs=0.01)
        assert row['horizon_points']['5'] <= row['points'] + 0.01, 'a horizon is part of the run'
        assert row['horizon_per_gameweek']['3'] == pytest.approx(
            row['horizon_points']['3'] / 3, abs=0.01)


def test_the_fpl_board_ranks_on_the_deciding_horizon(client):
    """Rank and the column you are deciding on must never disagree - that is how a board lies."""
    if not artifacts.list_runs(Season.CURRENT, 'fpl'):
        pytest.skip('No fpl runs')
    for horizon in (1, 5):
        body = client.get('/api/board', params={
            'game': 'fpl', 'horizons': '1,3,5', 'sort_horizon': horizon}).json()
        assert body['sort_horizon'] == horizon
        points = [row['horizon_points'][str(horizon)] for row in body['players']]
        assert points == sorted(points, reverse=True)
        assert [row['rank'] for row in body['players'][:5]] == [1, 2, 3, 4, 5]


def test_the_draft_board_keeps_ranking_on_vorp(client, draft_run_id):
    """VORP is priced over the whole run, so a horizon must not silently reorder the draft board."""
    body = client.get('/api/board',
                      params={'game': 'draft', 'run_id': draft_run_id, 'horizons': '1,3'}).json()
    assert body['horizons'] == [1, 3]
    vorp = [row['vorp'] for row in body['players']]
    assert vorp == sorted(vorp, reverse=True)


def test_a_board_horizon_past_the_run_is_a_400(client, draft_run_id):
    run = artifacts.load_run(Season.CURRENT, 'draft', draft_run_id)
    too_long = run['gameweek_to'] - run['gameweek_from'] + 2
    if too_long > 10:
        pytest.skip('This run is already as long as the horizon limit allows')
    response = client.get('/api/board', params={
        'game': 'draft', 'run_id': draft_run_id, 'horizons': str(too_long)})
    assert response.status_code == 400
    assert 'src.fpl.project' in response.json()['detail'], 'say how to fix it'


def test_config_reports_the_connected_fpl_team_or_that_there_is_none(client):
    """Both are correct states. What must not happen is a squad appearing from nowhere."""
    entry = client.get('/api/config').json()['fpl_entry']
    if entry is None:
        return
    assert entry['entry_id'] and entry['entry_name'], 'a connected team is named, not just numbered'
    if entry['squad'] is None:
        assert 'src.fpl.squad' in entry['note'], 'say how to fetch the picks'
        return
    assert len(entry['squad']['picks']) == 15
    assert entry['squad']['gameweek'] >= 1


def test_the_fpl_board_tags_our_squad_or_tags_nobody(client):
    """The squad filter's whole input. Every row carries the key, so absent never means 'mine'."""
    if not artifacts.list_runs(Season.CURRENT, 'fpl'):
        pytest.skip('No fpl runs')
    body = client.get('/api/board', params={'game': 'fpl', 'horizons': '1,3'}).json()
    assert all('squad' in row for row in body['players']), 'explicitly None, never absent'
    squad = body['squad']
    if squad is None:
        assert all(row['squad'] is None for row in body['players'])
        return

    mine = [row for row in body['players'] if row['squad']]
    assert len(mine) == squad['size'] - len(squad['missing'])
    assert sum(1 for row in mine if row['squad']['is_bench']) <= 4
    assert sum(1 for row in mine if row['squad']['is_captain']) <= 1
    slots = sorted(row['squad']['slot'] for row in mine)
    assert len(set(slots)) == len(slots), 'two players cannot share a slot'
    if squad['points'] is not None:
        # The best legal XI, so eleven players' worth - never all fifteen, never one player's.
        assert squad['points']['1'] > 0
        assert squad['points']['3'] >= squad['points']['1']


def test_the_draft_board_has_no_fpl_squad_block(client, draft_run_id):
    """Different game, different squad. The classic fifteen must not leak onto the draft board."""
    body = client.get('/api/board', params={'game': 'draft', 'run_id': draft_run_id}).json()
    assert body['squad'] is None
    assert all('squad' not in row for row in body['players'])


def test_player_history_carries_what_happened_in_each_match(client, draft_run_id):
    """The panel's game log: enough per match to see why a projection looks the way it does."""
    board = client.get('/api/board', params={'game': 'draft', 'run_id': draft_run_id}).json()
    player_id = board['players'][0]['player_id']
    body = client.get('/api/player',
                      params={'player_id': player_id, 'game': 'draft', 'run_id': draft_run_id}).json()
    if not body['history']:
        pytest.skip('No stored match history for the top player')
    for match in body['history']:
        assert {'minutes', 'points', 'goals_conceded', 'defensive_actions', 'bonus',
                'team_score', 'opponent_score'} <= set(match)
        # A scoreline is attached only where the fixture id can be trusted: fixture ids are
        # season-scoped, so last season's would resolve against this season's fixture list.
        if match['season'] != Season.CURRENT:
            assert match['team_score'] is None
    current = [m for m in body['history'] if m['season'] == Season.CURRENT]
    for match in current:
        assert match['team_score'] is not None, 'a finished match this season has a score'
