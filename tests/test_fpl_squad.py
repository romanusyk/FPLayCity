"""Our classic-FPL team: the id, and the fifteen players read back from a stored snapshot.

The invariant worth a file of its own is the one that already went wrong. `FplManager.ME =
2486591` sat in source from December 2025 and, checked against the live API on 2026-08-26, that
entry belongs to somebody else - so every "my players" answer since had been about a stranger's
squad, silently. A permanent id makes that worse rather than better: it is a *stable* wrong
answer, and nothing ever breaks to reveal it.

So the tests here are mostly about refusing to guess: no id means an error naming the command,
and a payload that is not a legal fifteen-player squad raises rather than filtering a board with
fourteen.
"""
import json
import os

import pytest

from src.fpl.loader.fpl_squad import (
    SQUAD_SIZE,
    FplEntryConfig,
    config_path,
    load_config,
    load_config_or_none,
    load_squad,
    picks_store,
    save_config,
    stored_gameweeks,
)


SEASON = '2026-2027'

SHAPE = (
    [('GKP', 1)] * 2 + [('DEF', 2)] * 5 + [('MID', 3)] * 5 + [('FWD', 4)] * 3
)
"""2/5/5/3 as (name, element_type) pairs, in slot order: keeper, defenders, midfielders, forwards.

Not the order a real payload uses - a real one interleaves the bench at slots 12-15 - which is
exactly why `a_payload` takes a bench argument.
"""


def a_payload(bench=(11, 12, 13, 14), captain_slot: int = 1, active_chip=None) -> dict:
    """A picks payload with fifteen players, 2/5/5/3, and `bench` at the back.

    `bench` indexes into SHAPE, so the caller decides which four sit out - and the resulting slots
    are 12-15, as FPL numbers them.
    """
    order = [index for index in range(SQUAD_SIZE) if index not in bench] + list(bench)
    picks = []
    for slot, index in enumerate(order, start=1):
        _, element_type = SHAPE[index]
        picks.append({
            'element': 100 + index,
            'element_type': element_type,
            'position': slot,
            'multiplier': 0 if slot > 11 else (2 if slot == captain_slot else 1),
            'is_captain': slot == captain_slot,
            'is_vice_captain': slot == captain_slot + 1,
        })
    return {'active_chip': active_chip, 'automatic_subs': [], 'picks': picks}


def a_config(entry_id: int = 4242) -> FplEntryConfig:
    return FplEntryConfig(entry_id=entry_id, entry_name='Team Us', manager='A Manager',
                          connected_at='2026-08-26T21:00:00')


def store_picks(entry_id: int, gameweek: int, body: dict, timestamp: str = '2026-08-26T16:45:45'):
    """Write a picks snapshot where `load_squad` will look for it."""
    store = picks_store(SEASON, entry_id, gameweek)
    path = f"{store.base_path}_{timestamp}.json"
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as handle:
        json.dump(body, handle)
    return path


