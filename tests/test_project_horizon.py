"""The projection horizon defaults to the gameweeks still to come.

`_with_horizon` in `src/fpl/project.py` is the fix for a bug that survived three weekly refreshes:
the method registry carries `gameweek_from=1`, so every run generated after GW1 summed gameweeks
that had already been played, reported `played_gameweeks=0` (switching off the `current_season_ramp`
that `v4-current-form` exists for), and read role evidence as if no match had been played.

What is pinned here is the defaulting, not the model: an explicit `--gw-from`/`--gw-to` still wins,
pre-season behaviour is unchanged, and an empty range raises rather than writing a plausible-looking
artifact.
"""
import pytest

from src.fpl.loader.utils import MAX_GAMEWEEK
from src.fpl.project import _with_horizon
from src.fpl.projection.methods import DEFAULT_METHOD, method as lookup_method


@pytest.fixture
def baseline():
    return lookup_method(DEFAULT_METHOD)


@pytest.fixture
def next_gameweek(monkeypatch):
    """Pin the next gameweek so the tests do not depend on a stored fixtures snapshot."""
    def _set(gameweek):
        monkeypatch.setattr('src.fpl.project.resolve_next_gameweek', lambda season: gameweek)
    return _set


def test_defaults_to_the_next_gameweek(baseline, next_gameweek):
    next_gameweek(4)
    params = _with_horizon(baseline, None, None, '2026-2027').params
    assert (params.gameweek_from, params.gameweek_to) == (4, 13)


def test_keeps_the_methods_horizon_length(baseline, next_gameweek):
    """A later start must not shorten the run - that was the manual `--gw-to` fix each week."""
    next_gameweek(9)
    params = _with_horizon(baseline, None, None, '2026-2027').params
    assert params.horizon == baseline.params.horizon


def test_is_inert_before_the_season_starts(baseline, next_gameweek):
    """Pre-season the next gameweek is 1, so every stored comparison keeps its meaning."""
    next_gameweek(1)
    params = _with_horizon(baseline, None, None, '2026-2027').params
    assert (params.gameweek_from, params.gameweek_to) == (
        baseline.params.gameweek_from, baseline.params.gameweek_to,
    )


def test_explicit_overrides_win(baseline, next_gameweek):
    next_gameweek(4)
    params = _with_horizon(baseline, 2, 6, '2026-2027').params
    assert (params.gameweek_from, params.gameweek_to) == (2, 6)


def test_explicit_start_still_gets_a_full_horizon(baseline, next_gameweek):
    next_gameweek(4)
    params = _with_horizon(baseline, 20, None, '2026-2027').params
    assert (params.gameweek_from, params.gameweek_to) == (20, 29)


def test_clamps_to_the_last_gameweek_of_the_season(baseline, next_gameweek):
    """Beyond GW38 there are no fixtures, so a full horizon would project nothing."""
    next_gameweek(35)
    params = _with_horizon(baseline, None, None, '2026-2027').params
    assert (params.gameweek_from, params.gameweek_to) == (35, MAX_GAMEWEEK)


def test_empty_range_raises(baseline, next_gameweek):
    next_gameweek(4)
    with pytest.raises(SystemExit, match='Empty horizon'):
        _with_horizon(baseline, 10, 4, '2026-2027')


def test_method_identity_is_preserved(baseline, next_gameweek):
    """Only the horizon moves: a renamed or re-tuned method would invalidate stored comparisons."""
    next_gameweek(4)
    pinned = _with_horizon(baseline, None, None, '2026-2027')
    assert pinned.name == baseline.name
    assert pinned.notes == baseline.notes
    unchanged = {
        field: getattr(pinned.params, field)
        for field in vars(pinned.params)
        if field not in ('gameweek_from', 'gameweek_to')
    }
    assert unchanged == {
        field: getattr(baseline.params, field)
        for field in vars(baseline.params)
        if field not in ('gameweek_from', 'gameweek_to')
    }
