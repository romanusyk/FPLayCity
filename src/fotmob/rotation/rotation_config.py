from __future__ import annotations

from pydantic import BaseModel, Field


class RotationConfig(BaseModel):
    first_team_start_ratio: float = 0.8
    min_subs_for_rival: int = 1
    included_leagues: list[str] = Field(default_factory=lambda: ["Premier League"])


class PlayerMappingOverride(BaseModel):
    fotmob_team_id: int
    fotmob_player_id: int
    fpl_team_id: int | None = None
    fpl_player_id: int | None = None
    ignore: bool = False
    note: str | None = None


PLAYER_MAPPING_OVERRIDES: list[PlayerMappingOverride] = [
    PlayerMappingOverride(
        fotmob_team_id=9825,
        fotmob_player_id=795179,
        fpl_team_id=1,
        fpl_player_id=5,
        note="Gabriel dos Santos Magalhães",
    ),
    PlayerMappingOverride(
        fotmob_team_id=10252,
        fotmob_player_id=610184,
        fpl_team_id=2,
        fpl_player_id=50,
        note="Emiliano Buendía Stati (MID) - Aston Villa",
    ),
    PlayerMappingOverride(
        fotmob_team_id=8602,
        fotmob_player_id=1174672,
        fpl_team_id=20,
        fpl_player_id=646,
        note="João Victor Gomes da Silva",
    ),
    PlayerMappingOverride(
        fotmob_team_id=8602,
        fotmob_player_id=1174672,
        fpl_team_id=20,
        fpl_player_id=646,
        note="João Victor Gomes da Silva",
    ),
]
