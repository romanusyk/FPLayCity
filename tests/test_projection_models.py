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
