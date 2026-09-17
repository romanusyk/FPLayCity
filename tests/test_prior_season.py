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
    return {"id": 105, "code": 190000, "web_name": "Anthony", "team": 4,
            **TOTALS, **EXPECTED, **overrides}


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


class TestInSeasonPath:
    """Once a gameweek is played the bootstrap totals are *this* season's, so nothing reconciles.

    Discovered on 2026-08-26 by refreshing the snapshots after GW1: every player who had kicked a
    ball failed the reconciliation, Raya reading as "90 minutes, 1 start" against last season's
    3,330. The pre-kickoff capture is the answer, and `history_past` fills its gaps.
    """

    def test_history_only_build_keeps_the_totals_and_says_where_they_came_from(self):
        from src.fpl.loader.convert import prior_season_from_history

        result = prior_season_from_history(
            element_row={"id": 611, "web_name": "Wan-Bissaka", "team": 4, "code": 9999,
                         "minutes": 90, "starts": 1},
            history_past_rows=[make_history_row()],
            season=Season.s2526,
            fpl_season_name="2025/26",
            team="BRE",
            prior_team="WHU",
        )
        assert result.source is PriorSeasonSource.HISTORY_PAST_ONLY
        assert result.minutes == TOTALS["minutes"], 'the in-season bootstrap minutes are ignored'
        assert result.is_new_club is True

    def test_history_only_build_returns_none_without_a_record(self):
        """Absence stays absence: it is not a zero-filled row."""
        from src.fpl.loader.convert import prior_season_from_history

        assert prior_season_from_history(
            element_row={"id": 700, "web_name": "Academy", "team": 4, "code": 8888, "minutes": 0},
            history_past_rows=[make_history_row(season_name="2024/25")],
            season=Season.s2526,
            fpl_season_name="2025/26",
            team="BRE",
            prior_team=None,
        ) is None

    def test_top_up_only_adds_players_the_baseline_is_missing(self):
        from src.fpl.loader.baseline import top_up_prior_season_from_history
        from src.fpl.models.immutable import PlayerSeasons

        PlayerSeasons.clear()
        PlayerSeasons.add(build(make_element(), [make_history_row()]))
        already = make_element()
        late = make_element(id=611, code=190001, web_name="Wan-Bissaka")
        summaries = {
            str(already["id"]): {"history_past": [make_history_row()]},
            str(late["id"]): {"history_past": [make_history_row()]},
        }
        added = top_up_prior_season_from_history(
            element_rows=[already, late],
            team_rows=[{"id": 4, "short_name": "BRE"}],
            season=Season.CURRENT,
            player_summaries=summaries,
        )
        assert added == ["Wan-Bissaka"]
        assert len(PlayerSeasons.get_list(season=Season.s2526)) == 2
        PlayerSeasons.clear()

    def test_no_snapshot_after_kickoff_says_what_actually_went_wrong(self, tmp_path, monkeypatch):
        """Not the per-player reconciliation error, which points at the wrong thing entirely."""
        from src.fpl.loader.load import _restore_or_build_prior_season

        monkeypatch.chdir(tmp_path)
        with pytest.raises(ValueError, match='before the first kickoff'):
            _restore_or_build_prior_season(
                season=Season.CURRENT,
                events=[{'id': 1, 'finished': True}, {'id': 2, 'finished': False}],
                element_rows=[make_element()],
                player_summaries={},
                team_rows=[{"id": 4, "short_name": "BRE"}],
            )
