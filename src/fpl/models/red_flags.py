from dataclasses import dataclass

from src.fpl.models.immutable import PlayerFixture, Query


def get_last_finished_fixture(player_id: int) -> PlayerFixture | None:
    fixtures = Query.player_fixtures_by_player(player_id)
    for fixture in fixtures[::-1]:
        if fixture.fixture.finished:
            return fixture
    return None


@dataclass
class PlayerRegFlag:

    importance: float = 0.
    description: str = 'Reg flag'

    @classmethod
    def check(cls, player_id: int) -> 'PlayerRegFlag | None':
        raise NotImplementedError

    def __repr__(self):
        return f'{self.description} ({self.importance:.1f})'


@dataclass
class MissedLastGame(PlayerRegFlag):

    importance: float = 1.0
    description: str = '0 MP'

    @classmethod
    def check(cls, player_id: int) -> 'PlayerRegFlag | None':
        if fixture := get_last_finished_fixture(player_id):
            return cls() if fixture.minutes == 0 else None
        return None

    def __repr__(self):
        return f'{self.description}'


@dataclass
class ShortLastGame(PlayerRegFlag):
    importance: float = 0.7
    description: str = '<60 MP'

    @classmethod
    def check(cls, player_id: int) -> 'PlayerRegFlag | None':
        if fixture := get_last_finished_fixture(player_id):
            return cls() if fixture.minutes < 60 else None
        return None

    def __repr__(self):
        return f'{self.description}'


@dataclass
class Unavailable(PlayerRegFlag):
    importance: float = 1.0
    description: str = 'I'

    @classmethod
    def check(cls, player_id: int) -> 'PlayerRegFlag | None':
        chance = Query.player(player_id).chance_of_playing_next_round
        if chance is None or chance == 100:
            return None
        importance = (100 - chance) / 100.0
        return cls(importance=importance)

    def __repr__(self):
        return f'{self.description} {int(self.importance * 100):d}%'


@dataclass
class NotStartedLastGame(PlayerRegFlag):
    importance: float = 0.7
    description: str = 'B'

    @classmethod
    def check(cls, player_id: int) -> 'PlayerRegFlag | None':
        if fixture := get_last_finished_fixture(player_id):
            return cls() if fixture.starts == 0 else None
        return None

    def __repr__(self):
        return f'{self.description}'


all_red_flags: list[list[type[PlayerRegFlag]]] = [
    [Unavailable],
    [NotStartedLastGame],
    [MissedLastGame, ShortLastGame],
]


def build_red_flags(player_id: int) -> list[PlayerRegFlag]:
    result = []
    for flags in all_red_flags:
        for flag_cls in flags:
            if flag := flag_cls.check(player_id):
                result.append(flag)
                break
    return result
