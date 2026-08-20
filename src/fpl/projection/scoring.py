"""The FPL scoring function, in one place.

Why this module exists
----------------------
`src/fpl/forecast/models.py` scores a player with four terms (clean sheets, xG, xA, defensive
contribution). Measured over 2025/26 that is 45.5% of the points actually awarded - appearance
alone is 56%. Any projection or backtest that disagrees with the real rules is measuring its
own arithmetic rather than the game, so the rules live here and everything shares them.

Key invariants:
- Point values are keyed by `PlayerType`. `PlayerType.MNG` (manager) is not scored; managers are
  a separate FPL game mode and are excluded from projections rather than silently zeroed.
- `DEFENSIVE_CONTRIBUTION_THRESHOLD` is an action count, not points. The award is a step
  function: 9 actions score nothing, 10 score two.
- `score_player_match` reproduces `total_points` exactly given a completed match row. It is
  verified against real per-match history in `tests/test_scoring.py`.
"""
from __future__ import annotations

from dataclasses import dataclass

from src.fpl.models.immutable import PlayerType


APPEARANCE_POINTS_FULL = 2
"""Awarded for 60+ minutes."""

APPEARANCE_POINTS_PARTIAL = 1
"""Awarded for 1-59 minutes."""

FULL_APPEARANCE_MINUTES = 60
"""Minutes needed for the second appearance point, and for clean-sheet eligibility."""

GOAL_POINTS: dict[PlayerType, int] = {
    PlayerType.GKP: 6,
    PlayerType.DEF: 6,
    PlayerType.MID: 5,
    PlayerType.FWD: 4,
}

ASSIST_POINTS = 3

CLEAN_SHEET_POINTS: dict[PlayerType, int] = {
    PlayerType.GKP: 4,
    PlayerType.DEF: 4,
    PlayerType.MID: 1,
    PlayerType.FWD: 0,
}

GOALS_CONCEDED_PER_POINT: dict[PlayerType, int] = {
    PlayerType.GKP: 2,
    PlayerType.DEF: 2,
}
"""Every N goals conceded costs one point, for these positions only."""

SAVES_PER_POINT = 3

PENALTY_SAVE_POINTS = 5
PENALTY_MISS_POINTS = -2
OWN_GOAL_POINTS = -2
YELLOW_CARD_POINTS = -1
RED_CARD_POINTS = -3

DEFENSIVE_CONTRIBUTION_POINTS = 2

DEFENSIVE_CONTRIBUTION_THRESHOLD: dict[PlayerType, int] = {
    PlayerType.GKP: 0,
    PlayerType.DEF: 10,
    PlayerType.MID: 12,
    PlayerType.FWD: 12,
}
"""Action count needed for the +2 award, per position.

`defensive_contribution` in the FPL per-match history is a raw count of actions - clearances,
blocks, interceptions and tackles for defenders, plus ball recoveries for everyone else. A
threshold of 0 means the award does not exist for that position: goalkeepers cannot earn it.
"""


def defensive_contribution_threshold(position: PlayerType) -> int:
    """Return the action count a position needs for the +2 defensive-contribution award.

    Raises:
    - KeyError: for a position with no declared threshold, so a new `PlayerType` cannot
      silently score zero everywhere.
    """
    if position not in DEFENSIVE_CONTRIBUTION_THRESHOLD:
        raise KeyError(
            f"No defensive-contribution threshold declared for {position}. "
            f"Add it to DEFENSIVE_CONTRIBUTION_THRESHOLD in src/fpl/projection/scoring.py."
        )
    return DEFENSIVE_CONTRIBUTION_THRESHOLD[position]


def clears_defensive_threshold(position: PlayerType, actions: int) -> bool:
    """True when `actions` earns the defensive-contribution award for `position`."""
    threshold = defensive_contribution_threshold(position)
    return threshold > 0 and actions >= threshold


def appearance_points(minutes: int) -> int:
    """Points for turning up: 0, 1 or 2."""
    if minutes <= 0:
        return 0
    if minutes >= FULL_APPEARANCE_MINUTES:
        return APPEARANCE_POINTS_FULL
    return APPEARANCE_POINTS_PARTIAL


