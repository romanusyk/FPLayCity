from datetime import datetime
from pydantic import BaseModel


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
    match_id: int
    event_time: datetime
    opponent_team: FotmobTeam
    starters: list[FotmobPlayer]
    benched: list[FotmobPlayer]
    unavailable: list[FotmobPlayer]
    subs_log: list[Substitution]
    league_name: str


