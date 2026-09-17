"""Component models, tested on hand-built inputs.

These are offline: no snapshots, no network. The point is to pin the behaviour that is easy to
break silently - which way shrinkage pulls, what happens with no evidence at all, and whether
replacement level moves when the pool shrinks.
"""
import pytest

from src.fpl.models.immutable import (
    Player,
    PlayerSeason,
    PlayerType,
    PriorSeasonSource,
)
from src.fpl.projection.defensive import (
    DefensiveContributionModel,
    implied_hit_rate,
)
from src.fpl.projection.history import MatchRow, PlayerHistory
from src.fpl.projection.methods import METHODS, ProjectionParams, method
from src.fpl.projection.minutes import (
    LATERAL_MOVE_MULTIPLIER,
    MIN_MOVE_MULTIPLIER,
    PRESEASON_ROLE_KNOTS,
    MinutesModel,
    preseason_weight_for_prior,
    transfer_role_multiplier,
)
from src.fpl.projection.preseason import PreseasonRole
from src.fpl.projection.vorp import (
    DRAFT_SQUAD_SLOTS,
    replacement_levels,
    tier_breaks,
    value_over_replacement,
)


def make_match(actions: int, minutes: int = 90, started: bool = True, gameweek: int = 1) -> MatchRow:
    return MatchRow(
        season='2025-2026', gameweek=gameweek, fixture_id=gameweek, opponent='XXX',
        was_home=True, kickoff_time='2025-08-17T15:30:00Z', minutes=minutes,
        starts=1 if started else 0, total_points=2, goals_scored=0, assists=0, clean_sheets=0,
        goals_conceded=0, saves=0, defensive_contribution=actions, bonus=0, bps=0,
        yellow_cards=0, red_cards=0, penalties_saved=0, penalties_missed=0, own_goals=0,
        expected_goals=0.0, expected_assists=0.0, expected_goal_involvements=0.0,
        expected_goals_conceded=0.0,
    )


def history_of(actions: list[int]) -> PlayerHistory:
    return PlayerHistory(
        player_id=1, code=1,
        matches=[make_match(value, gameweek=index + 1) for index, value in enumerate(actions)],
    )


class TestDefensiveContribution:

    def test_shrinkage_pulls_an_outlier_toward_the_rate_implied_prior(self):
        """Two defenders on the same mean, very different hit rates - the classic case."""
        steady = history_of([10] * 20)          # clears every time, mean 10
        spiky = history_of([0, 20] * 10)        # same mean, clears half the time
        model = DefensiveContributionModel()

        steady_estimate = model.estimate(PlayerType.DEF, steady)
        spiky_estimate = model.estimate(PlayerType.DEF, spiky)

        assert steady_estimate.observed_hit_rate == 1.0
        assert spiky_estimate.observed_hit_rate == 0.5
        # Both are pulled toward the same rate-implied prior, so the gap narrows...
        assert steady_estimate.hit_rate < 1.0
        assert spiky_estimate.hit_rate > 0.5
        # ...but the observed difference survives, which is the whole point.
        assert steady_estimate.hit_rate > spiky_estimate.hit_rate

    def test_no_shrinkage_returns_the_raw_rate(self):
        raw = DefensiveContributionModel(shrinkage_starts=0.0)
        estimate = raw.estimate(PlayerType.DEF, history_of([10] * 20))
        assert estimate.hit_rate == 1.0
        assert estimate.shrinkage_weight == 1.0

    def test_small_sample_falls_back_to_the_rate_implied_prior(self):
        model = DefensiveContributionModel()
        estimate = model.estimate(PlayerType.DEF, history_of([12, 12]))
        assert estimate.observed_hit_rate is None, 'two starts is not an observed rate'
        assert estimate.hit_rate == estimate.implied_hit_rate
        assert estimate.shrinkage_weight == 0.0

    def test_no_starts_reports_zero_with_a_zero_sample(self):
        estimate = DefensiveContributionModel().estimate(PlayerType.MID, PlayerHistory(1, 1))
        assert estimate.hit_rate == 0.0
        assert estimate.starts == 0, 'callers must be able to see this is absence, not evidence'

    def test_goalkeepers_have_no_award(self):
        estimate = DefensiveContributionModel().estimate(PlayerType.GKP, history_of([15] * 20))
        assert estimate.has_award is False
        assert estimate.hit_rate == 0.0

    def test_implied_hit_rate_rises_with_the_rate(self):
        low = implied_hit_rate(10, actions_per_90=6.0, minutes_per_start=90)
        high = implied_hit_rate(10, actions_per_90=14.0, minutes_per_start=90)
        assert 0.0 < low < high < 1.0

    def test_implied_hit_rate_falls_when_a_player_is_substituted_early(self):
        """The minutes confound, made explicit: same rate, fewer minutes, fewer chances."""
        full = implied_hit_rate(10, actions_per_90=11.0, minutes_per_start=90)
        early = implied_hit_rate(10, actions_per_90=11.0, minutes_per_start=60)
        assert early < full

    def test_negative_shrinkage_is_rejected(self):
        with pytest.raises(ValueError, match='must be >= 0'):
            DefensiveContributionModel(shrinkage_starts=-1.0)


