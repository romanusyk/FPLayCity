from __future__ import annotations

from pydantic import BaseModel, Field

from src.fotmob.models.fotmob import MatchKind


DEFAULT_MATCH_KIND_WEIGHTS: dict[MatchKind, float] = {
    MatchKind.COMPETITIVE: 1.0,
    MatchKind.FRIENDLY: 0.35,
}
"""How much a start in each kind of match says about a player's standing.

A pre-season friendly start is real evidence - it is the only evidence there is in August -
but a weak one: managers rotate at half-time, hand minutes to academy players, and rest
anyone who was at a summer tournament. 0.35 keeps friendlies informative without letting a
full pre-season outweigh a handful of competitive matches once the season starts.

Availability is deliberately *not* weighted. A player on a friendly's `unavailable` list is
just as injured as one missing a league game, so `PlayerSquadRole.unavailable` counts raw.
"""


class RotationConfig(BaseModel):
    """Thresholds controlling how appearances are turned into squad roles.

    Fields:
    - first_team_start_ratio: weighted start share above which a player is "first team".
    - min_subs_for_rival: substitutions needed before another player counts as a rival.
    - match_kind_weights: per-`MatchKind` weight applied to start/bench evidence.
    - included_leagues: optional hard allow-list of FotMob `leagueName`s. Empty means "all
      leagues", which is the default now that friendlies are down-weighted rather than
      dropped. Set it to `["Premier League"]` to restore the old league-only behaviour.
    """

    first_team_start_ratio: float = 0.8
    min_subs_for_rival: int = 1
    match_kind_weights: dict[MatchKind, float] = Field(
        default_factory=lambda: dict(DEFAULT_MATCH_KIND_WEIGHTS)
    )
    included_leagues: list[str] = Field(default_factory=list)

    def weight_for(self, kind: MatchKind) -> float:
        """Return the evidence weight for a match kind.

        Raises:
        - KeyError: if a kind has no configured weight, so a newly added `MatchKind` cannot
          silently default to zero and vanish from every calculation.
        """
        if kind not in self.match_kind_weights:
            raise KeyError(
                f"No weight configured for MatchKind.{kind.name}. "
                f"Add it to RotationConfig.match_kind_weights."
            )
        return self.match_kind_weights[kind]


class PlayerMappingOverride(BaseModel):
    """A hand-resolved FotMob-to-FPL identity that name matching gets wrong.

    Keyed on `fpl_player_code`, never on an element id. FPL reassigns element ids every
    season, so an override written against `fpl_player_id=5` silently pointed FotMob's Gabriel
    at J.Timber once 2026/27 ids landed. `code` is stable for the life of a player.

    An override whose `fpl_player_code` is absent from the current season means that player
    left the league. That is a legitimate skip, but `FotmobAdapter` counts and logs it rather
    than passing over it quietly.
    """

    fotmob_team_id: int
    fotmob_player_id: int
    fpl_player_code: int | None = None
    ignore: bool = False
    note: str | None = None


PLAYER_MAPPING_OVERRIDES: list[PlayerMappingOverride] = [
    PlayerMappingOverride(
        fotmob_team_id=9825,
        fotmob_player_id=795179,
        fpl_player_code=226597,
        note="Gabriel dos Santos Magalhães - Arsenal",
    ),
    PlayerMappingOverride(
        fotmob_team_id=10252,
        fotmob_player_id=610184,
        fpl_player_code=195546,
        note="Emiliano Buendía Stati (MID) - Aston Villa",
    ),
    PlayerMappingOverride(
        fotmob_team_id=8602,
        fotmob_player_id=1174672,
        fpl_player_code=448089,
        note="João Victor Gomes da Silva - Wolves in 2025/26, Aston Villa in 2026/27",
    ),
    PlayerMappingOverride(
        fotmob_team_id=8472,
        fotmob_player_id=1421003,
        fpl_player_code=604211,
        note="Timur Tuterov / Tutierov - Sunderland in 2025/26, not in the 2026/27 league",
    ),
]
