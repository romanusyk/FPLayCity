"""Who owns whom in a draft league, and the season-scoped ids behind it.

The invariant worth a test file of its own is the one that has already cost us: **draft entry
and league ids are re-issued every season**. Checked against the live API on 2026-08-26, the
entry hardcoded as ours for all of 2025/26 now serves a stranger's team. So the ids are stored
per season and a config read under the wrong season raises rather than valuing the wrong squad.

The waiver arithmetic built on top of this lives in `tests/test_waivers.py`.
"""
import json
import os

import pytest

from src.fpl.loader.draft_league import (
    AVAILABLE,
    LOCKED,
    LeagueConfig,
    build_ownership,
    config_path,
    load_config,
    save_config,
)


def payloads(my_entry_id: int = 1, entries=(1, 2), owned_per_entry: int = 15,
             free: tuple[int, ...] = (900, 901), locked: tuple[int, ...] = ()):
    entry_rows = [
        {'entry_id': entry_id, 'entry_name': f'Team {entry_id}', 'player_first_name': 'First',
         'player_last_name': f'Last{entry_id}', 'waiver_pick': entry_id}
        for entry_id in entries
    ]
    status_rows = []
    element = 1
    for entry_id in entries:
        for _ in range(owned_per_entry):
            status_rows.append({'element': element, 'owner': entry_id, 'status': 'o',
                                'in_accepted_trade': False})
            element += 1
    for free_element in free:
        status_rows.append({
            'element': free_element, 'owner': None,
            'status': LOCKED if free_element in locked else AVAILABLE, 'in_accepted_trade': False,
        })
    details = {
        'league': {'id': 4242, 'name': 'Sunday League', 'transaction_mode': 'waivers'},
        'league_entries': entry_rows,
    }
    return details, {'element_status': status_rows}, my_entry_id


def an_ownership(**kwargs):
    details, element_status, my_entry_id = payloads(**kwargs)
    return build_ownership(
        season='2026-2027', my_entry_id=my_entry_id, details=details,
        element_status=element_status, fetched_at='2026-08-26T12:00:00',
    )


class TestBuildOwnership:

    def test_our_squad_is_the_players_our_entry_holds(self):
        ownership = an_ownership()
        assert ownership.my_entry.entry_id == 1
        assert ownership.my_squad == set(range(1, 16))
        assert ownership.squad_of(2) == set(range(16, 31))

    def test_an_entry_that_is_not_ours_is_refused_with_the_alternatives(self):
        """The failure a stale id produces, made loud."""
        details, element_status, _ = payloads()
        with pytest.raises(ValueError, match='re-issued every season'):
            build_ownership(season='2026-2027', my_entry_id=52242, details=details,
                            element_status=element_status, fetched_at='now')

    def test_a_squad_that_is_not_fifteen_players_is_refused(self):
        with pytest.raises(ValueError, match='does not add up'):
            an_ownership(owned_per_entry=14)

    def test_an_entry_holding_nobody_is_refused(self):
        details, element_status, _ = payloads(entries=(1, 2))
        details['league_entries'].append({
            'entry_id': 3, 'entry_name': 'Ghost', 'player_first_name': 'No',
            'player_last_name': 'Squad', 'waiver_pick': 3,
        })
        with pytest.raises(ValueError, match='holding nobody'):
            build_ownership(season='2026-2027', my_entry_id=1, details=details,
                            element_status=element_status, fetched_at='now')

    def test_a_locked_player_is_free_but_not_claimable(self):
        """He shows on the site as unowned and cannot be signed until the next deadline."""
        ownership = an_ownership(locked=(901,))
        assert ownership.is_claimable(900) is True
        assert ownership.is_claimable(901) is False
        assert ownership.owner_label(901) == 'locked until the next waiver deadline'

    def test_an_owned_player_is_labelled_by_squad(self):
        ownership = an_ownership()
        assert ownership.owner_label(1) == 'you'
        assert ownership.owner_label(16) == 'Team 2'
        assert ownership.owner_label(900) == 'free agent'

    def test_an_unmodelled_status_is_counted_and_not_treated_as_claimable(self):
        details, element_status, _ = payloads()
        element_status['element_status'].append({'element': 999, 'owner': None, 'status': 'x',
                                                 'in_accepted_trade': False})
        ownership = build_ownership(season='2026-2027', my_entry_id=1, details=details,
                                   element_status=element_status, fetched_at='now')
        assert ownership.unknown_statuses == {'x': 1}
        assert ownership.is_claimable(999) is False


class TestLeagueConfig:

    def a_config(self, season: str = '2026-2027') -> LeagueConfig:
        return LeagueConfig(season=season, league_id=4242, entry_id=1, entry_name='Team 1',
                            manager='First Last1', league_name='Sunday League',
                            transaction_mode='waivers')

    def test_it_round_trips(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        save_config(self.a_config())
        assert load_config('2026-2027') == self.a_config()

    def test_a_missing_config_names_the_command_that_creates_it(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        with pytest.raises(FileNotFoundError, match='src.fpl.league --entry'):
            load_config('2026-2027')

    def test_ids_stored_for_another_season_are_refused(self, tmp_path, monkeypatch):
        """The whole reason this file is per season."""
        monkeypatch.chdir(tmp_path)
        save_config(self.a_config(season='2025-2026'))
        os.makedirs(os.path.dirname(config_path('2026-2027')), exist_ok=True)
        with open(config_path('2026-2027'), 'w', encoding='utf-8') as handle:
            json.dump(self.a_config(season='2025-2026').as_dict(), handle)
        with pytest.raises(ValueError, match='re-issued every season'):
            load_config('2026-2027')

    def test_a_corrupt_config_raises_rather_than_reading_as_absent(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        path = config_path('2026-2027')
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w', encoding='utf-8') as handle:
            handle.write('{not json')
        with pytest.raises(ValueError, match='not a readable draft league config'):
            load_config('2026-2027')


class TestDraftEntriesForPicks:
    """`bootstrap()` reads manager picks for whoever is in our league, and nobody otherwise."""

    def a_connected_league(self, season: str = '2026-2027'):
        from src.fpl.loader.draft_league import league_dir
        from src.fpl.loader.store import JsonSnapshotStore, SnapshotSpec

        details, element_status, my_entry_id = payloads()
        save_config(LeagueConfig(season=season, league_id=details['league']['id'],
                                 entry_id=my_entry_id, entry_name='Team 1', manager='First Last1',
                                 league_name=details['league']['name'], transaction_mode='waivers'))
        directory = league_dir(season, details['league']['id'])
        JsonSnapshotStore(SnapshotSpec(base_path=os.path.join(directory, 'details'))).write(details)
        JsonSnapshotStore(SnapshotSpec(base_path=os.path.join(directory, 'element_status'))).write(element_status)

    def test_it_yields_every_entry_with_ours_flagged(self, tmp_path, monkeypatch):
        from src.fpl.loader.load import _draft_entries

        monkeypatch.chdir(tmp_path)
        self.a_connected_league()
        assert sorted(_draft_entries('2026-2027')) == [(1, True), (2, False)]

    def test_no_connected_league_skips_loudly_rather_than_guessing(self, tmp_path, monkeypatch, caplog):
        """The old code hardcoded ids here, which silently fetched a stranger's squad."""
        from src.fpl.loader.load import _draft_entries

        monkeypatch.chdir(tmp_path)
        with caplog.at_level('WARNING'):
            assert _draft_entries('2026-2027') == []
        assert 'src.fpl.league --entry' in caplog.text