class TestTransferDiscount:
    """A prior-season start share describes the squad the player has just left."""

    def test_staying_costs_nothing(self):
        assert transfer_role_multiplier(moved=False, quality_ratio=1.0) == 1.0
        # Even a nonsense ratio is irrelevant when the player did not move.
        assert transfer_role_multiplier(moved=False, quality_ratio=0.2) == 1.0

    def test_a_lateral_move_costs_the_measured_amount(self):
        assert transfer_role_multiplier(moved=True, quality_ratio=1.0) == LATERAL_MOVE_MULTIPLIER

    def test_a_step_up_costs_more_than_a_lateral_move(self):
        step_up = transfer_role_multiplier(moved=True, quality_ratio=0.4)
        assert step_up < LATERAL_MOVE_MULTIPLIER
        # Nottingham -> Manchester City is roughly this ratio, and should land near 0.54.
        assert 0.5 < step_up < 0.58

    def test_bigger_steps_up_cost_monotonically_more(self):
        ratios = [0.9, 0.7, 0.5, 0.3]
        values = [transfer_role_multiplier(moved=True, quality_ratio=r) for r in ratios]
        assert values == sorted(values, reverse=True)

    def test_a_step_down_is_capped_rather_than_rewarded(self):
        assert transfer_role_multiplier(moved=True, quality_ratio=5.0) == 1.0

    def test_the_discount_is_floored(self):
        assert transfer_role_multiplier(moved=True, quality_ratio=0.0001) == MIN_MOVE_MULTIPLIER

    def test_an_unrateable_old_club_falls_back_to_the_lateral_discount(self):
        """A promoted club or a move from abroad: we know it costs, we cannot size it."""
        assert transfer_role_multiplier(moved=True, quality_ratio=None) == LATERAL_MOVE_MULTIPLIER


class FakeProjection:
    """The minimum `replacement_levels` needs: a position, points and an id."""

    def __init__(self, player_id, position, points):
        self.player_id = player_id
        self.position = position
        self.points = points
        self.web_name = f'p{player_id}'


def pool(counts: dict) -> list:
    projections, next_id = [], 1
    for position, count in counts.items():
        for index in range(count):
            projections.append(FakeProjection(next_id, position, 100.0 - index))
            next_id += 1
    return projections


