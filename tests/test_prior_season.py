"""Unit tests for the prior-season baseline reconciliation.

These run offline against hand-built payloads. They pin the invariant discovered on
2026-08-15: bootstrap mirrors `history_past` exactly for players who stayed at a club, and is
unreliable (zeroed or truncated) for players who moved.
"""
import pytest

from src.fpl.loader.convert import prior_season_to_player_season
from src.fpl.loader.utils import Season
from src.fpl.models.immutable import PriorSeasonSource


TOTALS = dict(
    minutes=2717, starts=32, total_points=127, goals_scored=8, assists=4, clean_sheets=4,
    goals_conceded=50, own_goals=0, penalties_saved=0, penalties_missed=0, yellow_cards=5,
    red_cards=0, saves=0, bonus=6, bps=430, defensive_contribution=232,
)
EXPECTED = dict(
    expected_goals="5.10", expected_assists="2.86",
    expected_goal_involvements="7.96", expected_goals_conceded="48.20",
)


def make_history_row(**overrides) -> dict:
    return {"season_name": "2025/26", **TOTALS, **EXPECTED, **overrides}


def make_element(**overrides) -> dict:
    return {"id": 105, "web_name": "Anthony", "team": 4, **TOTALS, **EXPECTED, **overrides}


def build(element, history_rows, team="BRE", prior_team="BRE"):
    return prior_season_to_player_season(
        element_row=element,
        history_past_rows=history_rows,
        season=Season.s2526,
        fpl_season_name="2025/26",
        team=team,
        prior_team=prior_team,
    )


class TestAgreement:
    """Bootstrap and history_past agree - the common case."""

    def test_uses_bootstrap_source(self):
        result = build(make_element(), [make_history_row()])
        assert result.source is PriorSeasonSource.BOOTSTRAP
        assert result.minutes == 2717
        assert result.total_points == 127
        assert result.is_new_club is False

    def test_zero_minute_player_marked_history_past(self):
        zeros = {field: 0 for field in TOTALS}
        result = build(make_element(**zeros), [make_history_row(**zeros)])
        assert result.source is PriorSeasonSource.HISTORY_PAST
        assert result.minutes == 0


class TestClubChangeRepairs:
    """Bootstrap is unreliable for players who moved; history_past wins."""

    def test_zeroed_bootstrap_is_recovered(self):
        """The Jaidon Anthony case: a full season reads as zero in bootstrap."""
        zeroed = make_element(minutes=0, starts=0, total_points=0, goals_scored=0, assists=0)
        result = build(zeroed, [make_history_row()], team="BRE", prior_team="BUR")

        assert result.source is PriorSeasonSource.RECOVERED_FROM_HISTORY
        assert result.minutes == 2717
        assert result.total_points == 127
        assert result.is_new_club is True

    def test_truncated_bootstrap_is_corrected(self):
        """The Joao Gomes case: bootstrap holds a partial total."""
        partial = make_element(minutes=2207, starts=25, total_points=85)
        result = build(partial, [make_history_row()], team="AVL", prior_team="WOL")

        assert result.source is PriorSeasonSource.PARTIAL_IN_BOOTSTRAP
        assert result.minutes == 2717
        assert result.starts == 32


class TestFailsLoudly:
    """Anything unexplained must stop the load rather than corrupt the baseline."""

    def test_divergence_without_club_change_raises(self):
        diverging = make_element(minutes=2207)
        with pytest.raises(ValueError, match="did not change club"):
            build(diverging, [make_history_row()], team="BRE", prior_team="BRE")

    def test_duplicate_season_rows_raise(self):
        with pytest.raises(ValueError, match="rows in history_past"):
            build(make_element(), [make_history_row(), make_history_row()])

    def test_minutes_without_history_row_raises(self):
        with pytest.raises(ValueError, match="no '2025/26' row in history_past"):
            build(make_element(), [])


class TestAbsence:
    """A player with no prior Premier League season yields no row at all."""

    def test_returns_none_rather_than_zeros(self):
        newcomer = make_element(**{field: 0 for field in TOTALS})
        assert build(newcomer, []) is None

    def test_unknown_prior_club_does_not_raise(self):
        """With no prior-season snapshot we cannot prove a club change, so we accept history."""
        zeroed = make_element(minutes=0, starts=0, total_points=0)
        result = build(zeroed, [make_history_row()], prior_team=None)
        assert result.source is PriorSeasonSource.RECOVERED_FROM_HISTORY
        assert result.is_new_club is False


class TestDerivedRates:
    def test_per_90_rates(self):
        result = build(make_element(), [make_history_row()])
        assert result.nineties == pytest.approx(2717 / 90)
        assert result.points_per_90 == pytest.approx(127 / (2717 / 90))
        assert result.defensive_contribution_per_90 == pytest.approx(232 / (2717 / 90))

    def test_per_90_is_zero_without_minutes(self):
        zeros = {field: 0 for field in TOTALS}
        result = build(make_element(**zeros), [make_history_row(**zeros)])
        assert result.points_per_90 == 0.0