class TestConfig:

    def test_it_round_trips(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        save_config(a_config())
        assert load_config() == a_config()

    def test_it_is_not_season_scoped(self, tmp_path, monkeypatch):
        """Unlike a draft league. Classic entry ids really are permanent; only picks are seasonal."""
        monkeypatch.chdir(tmp_path)
        assert config_path() == os.path.join('data', 'fpl_entry.json')

    def test_a_missing_config_names_the_command_that_creates_it(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        with pytest.raises(FileNotFoundError, match='src.fpl.squad --entry'):
            load_config()

    def test_a_missing_config_reads_as_absent_only_where_that_is_asked_for(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        assert load_config_or_none() is None

    def test_a_corrupt_config_raises_rather_than_reading_as_absent(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        os.makedirs('data', exist_ok=True)
        with open(config_path(), 'w', encoding='utf-8') as handle:
            handle.write('{"entry_id": "not a number"}')
        with pytest.raises(ValueError, match='not a readable'):
            load_config()
        # And it stays an error on the lenient path too: a typo must not read as "no team".
        with pytest.raises(ValueError, match='not a readable'):
            load_config_or_none()

    def test_the_names_are_stored_so_a_wrong_id_is_legible(self, tmp_path, monkeypatch):
        """The whole defence against the bug this module was written for."""
        monkeypatch.chdir(tmp_path)
        save_config(FplEntryConfig(entry_id=2486591, entry_name='Reddevil', manager='Yousif Ali',
                                   connected_at='2026-08-26T21:00:00'))
        stored = load_config()
        assert stored.entry_name == 'Reddevil' and stored.manager == 'Yousif Ali'


class TestLoadSquad:

    def test_it_reads_the_fifteen_with_slots_and_the_armband(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        save_config(a_config())
        store_picks(4242, 1, a_payload())
        squad = load_squad(SEASON)

        assert squad.gameweek == 1 and len(squad.picks) == SQUAD_SIZE
        assert [pick.slot for pick in squad.picks] == list(range(1, 16)), 'in slot order'
        assert sum(1 for pick in squad.picks if pick.is_bench) == 4
        assert [pick.slot for pick in squad.picks if pick.is_bench] == [12, 13, 14, 15]
        captain = [pick for pick in squad.picks if pick.is_captain]
        assert len(captain) == 1 and captain[0].multiplier == 2
        assert squad.entry_name == 'Team Us', 'the name comes from the config, not the payload'

    def test_the_default_gameweek_is_the_latest_stored(self, tmp_path, monkeypatch):
        """FPL publishes a squad only after its deadline, so the newest on disk is the freshest
        that exists anywhere."""
        monkeypatch.chdir(tmp_path)
        save_config(a_config())
        store_picks(4242, 1, a_payload())
        store_picks(4242, 2, a_payload(bench=(0, 2, 3, 4)))
        assert stored_gameweeks(SEASON, 4242) == [1, 2]
        assert load_squad(SEASON).gameweek == 2
        assert load_squad(SEASON, gameweek=1).gameweek == 1

    def test_a_squad_and_a_bench_are_distinguishable(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        save_config(a_config())
        store_picks(4242, 1, a_payload(bench=(1, 2, 3, 4)))
        squad = load_squad(SEASON)
        benched = {pick.element for pick in squad.picks if pick.is_bench}
        assert benched == {101, 102, 103, 104}
        assert squad.pick(101).is_bench and not squad.pick(100).is_bench
        assert squad.pick(999) is None, 'a player we do not own'

    def test_nothing_stored_names_the_command_and_says_why_it_may_be_empty(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        save_config(a_config())
        with pytest.raises(FileNotFoundError, match='src.fpl.squad --refresh'):
            load_squad(SEASON)

    def test_a_named_gameweek_that_is_not_stored_lists_what_is(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        save_config(a_config())
        store_picks(4242, 1, a_payload())
        with pytest.raises(FileNotFoundError, match=r'Stored gameweeks: \[1\]'):
            load_squad(SEASON, gameweek=3)


class TestSquadValidation:
    """Every one of these would otherwise be a board filtered by the wrong set of players."""

    def stored(self, tmp_path, monkeypatch, body):
        monkeypatch.chdir(tmp_path)
        save_config(a_config())
        store_picks(4242, 1, body)

    def test_a_short_squad_is_refused(self, tmp_path, monkeypatch):
        body = a_payload()
        body['picks'] = body['picks'][:14]
        self.stored(tmp_path, monkeypatch, body)
        with pytest.raises(ValueError, match='holds 14 players'):
            load_squad(SEASON)

    def test_duplicate_slots_are_refused(self, tmp_path, monkeypatch):
        body = a_payload()
        body['picks'][3]['position'] = body['picks'][2]['position']
        self.stored(tmp_path, monkeypatch, body)
        with pytest.raises(ValueError, match='expected 1-15'):
            load_squad(SEASON)

    def test_the_same_player_twice_is_refused(self, tmp_path, monkeypatch):
        body = a_payload()
        body['picks'][3]['element'] = body['picks'][2]['element']
        self.stored(tmp_path, monkeypatch, body)
        with pytest.raises(ValueError, match='same player twice'):
            load_squad(SEASON)

    def test_a_shape_that_is_not_2_5_5_3_is_refused(self, tmp_path, monkeypatch):
        body = a_payload()
        body['picks'][0]['element_type'] = 2  # a third defender's worth of keepers
        self.stored(tmp_path, monkeypatch, body)
        with pytest.raises(ValueError, match='expected'):
            load_squad(SEASON)

    def test_an_unreadable_pick_names_the_row(self, tmp_path, monkeypatch):
        body = a_payload()
        del body['picks'][5]['multiplier']
        self.stored(tmp_path, monkeypatch, body)
        with pytest.raises(ValueError, match='unreadable pick'):
            load_squad(SEASON)

    def test_a_payload_that_is_not_picks_at_all_is_refused(self, tmp_path, monkeypatch):
        self.stored(tmp_path, monkeypatch, {'detail': 'Not found.'})
        with pytest.raises(ValueError, match="no 'picks' list"):
            load_squad(SEASON)