class TestPreseasonRoleCurve:
    """How much pre-season outweighs last season, by how nailed the player was.

    The shape is the point: low at both ends, high in the middle. A monotonic version of this
    was measured and rejected - see the `minutes.py` docstring - so a test that only checked
    "nailed starters get less" would pass on the wrong model.
    """

    def test_a_nailed_starter_keeps_his_prior(self):
        assert preseason_weight_for_prior(0.92) < 0.35

    def test_an_open_squad_place_is_decided_by_preseason(self):
        assert preseason_weight_for_prior(0.5) == pytest.approx(PRESEASON_ROLE_KNOTS[1])
        assert preseason_weight_for_prior(0.5) > 0.9

    def test_a_fringe_player_gets_little_weight_too(self):
        """Not monotonic: friendly starts are cheap when the first team is being rested."""
        assert preseason_weight_for_prior(0.05) < 0.35

    def test_the_curve_peaks_in_the_middle(self):
        middle = preseason_weight_for_prior(0.5)
        assert middle > preseason_weight_for_prior(0.1)
        assert middle > preseason_weight_for_prior(0.9)

    def test_it_is_continuous_across_the_range(self):
        """A threshold would put a cliff between a 0.79 and an 0.81 starter."""
        samples = [preseason_weight_for_prior(x / 100) for x in range(101)]
        for earlier, later in zip(samples, samples[1:]):
            assert abs(later - earlier) < 0.02

    def test_no_prior_season_returns_the_middle_knot(self):
        assert preseason_weight_for_prior(None) == pytest.approx(PRESEASON_ROLE_KNOTS[1])

    def test_every_weight_stays_a_weight(self):
        for x in range(101):
            assert 0.0 <= preseason_weight_for_prior(x / 100) <= 1.0


def a_player(position: PlayerType = PlayerType.MID, status: str = 'a') -> Player:
    return Player(
        player_id=1, code=1, first_name='A', second_name='B', web_name='AB',
        player_type=position, team_id=1, now_cost=9.0, status=status,
        chance_of_playing_next_round=None, chance_of_playing_this_round=None, news='', minutes=0,
    )


def a_prior_season(starts: int, prior_team: str | None = 'MUN') -> PlayerSeason:
    return PlayerSeason(
        player_id=1, season='2025-2026', source=PriorSeasonSource.BOOTSTRAP, team_id=1,
        team='MUN', prior_team=prior_team, minutes=starts * 88, starts=starts, total_points=100,
        goals_scored=5, assists=5, clean_sheets=5, goals_conceded=20, own_goals=0,
        penalties_saved=0, penalties_missed=0, yellow_cards=3, red_cards=0, saves=0, bonus=10,
        bps=300, defensive_contribution=200, expected_goals=4.0, expected_assists=4.0,
        expected_goal_involvements=8.0, expected_goals_conceded=15.0,
    )


def a_preseason_role(starts: float, matches: int = 6, weight: float = 1.2) -> PreseasonRole:
    return PreseasonRole(
        player_id=1, team_matches=matches, team_weight=weight, matches=matches,
        starts=int(starts * matches), competitive_starts=0,
        weighted_starts=starts * weight, benched=matches - int(starts * matches), unavailable=0,
    )


