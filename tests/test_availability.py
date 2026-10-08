"""How long a flagged player is out, rather than "for the rest of the horizon".

`status` is a flag about *now*, and the model used to apply it to every gameweek in the run. A
two-match ban therefore cost a player ten gameweeks: Foden, measured on 2026-09-17, projected ~7
points over GW5-14 against a true figure in the low thirties, and a waiver board recommended selling
him on the strength of it.

What is pinned here is the parsing and the gameweek boundary, because both fail quietly. A date read
with the wrong year puts a return twelve months out; an off-by-one at the boundary hands a suspended
player a gameweek he is banned for. The suppression of players FPL cannot date - "Unknown return
date" - must stay exactly as it was, and has its own test.

Offline; no snapshots are read.
"""
from datetime import date, datetime

import pytest

from src.fpl.projection import availability
from src.fpl.projection.availability import ReturnWindow, parse_return_date, return_window


DEADLINES = {
    5: datetime(2026, 9, 19, 10, 0),
    6: datetime(2026, 10, 3, 10, 0),
    7: datetime(2026, 10, 17, 10, 0),
    8: datetime(2026, 10, 24, 10, 0),
    9: datetime(2026, 11, 7, 10, 0),
}


class TestParsing:
    @pytest.mark.parametrize('news, expected, suspension', [
        ('Suspended until 19 Oct', date(2026, 10, 19), True),
        ('Suspended until 25 Oct', date(2026, 10, 25), True),
        ('Back injury - Expected back 10 Oct', date(2026, 10, 10), False),
        ('Knee injury - Expected back 03 Jan', date(2027, 1, 3), False),
        ('Expected back 1 May', date(2027, 5, 1), False),
    ])
    def test_a_stated_return_is_read(self, news, expected, suspension):
        assert parse_return_date(news, '2026-2027') == (expected, suspension)

    def test_the_year_follows_the_season_not_the_calendar(self):
        """A season runs August to May, so each month appears once and `3 Jan` is unambiguous."""
        assert parse_return_date('Expected back 3 Dec', '2026-2027')[0].year == 2026
        assert parse_return_date('Expected back 3 Jan', '2026-2027')[0].year == 2027

    @pytest.mark.parametrize('news', [
        'Back injury - Unknown return date',
        'Hamstring injury - 75% chance of playing',
        'Has joined Al Hilal permanently',
        '',
    ])
    def test_text_without_a_date_yields_none(self, news):
        """These are the majority. None of them may be read as a return."""
        assert parse_return_date(news, '2026-2027')[0] is None

    def test_a_suspension_is_distinguished_from_an_injury(self):
        """The two are controlled separately because the evidence differs in kind."""
        assert parse_return_date('Suspended until 19 Oct', '2026-2027')[1] is True
        assert parse_return_date('Expected back 19 Oct', '2026-2027')[1] is False

    def test_nonsense_is_not_an_error(self):
        """Unparseable text is a normal state - it must degrade to "no date", not raise."""
        assert parse_return_date('Expected back 31 Flurm', '2026-2027')[0] is None
        assert parse_return_date('Expected back 31 Feb', '2026-2027')[0] is None

    def test_a_malformed_season_raises(self):
        with pytest.raises(ValueError, match='2026-2027'):
            availability.season_years('2026')


class TestGameweekBoundary:
    def test_the_first_gameweek_on_or_after_the_date(self):
        got = return_window('Suspended until 17 Oct', '2026-2027', DEADLINES)
        assert got.gameweek == 7, 'GW7 deadline is 17 Oct - on the date, so he is back for it'

    def test_a_return_just_after_a_deadline_waits_for_the_next_gameweek(self):
        """Conservative on purpose: a player back on the 19th missed GW7's 17 Oct deadline."""
        got = return_window('Suspended until 19 Oct', '2026-2027', DEADLINES)
        assert got.gameweek == 8

    def test_a_return_past_every_stored_deadline_is_unknown(self):
        got = return_window('Expected back 1 May', '2026-2027', DEADLINES)
        assert got.gameweek is None and got.known is False

    def test_no_date_means_no_window(self):
        got = return_window('Back injury - Unknown return date', '2026-2027', DEADLINES)
        assert got is not None and got.known is False


class TestMinutesAtGameweek:
    """The boundary as the projection sees it."""

    def estimate(self, **kwargs):
        from src.fpl.models.immutable import PlayerType
        from src.fpl.projection.minutes import MinutesEstimate
        defaults = dict(
            position=PlayerType.MID, p_start=0.0, role_share=0.8, availability=0.0,
            status='s', news='Suspended until 19 Oct', prior_start_share=0.8, moved=False,
            transfer_multiplier=1.0, preseason_start_share=None, preseason_matches=0,
            preseason_weight_used=0.0, minutes_per_start=85.0, full_match_rate=0.9,
            cameo_rate=0.1, cameo_minutes=20.0, sample_starts=30,
        )
        defaults.update(kwargs)
        return MinutesEstimate(**defaults)

    def test_before_the_return_he_is_still_out(self):
        got = self.estimate(return_window=ReturnWindow(8, date(2026, 10, 19), True))
        assert got.at_gameweek(7).p_start == 0.0

    def test_from_the_return_he_is_back_in_the_side(self):
        got = self.estimate(return_window=ReturnWindow(8, date(2026, 10, 19), True))
        back = got.at_gameweek(8)
        assert back.p_start == pytest.approx(0.8)
        assert back.availability == pytest.approx(1.0)

    def test_everything_derived_follows(self):
        """`p_sixty_plus` and `expected_minutes` key off `p_start`, so the fix must reach them."""
        got = self.estimate(return_window=ReturnWindow(8, date(2026, 10, 19), True))
        assert got.at_gameweek(7).expected_minutes < 5.0
        assert got.at_gameweek(8).expected_minutes > 60.0
        assert got.at_gameweek(8).p_sixty_plus > got.at_gameweek(7).p_sixty_plus

    def test_an_unknown_return_suppresses_the_whole_horizon(self):
        """The pre-v5 behaviour, and still right when FPL cannot date the return."""
        got = self.estimate(news='Back injury - Unknown return date')
        assert got.at_gameweek(5).p_start == 0.0
        assert got.at_gameweek(14).p_start == 0.0

    def test_a_discounted_return_credits_less_than_a_full_role(self):
        """A player back from a long injury need not walk straight back into the side."""
        got = self.estimate(
            return_window=ReturnWindow(8, date(2026, 10, 19), False), return_role_share=0.5)
        assert got.at_gameweek(8).p_start == pytest.approx(0.4)

    def test_an_available_player_is_untouched(self):
        got = self.estimate(status='a', news='', p_start=0.8, availability=1.0)
        assert got.at_gameweek(9) is got, 'no window means the same object, not a copy'
