"""Opponent adjustment: dividing the schedule out of a record, and putting it back per fixture.

Three separate claims are pinned here, because they fail in different ways:

- `normalise`/`apply` are inverses at the same elasticity, so a player who faced an average
  schedule is left exactly as measured. Anything else silently re-rates the whole league.
- The fitted elasticities are used, not assumed to be 1.0. A forward's xG measured *no* opponent
  effect, so adjusting it must be a no-op - and that is the case most likely to be "fixed" by
  someone who thinks a zero looks like a bug.
- Defensive actions are shrunk toward the position mean *before* the threshold is applied. This is
  the lever that actually moves a thin sample, and the test contrasts it with the hit-rate prior,
  which looks like the same fix and is not.

All offline, on hand-built rows.
"""
import pytest

from src.fpl.models.immutable import PlayerType
from src.fpl.projection import opponent
from src.fpl.projection.defensive import DefensiveContributionModel
from src.fpl.projection.history import MatchRow, PlayerHistory


def make_match(season='2026-2027', gameweek=1, opponent_name='AAA', minutes=90,
               dc=8, was_home=True, started=True):
    return MatchRow(
        season=season, gameweek=gameweek, fixture_id=gameweek, opponent=opponent_name,
        was_home=was_home, kickoff_time='2026-08-15T14:00:00Z',
        minutes=minutes, starts=1 if started else 0, total_points=2,
        goals_scored=0, assists=0, clean_sheets=0, goals_conceded=1, saves=0,
        defensive_contribution=dc, bonus=0, bps=10, yellow_cards=0, red_cards=0,
        penalties_saved=0, penalties_missed=0, own_goals=0,
        expected_goals=0.1, expected_assists=0.1, expected_goal_involvements=0.2,
        expected_goals_conceded=1.0,
    )


class Rating:
    def __init__(self, attack, defence):
        self.attack, self.defence = attack, defence


class TestNormaliseAndApply:
    def test_they_are_inverses(self):
        """A rate divided out at one opponent and put back at the same one must not move."""
        rate = 0.4
        neutral = opponent.normalise(rate, exposure=1.3, elasticity=0.58)
        assert opponent.apply(1.3, 0.58) * neutral == pytest.approx(rate)

    def test_an_average_schedule_changes_nothing(self):
        assert opponent.normalise(0.4, exposure=1.0, elasticity=0.58) == pytest.approx(0.4)

    def test_zero_elasticity_is_a_no_op_in_both_directions(self):
        """A forward's xG measured no opponent effect. Adjusting it must do literally nothing."""
        assert opponent.normalise(0.44, exposure=1.4, elasticity=0.0) == 0.44
        assert opponent.apply(1.4, 0.0) == 1.0

    def test_a_partial_elasticity_moves_less_than_proportionally(self):
        """The whole point: the engine used to apply the multiplier whole."""
        assert 1.0 < opponent.apply(1.4, 0.51) < 1.4

    def test_a_harder_schedule_raises_the_neutral_rate(self):
        """Actions earned against strong attacks are worth less in an average fixture."""
        assert opponent.normalise(12.5, exposure=1.2, elasticity=0.5) < 12.5
        assert opponent.normalise(12.5, exposure=0.8, elasticity=0.5) > 12.5


class TestFittedElasticities:
    def test_every_metric_and_position_is_declared(self):
        """A missing entry would be a KeyError mid-projection, on one position, for one metric."""
        for metric in ('xg', 'xa', 'bonus', 'saves', 'dc', 'yellow', 'red'):
            for position in PlayerType:
                assert metric in opponent.OPPONENT_ELASTICITY
                assert position in opponent.OPPONENT_ELASTICITY[metric]

    def test_all_are_clamped_to_a_sane_range(self):
        """Negative would make a striker better against better defences; above 1 would amplify."""
        for metric, by_position in opponent.OPPONENT_ELASTICITY.items():
            for position, value in by_position.items():
                assert 0.0 <= value <= 1.0, (metric, position, value)

    def test_a_forwards_xg_is_not_adjusted(self):
        """Measured at -0.11 - noise around no effect. Clamped to zero on purpose, not overlooked."""
        assert opponent.OPPONENT_ELASTICITY['xg'][PlayerType.FWD] == 0.0

    def test_cards_are_never_adjusted(self):
        for position in PlayerType:
            assert opponent.OPPONENT_ELASTICITY['yellow'][position] == 0.0
            assert opponent.OPPONENT_ELASTICITY['red'][position] == 0.0

    def test_each_metric_is_routed_to_an_axis(self):
        """Attacking output keys off the opponent's defence; work created keys off their attack."""
        assert opponent.METRIC_AXIS['xg'] == opponent.ATTACK_SIDE
        assert opponent.METRIC_AXIS['dc'] == opponent.PRESSURE_SIDE
        assert opponent.METRIC_AXIS['saves'] == opponent.PRESSURE_SIDE