class TestMinutesBlend:
    """The Bruno Fernandes case, and the two guards that keep the fix from overreaching."""

    def test_a_nailed_starter_survives_a_quiet_preseason(self):
        estimate = MinutesModel().estimate(
            a_player(), a_prior_season(starts=35), PlayerHistory(1, 1),
            a_preseason_role(starts=1 / 6),
        )
        assert estimate.prior_start_share == pytest.approx(0.921, abs=0.001)
        assert estimate.preseason_start_share == pytest.approx(0.167, abs=0.001)
        assert estimate.p_start > 0.65, 'a 35-start season must outweigh one friendly start'
        assert estimate.is_role_drop, 'and the disagreement must still be flagged'

    def test_the_flat_weight_is_what_it_replaces(self):
        """The same player under the old model, so the size of the change is pinned."""
        estimate = MinutesModel(role_knots=None).estimate(
            a_player(), a_prior_season(starts=35), PlayerHistory(1, 1),
            a_preseason_role(starts=1 / 6),
        )
        assert estimate.p_start == pytest.approx(0.468, abs=0.002)

    def test_a_squad_player_is_judged_on_preseason(self):
        estimate = MinutesModel().estimate(
            a_player(), a_prior_season(starts=19), PlayerHistory(1, 1),
            a_preseason_role(starts=1.0),
        )
        assert estimate.p_start > 0.9, 'an open place is decided in pre-season, not last season'

    def test_a_mover_keeps_the_flat_weight(self):
        """For him pre-season is the only observation of the squad he is now in."""
        nailed_share = 35 / 38
        moved = MinutesModel().estimate(
            a_player(), a_prior_season(starts=35, prior_team='BOU'), PlayerHistory(1, 1),
            a_preseason_role(starts=1 / 6), moved=True,
        )
        stayed = MinutesModel().estimate(
            a_player(), a_prior_season(starts=35), PlayerHistory(1, 1),
            a_preseason_role(starts=1 / 6), moved=False,
        )
        assert moved.preseason_weight_used == pytest.approx(0.60)
        assert moved.p_start < stayed.p_start
        assert moved.p_start < nailed_share * 0.6
        assert not moved.is_role_drop, 'a mover is covered by the transfer discount instead'

    def test_thin_club_evidence_still_ramps_the_weight_down(self):
        """The two reductions are independent and both apply."""
        thin = MinutesModel().estimate(
            a_player(), a_prior_season(starts=19), PlayerHistory(1, 1),
            a_preseason_role(starts=1.0, matches=1, weight=0.2),
        )
        full = MinutesModel().estimate(
            a_player(), a_prior_season(starts=19), PlayerHistory(1, 1),
            a_preseason_role(starts=1.0, matches=6, weight=1.2),
        )
        assert thin.preseason_weight_used < full.preseason_weight_used

    def test_availability_still_overrides_the_role(self):
        estimate = MinutesModel().estimate(
            a_player(status='i'), a_prior_season(starts=35), PlayerHistory(1, 1),
            a_preseason_role(starts=1.0),
        )
        assert estimate.p_start == 0.0
        assert estimate.role_share > 0.9, 'the role is intact; he is just not fit'

    def test_malformed_knots_are_rejected(self):
        with pytest.raises(ValueError, match='three values'):
            MinutesModel(role_knots=(0.2, 1.0))
        with pytest.raises(ValueError, match='must be in'):
            MinutesModel(role_knots=(0.2, 1.4, 0.1))


