from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable

from src.fotmob.models.fotmob import MatchDetails, MatchKind


class PlayerAppearanceStatus(str, Enum):
    STARTED = 'started'
    BENCHED = 'benched'
    UNAVAILABLE = 'unavailable'


@dataclass
class PlayerAppearance:
    """One player's involvement in one match, carrying the evidence weight of that match."""

    fotmob_player_id: int
    status: PlayerAppearanceStatus
    match: MatchDetails
    weight: float = 1.0

    @property
    def kind(self) -> MatchKind:
        return self.match.kind

    def __repr__(self) -> str:
        match_summary = f"{self.match.league_name}#{self.match.match_id}"
        opponent = self.match.opponent_team.name
        return (
            "PlayerAppearance("
            f"player={self.fotmob_player_id}, "
            f"status={self.status.value}, "
            f"weight={self.weight:g}, "
            f"match={match_summary} vs {opponent}"
            ")"
        )


@dataclass
class PlayerSquadRole:
    """A player's standing in the squad, blending competitive and friendly evidence.

    Key invariants:
    - `start_ratio` is *weighted*: friendlies count for whatever `RotationConfig` says.
      Use `raw_start_ratio` for the unweighted view.
    - `unavailable` is never weighted. Being left out injured is equally informative
      whoever the opponent was.
    """

    fotmob_player_id: int
    appearances: list[PlayerAppearance]
    first_team_threshold: float

    def _count(self, status: PlayerAppearanceStatus, kind: MatchKind | None = None) -> int:
        return sum(
            1 for appearance in self.appearances
            if appearance.status is status and (kind is None or appearance.kind is kind)
        )

    def _weighted(self, status: PlayerAppearanceStatus | None = None) -> float:
        return sum(
            appearance.weight for appearance in self.appearances
            if status is None or appearance.status is status
        )

    @property
    def starts(self) -> int:
        return self._count(PlayerAppearanceStatus.STARTED)

    @property
    def competitive_starts(self) -> int:
        return self._count(PlayerAppearanceStatus.STARTED, MatchKind.COMPETITIVE)

    @property
    def friendly_starts(self) -> int:
        return self._count(PlayerAppearanceStatus.STARTED, MatchKind.FRIENDLY)

    @property
    def benched(self) -> int:
        return self._count(PlayerAppearanceStatus.BENCHED)

    @property
    def unavailable(self) -> int:
        """Matches the player was listed as unavailable for. Never weighted - see class docs."""
        return self._count(PlayerAppearanceStatus.UNAVAILABLE)

    @property
    def total_matches(self) -> int:
        return len(self.appearances)

    @property
    def weighted_starts(self) -> float:
        return self._weighted(PlayerAppearanceStatus.STARTED)

    @property
    def weighted_matches(self) -> float:
        return self._weighted()

    @property
    def start_ratio(self) -> float:
        """Weighted share of matches started. 0.0 when there is no evidence at all."""
        if not self.weighted_matches:
            return 0.0
        return self.weighted_starts / self.weighted_matches

    @property
    def raw_start_ratio(self) -> float:
        """Unweighted share of matches started, treating a friendly like a league game."""
        if not self.total_matches:
            return 0.0
        return self.starts / self.total_matches

    @property
    def is_first_team(self) -> bool:
        return self.start_ratio >= self.first_team_threshold

    @property
    def evidence_is_friendly_only(self) -> bool:
        """True when every appearance came in a friendly - typical in pre-season.

        Callers should treat `is_first_team` as provisional when this holds.
        """
        return bool(self.appearances) and all(
            appearance.kind is MatchKind.FRIENDLY for appearance in self.appearances
        )

    def __repr__(self) -> str:
        return (
            "PlayerSquadRole("
            f"player={self.fotmob_player_id}, "
            f"starts={self.starts} ({self.competitive_starts} comp / {self.friendly_starts} fr), "
            f"benched={self.benched}, "
            f"unavailable={self.unavailable}, "
            f"total={self.total_matches}, "
            f"start_ratio={self.start_ratio:.2f}, "
            f"first_team={self.is_first_team}"
            ")"
        )


@dataclass
class RivalSubDetail:
    fpl_player_id: int
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



