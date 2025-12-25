from src.fpl.const import GameMode
from src.fpl.models.immutable import PlayerPresence


def draft_presence_json_to_player_presence(row: dict, gameweek: int, manager_id: int, is_mine: bool) -> PlayerPresence:
    """Convert a draft presence row into a PlayerPresence dataclass."""
    return PlayerPresence(
        game_mode=GameMode.draft,
        manager_id=manager_id,
        is_mine=is_mine,
        player_id=row["element"],
        gameweek=gameweek,
        position=row["position"],
        is_captain=row["is_captain"],
        is_vice_captain=row["is_vice_captain"],
        multiplier=row["multiplier"],
    )