class TestValueOverReplacement:

    def test_replacement_is_priced_against_starting_slots(self):
        """4 managers x 1 starting keeper = 4 taken, so the 5th best keeper is replacement."""
        projections = pool({p: 30 for p in (PlayerType.GKP, PlayerType.DEF, PlayerType.MID, PlayerType.FWD)})
        levels = replacement_levels(projections, managers=4)
        assert levels[PlayerType.GKP].points == 100.0 - 4
        assert levels[PlayerType.DEF].points == 100.0 - 16
        assert levels[PlayerType.FWD].points == 100.0 - 8

    def test_roster_slots_would_inflate_keeper_value(self):
        """Why the default is starting slots: roster slots price a starter against a backup.

        Eight keepers get drafted, so the roster-slot replacement is the ninth best - a player
        who will not start a game. Pricing against him inflates every real keeper's VORP, which
        is what put three goalkeepers in the top ten of the 2026/27 board.
        """
        projections = pool({p: 30 for p in (PlayerType.GKP, PlayerType.DEF, PlayerType.MID, PlayerType.FWD)})
        starting = replacement_levels(projections, managers=4)
        roster = replacement_levels(projections, managers=4, slots=DRAFT_SQUAD_SLOTS)
        assert roster[PlayerType.GKP].points < starting[PlayerType.GKP].points
        best_keeper = next(p for p in projections if p.position is PlayerType.GKP)
        assert (
            value_over_replacement(best_keeper, roster)
            > value_over_replacement(best_keeper, starting)
        )

    def test_taking_the_best_forwards_does_not_move_replacement(self):
        """Both the pool and the picks left shrink by four, so the same player is last taken."""
        projections = pool({p: 30 for p in (PlayerType.GKP, PlayerType.DEF, PlayerType.MID, PlayerType.FWD)})
        before = replacement_levels(projections, managers=4)[PlayerType.FWD]
        best = [p.player_id for p in projections if p.position is PlayerType.FWD][:4]
        after = replacement_levels(projections, managers=4, drafted=set(best))[PlayerType.FWD]

        assert after.remaining_picks == before.remaining_picks - 4
        assert after.points == before.points

    def test_reaching_below_replacement_raises_it_for_everyone_else(self):
        """A rival wasting a pick on a deep forward makes the surviving forwards worth more."""
        projections = pool({p: 30 for p in (PlayerType.GKP, PlayerType.DEF, PlayerType.MID, PlayerType.FWD)})
        before = replacement_levels(projections, managers=4)[PlayerType.FWD]
        reaches = [p.player_id for p in projections if p.position is PlayerType.FWD][20:24]
        after = replacement_levels(projections, managers=4, drafted=set(reaches))[PlayerType.FWD]

        assert after.remaining_picks == before.remaining_picks - 4
        assert after.points > before.points

    def test_vorp_is_points_minus_replacement(self):
        projections = pool({p: 30 for p in (PlayerType.GKP, PlayerType.DEF, PlayerType.MID, PlayerType.FWD)})
        levels = replacement_levels(projections, managers=4)
        best_forward = next(p for p in projections if p.position is PlayerType.FWD)
        # 2 starting forwards x 4 managers = 8 taken, so replacement is 100 - 8 = 92.
        assert value_over_replacement(best_forward, levels) == pytest.approx(8.0)

    def test_an_empty_position_raises_rather_than_scoring_zero(self):
        projections = pool({PlayerType.GKP: 5, PlayerType.DEF: 5, PlayerType.MID: 5, PlayerType.FWD: 0})
        with pytest.raises(ValueError, match='No projected players at FWD'):
            replacement_levels(projections, managers=4)

    def test_exhausted_pool_is_flagged(self):
        projections = pool({PlayerType.GKP: 3, PlayerType.DEF: 30, PlayerType.MID: 30, PlayerType.FWD: 30})
        levels = replacement_levels(projections, managers=4)
        assert levels[PlayerType.GKP].is_exhausted is True

    def test_tier_breaks_land_on_the_biggest_gaps(self):
        breaks = tier_breaks([30.0, 29.0, 28.0, 10.0, 9.0, 8.0, 1.0], max_tiers=3)
        assert breaks == [3, 6]

    def test_tier_breaks_of_a_tiny_board_are_empty(self):
        assert tier_breaks([5.0]) == []


class TestMethods:

    def test_registry_entries_are_self_consistent(self):
        for name, entry in METHODS.items():
            assert entry.name == name
            assert entry.notes
            assert entry.params.gameweek_from <= entry.params.gameweek_to

    def test_controls_differ_from_the_baseline_in_exactly_one_way(self):
        baseline = method('v1-baseline').params
        assert method('v0-raw-dc').params == baseline.replace(dc_shrinkage_starts=0.0)
        assert method('v2-transfer').params == baseline.replace(discount_transfers=True)

    def test_the_baseline_leaves_the_transfer_discount_off(self):
        """So that v1-baseline vs v2-transfer isolates exactly that decision."""
        assert method('v1-baseline').params.discount_transfers is False
        assert method('v2-transfer').params.discount_transfers is True

    def test_unknown_method_names_are_rejected(self):
        with pytest.raises(KeyError, match='Unknown projection method'):
            method('does-not-exist')

    def test_unknown_parameter_names_are_rejected(self):
        with pytest.raises(TypeError, match='Unknown projection parameter'):
            ProjectionParams().replace(dc_shrinkage_startz=3)

    def test_horizon_counts_both_endpoints(self):
        assert ProjectionParams(gameweek_from=1, gameweek_to=10).horizon == 10