class TestBuildExposures:
    def ratings(self):
        return {'2026-2027': {'WEAK': Rating(attack=0.7, defence=1.3),
                              'STRONG': Rating(attack=1.4, defence=0.7)}}

    def test_an_average_schedule_comes_out_neutral(self):
        history = PlayerHistory(player_id=1, code=1, matches=[
            make_match(opponent_name='WEAK', was_home=True),
            make_match(opponent_name='STRONG', was_home=False),
        ])
        got = opponent.build_exposures({1: history}, self.ratings(), home_advantage=1.0)[1]
        assert got.attack_side == pytest.approx(1.0)
        assert got.pressure_side == pytest.approx(1.05)

    def test_a_soft_schedule_shows_up_on_the_attacking_axis(self):
        history = PlayerHistory(player_id=1, code=1, matches=[
            make_match(opponent_name='WEAK'), make_match(opponent_name='WEAK')])
        got = opponent.build_exposures({1: history}, self.ratings(), home_advantage=1.0)[1]
        assert got.attack_side == pytest.approx(1.3)

    def test_it_is_weighted_by_minutes_not_by_appearances(self):
        """Ten minutes against a weak side is not evidence equal to a full match."""
        history = PlayerHistory(player_id=1, code=1, matches=[
            make_match(opponent_name='WEAK', minutes=10),
            make_match(opponent_name='STRONG', minutes=90),
        ])
        got = opponent.build_exposures({1: history}, self.ratings(), home_advantage=1.0)[1]
        assert got.attack_side < 0.85, 'the 90-minute match must dominate'

    def test_a_player_with_no_matches_is_neutral(self):
        got = opponent.build_exposures(
            {1: PlayerHistory(player_id=1, code=1, matches=[])}, self.ratings(), 1.0)[1]
        assert got is opponent.NEUTRAL

    def test_an_unrated_opponent_is_skipped_not_guessed(self):
        """A club relegated since has no rating. Rating it 1.0 would be a silent invention."""
        history = PlayerHistory(player_id=1, code=1, matches=[
            make_match(opponent_name='WEAK'), make_match(opponent_name='GONE')])
        got = opponent.build_exposures({1: history}, self.ratings(), home_advantage=1.0)[1]
        assert got.matches == 1
        assert got.attack_side == pytest.approx(1.3)


class TestActionsShrinkage:
    """The lever that actually moves a four-start sample."""

    POSITION_ACTIONS = {PlayerType.DEF: 7.77, PlayerType.MID: 8.33, PlayerType.FWD: 4.17}

    def history(self, starts, dc):
        return PlayerHistory(player_id=1, code=1, matches=[
            make_match(gameweek=i + 1, dc=dc) for i in range(starts)])

    def test_a_thin_sample_is_pulled_toward_the_position_mean(self):
        model = DefensiveContributionModel(
            actions_shrinkage_minutes=450.0, position_actions=self.POSITION_ACTIONS)
        got = model.estimate(PlayerType.DEF, self.history(4, 12.5))
        assert got.actions_per_90 == pytest.approx(12.5)
        assert 9.5 < got.actions_per_90_shrunk < 10.5, 'four starts must not be believed outright'

    def test_a_real_sample_is_barely_touched(self):
        model = DefensiveContributionModel(
            actions_shrinkage_minutes=450.0, position_actions=self.POSITION_ACTIONS)
        got = model.estimate(PlayerType.DEF, self.history(29, 7.1))
        assert got.actions_per_90_shrunk == pytest.approx(got.actions_per_90, abs=0.2)

    def test_off_by_default_so_older_methods_are_unchanged(self):
        model = DefensiveContributionModel()
        got = model.estimate(PlayerType.DEF, self.history(4, 12.5))
        assert got.actions_per_90_shrunk == pytest.approx(got.actions_per_90)

    def test_shrinking_lowers_the_implied_hit_rate_of_a_thin_sample(self):
        """`implied_hit_rate` is a tail probability, so an inflated mean overstates it twice over."""
        raw = DefensiveContributionModel().estimate(PlayerType.DEF, self.history(4, 12.5))
        shrunk = DefensiveContributionModel(
            actions_shrinkage_minutes=450.0, position_actions=self.POSITION_ACTIONS
        ).estimate(PlayerType.DEF, self.history(4, 12.5))
        assert shrunk.implied_hit_rate < raw.implied_hit_rate - 0.1

    def test_shrinking_toward_nothing_is_refused(self):
        with pytest.raises(ValueError, match='nothing to shrink toward'):
            DefensiveContributionModel(actions_shrinkage_minutes=450.0)


class TestHitRateAtFixture:
    def estimate(self):
        history = PlayerHistory(player_id=1, code=1, matches=[
            make_match(gameweek=i + 1, dc=9) for i in range(20)])
        return DefensiveContributionModel(
            actions_shrinkage_minutes=450.0,
            position_actions={PlayerType.DEF: 7.77},
        ).estimate(PlayerType.DEF, history)

    def test_an_average_fixture_returns_the_blended_rate(self):
        got = self.estimate()
        assert got.hit_rate_at(1.0) == pytest.approx(got.hit_rate)

    def test_a_more_dangerous_opponent_raises_it(self):
        got = self.estimate()
        assert got.hit_rate_at(1.25) > got.hit_rate

    def test_it_stays_a_probability(self):
        got = self.estimate()
        assert 0.0 <= got.hit_rate_at(5.0) <= 1.0
        assert 0.0 <= got.hit_rate_at(0.01) <= 1.0

    def test_a_goalkeeper_is_untouched(self):
        """Keepers cannot earn the award at all, so no fixture changes their zero."""
        model = DefensiveContributionModel()
        got = model.estimate(PlayerType.GKP, PlayerHistory(1, 1, [make_match(dc=0)]))
        assert got.hit_rate_at(1.5) == got.hit_rate == 0.0
