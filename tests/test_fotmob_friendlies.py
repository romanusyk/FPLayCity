"""Unit tests for friendly classification and weighted squad roles.

Pre-season is the only time we have data at all, and all of it is friendlies. These tests pin
how that evidence is treated: kept, down-weighted for selection, full-strength for availability.
"""
import pytest

from src.fpl.projection.preseason import PreseasonRole
from src.fotmob.models.fotmob import (
    FotmobPlayer,
    FotmobTeam,
    MatchDetails,
    MatchKind,
    classify_match_kind,
)
from src.fotmob.models.fotmob_metadata import (
    SEASON_TEAMS,
    teams_for_season,
    validate_against_fpl,
)
from src.fotmob.rotation.rotation_config import RotationConfig
from src.fotmob.rotation.rotation_view import (
    PlayerAppearance,
    PlayerAppearanceStatus,
    PlayerSquadRole,
)
from src.fpl.loader.utils import Season


def make_match(kind: MatchKind, match_id: int = 1) -> MatchDetails:
    league = "Club Friendlies" if kind is MatchKind.FRIENDLY else "Premier League"
    return MatchDetails(
        match_id=match_id,
        event_time="2026-08-01T15:00:00+00:00",
        opponent_team=FotmobTeam(id=999, name="Opponent"),
        starters=[], benched=[], unavailable=[], subs_log=[],
        league_name=league,
        kind=kind,
    )


def role(*appearances: PlayerAppearance, threshold: float = 0.8) -> PlayerSquadRole:
    return PlayerSquadRole(fotmob_player_id=1, appearances=list(appearances), first_team_threshold=threshold)


def appearance(status: PlayerAppearanceStatus, kind: MatchKind, weight: float) -> PlayerAppearance:
    return PlayerAppearance(fotmob_player_id=1, status=status, match=make_match(kind), weight=weight)


class TestClassification:
    @pytest.mark.parametrize("league_name", ["Club Friendlies", "Premier League Summer Series"])
    def test_friendly_league_names(self, league_name):
        assert classify_match_kind(league_name) is MatchKind.FRIENDLY

    @pytest.mark.parametrize(
        "league_name",
        ["Premier League", "EFL Cup", "FA Cup", "Champions League", "Community Shield", "UEFA Super Cup"],
    )
    def test_competitive_league_names(self, league_name):
        assert classify_match_kind(league_name) is MatchKind.COMPETITIVE

    def test_unknown_competition_defaults_to_competitive(self):
        assert classify_match_kind("Some New Cup") is MatchKind.COMPETITIVE


class TestWeights:
    def test_defaults(self):
        config = RotationConfig()
        assert config.weight_for(MatchKind.COMPETITIVE) == 1.0
        assert config.weight_for(MatchKind.FRIENDLY) == 0.35

    def test_missing_weight_raises_rather_than_defaulting_to_zero(self):
        config = RotationConfig(match_kind_weights={MatchKind.COMPETITIVE: 1.0})
        with pytest.raises(KeyError, match="No weight configured"):
            config.weight_for(MatchKind.FRIENDLY)

    def test_friendlies_can_be_excluded_entirely(self):
        config = RotationConfig(match_kind_weights={MatchKind.COMPETITIVE: 1.0, MatchKind.FRIENDLY: 0.0})
        assert config.weight_for(MatchKind.FRIENDLY) == 0.0


class TestWeightedSquadRole:
    def test_friendly_starts_count_less_than_competitive(self):
        friendly_only = role(*[appearance(PlayerAppearanceStatus.STARTED, MatchKind.FRIENDLY, 0.35)] * 3)
        competitive = role(*[appearance(PlayerAppearanceStatus.STARTED, MatchKind.COMPETITIVE, 1.0)] * 3)
        assert friendly_only.weighted_starts < competitive.weighted_starts
        assert friendly_only.starts == competitive.starts == 3

    def test_start_ratio_is_weighted(self):
        """One competitive start outweighs one friendly benching."""
        squad_role = role(
            appearance(PlayerAppearanceStatus.STARTED, MatchKind.COMPETITIVE, 1.0),
            appearance(PlayerAppearanceStatus.BENCHED, MatchKind.FRIENDLY, 0.35),
        )
        assert squad_role.start_ratio == pytest.approx(1.0 / 1.35)
        assert squad_role.raw_start_ratio == pytest.approx(0.5)

    def test_competitive_and_friendly_starts_are_reported_separately(self):
        squad_role = role(
            appearance(PlayerAppearanceStatus.STARTED, MatchKind.COMPETITIVE, 1.0),
            appearance(PlayerAppearanceStatus.STARTED, MatchKind.FRIENDLY, 0.35),
            appearance(PlayerAppearanceStatus.STARTED, MatchKind.FRIENDLY, 0.35),
        )
        assert squad_role.competitive_starts == 1
        assert squad_role.friendly_starts == 2

    def test_unavailability_is_not_weighted_down(self):
        """An injury in a friendly is exactly as informative as one in a league game."""
        squad_role = role(
            appearance(PlayerAppearanceStatus.UNAVAILABLE, MatchKind.FRIENDLY, 1.0),
            appearance(PlayerAppearanceStatus.UNAVAILABLE, MatchKind.FRIENDLY, 1.0),
        )
        assert squad_role.unavailable == 2

    def test_friendly_only_evidence_is_flagged(self):
        squad_role = role(*[appearance(PlayerAppearanceStatus.STARTED, MatchKind.FRIENDLY, 0.35)] * 4)
        assert squad_role.evidence_is_friendly_only is True
        assert squad_role.is_first_team is True  # provisional - caller must check the flag

    def test_no_appearances_is_not_flagged(self):
        assert role().evidence_is_friendly_only is False
        assert role().start_ratio == 0.0