class TestRoleEvidenceWindow:
    """Which matches count as role evidence, once some of the season has been played.

    Before GW1 the two settings cannot differ: the window closes at the GW1 deadline either way.
    From GW2 on, the default pulls played gameweeks into the blend at competitive weight, which is
    a real change in what the fitted constants are being applied to - so it is a named parameter
    with a control, not an implicit consequence of `gameweek_from`.
    """

    def test_the_default_window_is_the_projection_start(self):
        from src.fpl.projection.methods import METHODS

        params = METHODS['v3-role-trust'].params.replace(gameweek_from=2, gameweek_to=11)
        assert params.role_evidence_before_gameweek is None
        assert (params.role_evidence_before_gameweek or params.gameweek_from) == 2

    def test_the_control_pins_the_window_to_pre_season(self):
        from src.fpl.projection.methods import METHODS

        params = METHODS['v3-role-preseason-only'].params.replace(gameweek_from=2, gameweek_to=11)
        assert (params.role_evidence_before_gameweek or params.gameweek_from) == 1

    def test_the_pair_differ_in_exactly_one_parameter(self):
        from dataclasses import asdict

        from src.fpl.projection.methods import METHODS

        a = asdict(METHODS['v3-role-trust'].params)
        b = asdict(METHODS['v3-role-preseason-only'].params)
        assert [key for key in a if a[key] != b[key]] == ['role_evidence_before_gameweek']


class TestCurrentSeasonRamp:
    """Once matches have been played, they take over from last season's start share.

    The pre-season curve answers "what does *July* tell you about this player", and for a nailed
    starter the fitted answer is "very little - his absence is rest". That answer expires the
    moment real team sheets exist, and without a ramp a 0.15 weight would still be handing last
    season 85% of the say in October. Nothing here is fitted: one gameweek cannot fit a ramp, so
    the shape is a stated judgement with `v3-role-trust` as the do-nothing control.
    """

    def test_the_ramp_is_inert_before_a_ball_is_kicked(self):
        from src.fpl.projection.minutes import current_season_ramp

        assert current_season_ramp(0, 5.0) == 0.0
        assert current_season_ramp(4, None) == 0.0, 'None is the control, at any point in a season'

    def test_it_walks_to_one_over_the_stated_number_of_matches(self):
        from src.fpl.projection.minutes import current_season_ramp

        assert current_season_ramp(1, 5.0) == pytest.approx(0.2)
        assert current_season_ramp(3, 5.0) == pytest.approx(0.6)
        assert current_season_ramp(5, 5.0) == 1.0
        assert current_season_ramp(12, 5.0) == 1.0, 'and stays there'

    def test_a_nonsense_ramp_length_raises(self):
        from src.fpl.projection.minutes import current_season_ramp

        with pytest.raises(ValueError, match='must be positive'):
            current_season_ramp(3, 0)

    def a_nailed_starter_benched_recently(self, **kwargs):
        return MinutesModel(**kwargs).estimate(
            a_player(), a_prior_season(starts=35), PlayerHistory(1, 1),
            a_preseason_role(starts=0.0),
        )

    def test_before_the_season_the_ramp_changes_nothing(self):
        """v4 must be identical to v3 in August, or every pre-season comparison is invalidated."""
        with_ramp = self.a_nailed_starter_benched_recently(
            played_gameweeks=0, current_season_full_matches=5.0)
        without = self.a_nailed_starter_benched_recently()
        assert with_ramp.p_start == pytest.approx(without.p_start)

    def test_by_five_matches_the_recent_window_decides(self):
        """A nailed starter who has not started a match in five weeks is not a nailed starter."""
        after_one = self.a_nailed_starter_benched_recently(
            played_gameweeks=1, current_season_full_matches=5.0)
        after_five = self.a_nailed_starter_benched_recently(
            played_gameweeks=5, current_season_full_matches=5.0)
        before = self.a_nailed_starter_benched_recently()
        assert before.p_start > after_one.p_start > after_five.p_start
        assert after_five.p_start == pytest.approx(0.0, abs=0.01), 'he has started nothing'
        # 0.66 -> 0.53 on one benching. The curve is interpolated, not stepped: a 0.921 prior sits
        # between the 0.5 and 1.0 knots and starts at a 0.284 weight, not the 0.15 endpoint, so a
        # fifth of the remaining distance is a real but survivable dent.
        assert after_one.p_start == pytest.approx(0.53, abs=0.02)
        assert after_one.p_start > 0.5, 'one benching is not evidence of a role change'

    def test_a_player_the_recent_window_likes_gains_from_it(self):
        """Symmetry: the ramp is not a penalty, it is a transfer of authority."""
        model = MinutesModel(played_gameweeks=5, current_season_full_matches=5.0)
        estimate = model.estimate(
            a_player(), a_prior_season(starts=8), PlayerHistory(1, 1),
            a_preseason_role(starts=1.0),
        )
        assert estimate.p_start > 0.9