@dataclass(frozen=True)
class MatchScore:
    """One player-match decomposed into its scoring sources.

    `total` is the sum of the other fields and equals the FPL `total_points` for a completed
    match. The decomposition is what makes a projection auditable: every figure the web app
    shows traces back to one of these components.
    """

    appearance: float = 0.0
    goals: float = 0.0
    assists: float = 0.0
    clean_sheets: float = 0.0
    goals_conceded: float = 0.0
    saves: float = 0.0
    defensive_contribution: float = 0.0
    bonus: float = 0.0
    cards: float = 0.0
    penalties: float = 0.0
    own_goals: float = 0.0

    @property
    def total(self) -> float:
        return (
            self.appearance
            + self.goals
            + self.assists
            + self.clean_sheets
            + self.goals_conceded
            + self.saves
            + self.defensive_contribution
            + self.bonus
            + self.cards
            + self.penalties
            + self.own_goals
        )

    def __add__(self, other: 'MatchScore') -> 'MatchScore':
        return MatchScore(
            appearance=self.appearance + other.appearance,
            goals=self.goals + other.goals,
            assists=self.assists + other.assists,
            clean_sheets=self.clean_sheets + other.clean_sheets,
            goals_conceded=self.goals_conceded + other.goals_conceded,
            saves=self.saves + other.saves,
            defensive_contribution=self.defensive_contribution + other.defensive_contribution,
            bonus=self.bonus + other.bonus,
            cards=self.cards + other.cards,
            penalties=self.penalties + other.penalties,
            own_goals=self.own_goals + other.own_goals,
        )

    def as_dict(self) -> dict[str, float]:
        """Component name -> points, for serialisation into a run artifact."""
        return {
            'appearance': self.appearance,
            'goals': self.goals,
            'assists': self.assists,
            'clean_sheets': self.clean_sheets,
            'goals_conceded': self.goals_conceded,
            'saves': self.saves,
            'defensive_contribution': self.defensive_contribution,
            'bonus': self.bonus,
            'cards': self.cards,
            'penalties': self.penalties,
            'own_goals': self.own_goals,
        }


def score_player_match(
    position: PlayerType,
    minutes: int,
    goals_scored: int,
    assists: int,
    clean_sheet: bool,
    goals_conceded: int,
    saves: int,
    defensive_contribution: int,
    bonus: int,
    yellow_cards: int,
    red_cards: int,
    penalties_saved: int,
    penalties_missed: int,
    own_goals: int,
) -> MatchScore:
    """Score one completed player-match under the 2025/26 FPL rules.

    Parameters mirror the fields of a `data/<season>/elements/*.json` history row, so a caller
    can pass a stored row straight through.

    Returns:
    - A `MatchScore` whose `.total` equals the FPL `total_points` for that row.

    Raises:
    - KeyError: for a position with no declared scoring, i.e. `PlayerType.MNG`. Managers are a
      different game and must be filtered out by the caller rather than scored as zero.
    """
    if position not in GOAL_POINTS:
        raise KeyError(
            f"{position} has no player scoring rules - managers are a separate FPL game mode. "
            f"Filter them out before scoring."
        )

    played_full = minutes >= FULL_APPEARANCE_MINUTES
    conceded_divisor = GOALS_CONCEDED_PER_POINT.get(position)

    return MatchScore(
        appearance=appearance_points(minutes),
        goals=goals_scored * GOAL_POINTS[position],
        assists=assists * ASSIST_POINTS,
        clean_sheets=CLEAN_SHEET_POINTS[position] if (clean_sheet and played_full) else 0,
        goals_conceded=(
            -(goals_conceded // conceded_divisor) if conceded_divisor and minutes > 0 else 0
        ),
        saves=saves // SAVES_PER_POINT,
        defensive_contribution=(
            DEFENSIVE_CONTRIBUTION_POINTS
            if clears_defensive_threshold(position, defensive_contribution)
            else 0
        ),
        bonus=bonus,
        cards=yellow_cards * YELLOW_CARD_POINTS + red_cards * RED_CARD_POINTS,
        penalties=(
            penalties_saved * PENALTY_SAVE_POINTS + penalties_missed * PENALTY_MISS_POINTS
        ),
        own_goals=own_goals * OWN_GOAL_POINTS,
    )