class TestLineuplessFriendly:
    def test_lineup_flag_defaults_true(self):
        assert make_match(MatchKind.FRIENDLY).lineup_available is True

    def test_lineup_less_friendly_is_representable(self):
        match = make_match(MatchKind.FRIENDLY).model_copy(update={"lineup_available": False})
        assert match.is_friendly and not match.lineup_available
        assert match.starters == []


class TestSeasonRosters:
    def test_each_season_has_twenty_clubs(self):
        for season in SEASON_TEAMS:
            assert len(teams_for_season(season)) == 20, season

    def test_promotion_and_relegation_between_seasons(self):
        old = set(SEASON_TEAMS[Season.s2526])
        new = set(SEASON_TEAMS[Season.s2627])
        assert old - new == {"Burnley", "Westham", "Wolves"}
        assert new - old == {"Coventry", "Hull", "Ipswich"}

    def test_unknown_season_raises(self):
        with pytest.raises(KeyError, match="No FotMob team roster declared"):
            teams_for_season("1999-2000")

    def test_validate_against_fpl_rejects_a_stale_roster(self):
        fpl_teams = [{"short_name": name} for name in ("ARS", "AVL")]
        with pytest.raises(ValueError, match="disagrees with the FPL bootstrap"):
            validate_against_fpl(fpl_teams, Season.s2627)


class TestPreseasonRoleWeighting:
    """A competitive pre-season fixture has to outrank a handful of friendlies.

    The case that forced this: Arsenal played four friendlies and the Community Shield before
    GW1. Kepa started all four friendlies, David Raya started the Shield. At the old 0.35
    friendly weight the friendlies won and the model had Arsenal's reserve keeper ahead of its
    first choice.
    """

    @staticmethod
    def role(starts, competitive_starts, friendlies, competitive, friendly_weight=0.20):
        team_weight = friendlies * friendly_weight + competitive * 1.0
        weighted = (starts - competitive_starts) * friendly_weight + competitive_starts * 1.0
        return PreseasonRole(
            player_id=1,
            team_matches=friendlies + competitive,
            team_weight=team_weight,
            matches=starts,
            starts=starts,
            competitive_starts=competitive_starts,
            weighted_starts=weighted,
            benched=0,
            unavailable=0,
        )

    def test_one_competitive_start_beats_four_friendly_starts(self):
        raya = self.role(starts=1, competitive_starts=1, friendlies=4, competitive=1)
        kepa = self.role(starts=4, competitive_starts=0, friendlies=4, competitive=1)
        assert raya.start_share > kepa.start_share

    def test_at_the_old_weight_the_friendlies_would_have_won(self):
        """Documents the regression, so nobody quietly restores 0.35 here."""
        raya = self.role(starts=1, competitive_starts=1, friendlies=4, competitive=1, friendly_weight=0.35)
        kepa = self.role(starts=4, competitive_starts=0, friendlies=4, competitive=1, friendly_weight=0.35)
        assert raya.start_share < kepa.start_share

    def test_starting_everything_is_a_full_share(self):
        role = self.role(starts=5, competitive_starts=1, friendlies=4, competitive=1)
        assert role.start_share == pytest.approx(1.0)

    def test_starting_nothing_is_zero_but_still_evidence(self):
        role = self.role(starts=0, competitive_starts=0, friendlies=4, competitive=1)
        assert role.start_share == 0.0
        assert role.has_evidence is True

    def test_a_club_with_no_stored_matches_has_no_evidence(self):
        role = self.role(starts=0, competitive_starts=0, friendlies=0, competitive=0)
        assert role.has_evidence is False
        assert role.start_share == 0.0

    def test_share_is_capped_for_a_mid_window_transfer(self):
        """Starts made for a bigger old club can exceed the new club's own fixture weight."""
        role = self.role(starts=6, competitive_starts=2, friendlies=2, competitive=0)
        assert role.start_share == 1.0