class TestClubRatingsFromThisSeason:
    """Club ratings used to be a year old all season. Now this season is blended in.

    The blind spot this closes: Manchester United lost 0-2 at Hull in GW1 and every clean-sheet
    probability for their defenders was unchanged, because `TeamStrength` read only last season's
    finished fixtures. Reads the real snapshots, so it skips where they are absent.
    """

    @pytest.fixture(scope='class')
    def loaded(self):
        from src.fpl.loader.load import load_from_snapshots
        from src.fpl.loader.store import JsonSnapshotStore, SnapshotSpec
        from src.fpl.loader.utils import Season

        if JsonSnapshotStore(SnapshotSpec(base_path=f'data/{Season.CURRENT}/bootstrap')).find_latest() is None:
            pytest.skip('No bootstrap snapshot')
        load_from_snapshots(Season.CURRENT)
        return Season.CURRENT

    def test_ignoring_the_current_season_is_the_old_behaviour(self, loaded):
        from src.fpl.projection.strength import TeamStrength

        off = TeamStrength(loaded, current_prior_matches=None)
        assert off.current_totals == {}
        for rating in off.ratings.values():
            assert rating.matches <= 38, 'no current-season matches folded in'

    def test_blending_moves_a_club_toward_what_it_has_just_done(self, loaded):
        from src.fpl.projection.strength import TeamStrength

        off = TeamStrength(loaded, current_prior_matches=None)
        on = TeamStrength(loaded, current_prior_matches=5.0)
        if not on.current_totals or not any(row[2] for row in on.current_totals.values()):
            pytest.skip('No finished fixtures in the current season yet')
        moved = [name for name in on.ratings
                 if abs(on.ratings[name].defence - off.ratings[name].defence) > 1e-9]
        assert moved, 'at least one club has played and must have moved'
        # Direction, for clubs with a rate of their own: conceding above their prior rate must
        # rate them worse. Promoted clubs are excluded because their prior is the bottom-three
        # placeholder rather than a measured rate, and one hard match shrunk on its own sample is a
        # milder claim than a whole season of being the third-worst defence in the division.
        for name in moved:
            goals_for, goals_against, matches = on.current_totals[name]
            if not matches or off.ratings[name].promoted:
                continue
            prior = off.ratings[name]
            # Compare like with like: the club's own *raw* prior rate, not `prior.attack *
            # league_average`, which reconstructs a rate from an already-shrunk multiplier and so
            # sits above the real one. NFO scored 1.000/match against a raw prior of 0.941 and a
            # shrunk-implied 1.025 - an improvement that the old threshold read as a decline, and
            # the blend correctly raised their attack while the test demanded it fall.
            prior_attack_rate = prior.goals_for / prior.matches
            prior_defence_rate = prior.goals_against / prior.matches
            if goals_against / matches > prior_defence_rate:
                assert on.ratings[name].defence > prior.defence, name
            if goals_for / matches < prior_attack_rate:
                assert on.ratings[name].attack < prior.attack, name

    def test_a_nonsense_blend_length_raises(self, loaded):
        from src.fpl.projection.strength import TeamStrength

        with pytest.raises(ValueError, match='must be positive'):
            TeamStrength(loaded, current_prior_matches=0)
