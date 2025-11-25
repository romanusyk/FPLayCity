from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable

from src.fotmob.models.fotmob import MatchDetails


class PlayerAppearanceStatus(str, Enum):
    STARTED = 'started'
    BENCHED = 'benched'
    UNAVAILABLE = 'unavailable'


@dataclass
class PlayerAppearance:
    fotmob_player_id: int
    status: PlayerAppearanceStatus
    match: MatchDetails

    def __repr__(self) -> str:
        match_summary = f"{self.match.league_name}#{self.match.match_id}"
        opponent = self.match.opponent_team.name
        return (
            "PlayerAppearance("
            f"player={self.fotmob_player_id}, "
            f"status={self.status.value}, "
            f"match={match_summary} vs {opponent}"
            ")"
        )


@dataclass
class PlayerSquadRole:
    fotmob_player_id: int
    appearances: list[PlayerAppearance]
    first_team_threshold: float

    @property
    def starts(self) -> int:
        return sum(1 for appearance in self.appearances if appearance.status is PlayerAppearanceStatus.STARTED)

    @property
    def benched(self) -> int:
        return sum(1 for appearance in self.appearances if appearance.status is PlayerAppearanceStatus.BENCHED)

    @property
    def unavailable(self) -> int:
        return sum(1 for appearance in self.appearances if appearance.status is PlayerAppearanceStatus.UNAVAILABLE)

    @property
    def total_matches(self) -> int:
        return len(self.appearances)

    @property
    def start_ratio(self) -> float:
        if not self.total_matches:
            return 0.0
        return self.starts / self.total_matches

    @property
    def is_first_team(self) -> bool:
        return self.start_ratio >= self.first_team_threshold

    def __repr__(self) -> str:
        return (
            "PlayerSquadRole("
            f"player={self.fotmob_player_id}, "
            f"starts={self.starts}, "
            f"benched={self.benched}, "
            f"unavailable={self.unavailable}, "
            f"total={self.total_matches}, "
            f"start_ratio={self.start_ratio:.2f}, "
            f"first_team={self.is_first_team}"
            ")"
        )


@dataclass
class RivalSubDetail:
    fotmob_player_id: int
    fotmob_name: str
    sub_count: int
    matches: list[MatchDetails] = field(default_factory=list)

    def __repr__(self) -> str:
        return (
            "RivalSubDetail("
            f"player={self.fotmob_player_id}, "
            f"name='{self.fotmob_name}', "
            f"subs={self.sub_count}, "
            f"matches={len(self.matches)}"
            ")"
        )


@dataclass
class RivalStartHint:
    player_fotmob_id: int
    rivals_sorted: list[RivalSubDetail]
    rivals_unlikely_to_start: set[int] = field(default_factory=set)
    rivals_likely_to_start: set[int] = field(default_factory=set)

    @property
    def has_rival_unlikely_to_start(self) -> bool:
        return bool(self.rivals_unlikely_to_start)

    @property
    def has_rival_likely_to_start(self) -> bool:
        return bool(self.rivals_likely_to_start)

    def rivals_ordered(self) -> Iterable[RivalSubDetail]:
        return self.rivals_sorted

    def __repr__(self) -> str:
        rivals_preview = ", ".join(
            f"{detail.fotmob_name}({detail.fotmob_player_id})" for detail in self.rivals_sorted[:3]
        )
        if len(self.rivals_sorted) > 3:
            rivals_preview += ", ..."
        return (
            "RivalStartHint("
            f"player={self.player_fotmob_id}, "
            f"rivals={len(self.rivals_sorted)}, "
            f"unlikely={len(self.rivals_unlikely_to_start)}, "
            f"likely={len(self.rivals_likely_to_start)}, "
            f"top=[{rivals_preview}]"
            ")"
        )



