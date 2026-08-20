"""Typed FotMob match payloads.

Key concepts:
- `MatchKind` splits competitive fixtures from friendlies. Friendlies are noisy evidence of a
  player's role (managers rotate freely, run trialists, and play 60-minute halves), but they
  are the *only* evidence available in pre-season, and their `unavailable` list is a reliable
  signal of who is injured or otherwise out.
- `MatchDetails.lineup_available` makes a lineup-less friendly a first-class state instead of
  a swallowed parsing error. FotMob regularly publishes friendly results with no lineup at all.
"""
from datetime import datetime
from enum import Enum

from pydantic import BaseModel


class MatchKind(str, Enum):
    """How much a match tells us about a manager's first-choice XI."""

    COMPETITIVE = 'competitive'
    FRIENDLY = 'friendly'


FRIENDLY_LEAGUE_NAMES: frozenset[str] = frozenset({
    'Club Friendlies',
    'Premier League Summer Series',
})
"""FotMob `leagueName` values that are pre-season or exhibition fixtures.

Everything else - domestic league, cups, European competition, Community Shield - counts as
competitive. Keep this list explicit: silently classifying an unknown competition as
competitive would quietly overweight it.
"""


def classify_match_kind(league_name: str) -> MatchKind:
    """Map a FotMob `leagueName` onto a `MatchKind`."""
    return MatchKind.FRIENDLY if league_name in FRIENDLY_LEAGUE_NAMES else MatchKind.COMPETITIVE


class FotmobTeam(BaseModel):
    id: int
    name: str


class FotmobPlayer(BaseModel):
    id: int
    name: str


class Substitution(BaseModel):
    time: int
    player_out_injured: bool
    player_out: FotmobPlayer
    player_in: FotmobPlayer


class MatchDetails(BaseModel):
    """One team's view of a single FotMob match.

    Key invariants:
    - `starters`, `benched` and `subs_log` are empty when `lineup_available` is False. That
      only happens for friendlies; a competitive match without a lineup raises on parse.
    - `unavailable` is meaningful regardless of `lineup_available` and is never weighted down:
      a player listed as unavailable in a friendly is genuinely unavailable.
    """

    match_id: int
    event_time: datetime
    opponent_team: FotmobTeam
    starters: list[FotmobPlayer]
    benched: list[FotmobPlayer]
    unavailable: list[FotmobPlayer]
    subs_log: list[Substitution]
    league_name: str
    kind: MatchKind
    lineup_available: bool = True

    @property
    def is_friendly(self) -> bool:
        return self.kind is MatchKind.FRIENDLY

    def __repr__(self) -> str:
        lineup = '' if self.lineup_available else ', no lineup'
        return (f'MatchDetails(#{self.match_id} {self.league_name} [{self.kind.value}] '
                f'vs {self.opponent_team.name}{lineup})')
