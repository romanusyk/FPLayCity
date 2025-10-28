"""
Core immutable data models for FPL data.

Classes:
- Team: FPL team with strength ratings (home/away, attack/defense)
- TeamFixture: Team's view of a specific fixture (score, difficulty, aggregated xG/xA from players)
- Fixture: Complete fixture with home/away teams, outcome, clean sheets
- PlayerFixture: Player's performance in a specific fixture (points, minutes, xG, xA, goals, assists)
- PlayerType: Enum for player positions (GKP, DEF, MID, FWD)
- Player: FPL player with type, team, cost, and position-specific point values

Collections (singletons):
- Teams: Indexed collection of all teams
- Fixtures: Indexed collection of all fixtures (by ID and gameweek)
- PlayerFixtures: Collection of all player-fixture records with lookup by fixture/team/player/gw
- Players: Dictionary of all players by ID
"""
from collections import defaultdict
from dataclasses import dataclass
from enum import Enum

from src.fpl.collection import Collection, SimpleIndex, ListIndex


@dataclass
class Team:

    team_id: int
    name: str
    strength_overall_home: int
    strength_overall_away: int
    strength_attack_home: int
    strength_attack_away: int
    strength_defence_home: int
    strength_defence_away: int

    def __repr__(self):
        return f'{self.name}'


@dataclass
class TeamFixture:

    fixture_id: int
    team_id: int
    difficulty: int
    score: int | None

    @property
    def fixture(self) -> 'Fixture':
        return Fixtures.get_one(fixture_id=self.fixture_id)

    @property
    def player_fixtures(self) -> list['PlayerFixture']:
        return PlayerFixtures.by_fixture_and_team(self.fixture_id, self.team_id)

    @property
    def expected_goals(self) -> float:
        return sum([(pf.expected_goals if pf.expected_goals else 0.) for pf in self.player_fixtures])

    @property
    def expected_assists(self) -> float:
        return sum([(pf.expected_assists if pf.expected_assists else 0.) for pf in self.player_fixtures])

    @property
    def defensive_contribution(self) -> int:
        return sum([(pf.defensive_contribution if pf.defensive_contribution else 0.) for pf in self.player_fixtures])

    @property
    def total_points(self) -> int:
        return sum([pf.total_points or 0. for pf in self.player_fixtures])


@dataclass
class Fixture:

    fixture_id: int
    finished: bool
    gameweek: int
    home: TeamFixture
    away: TeamFixture

    @property
    def home_clean_sheet(self) -> int:
        return int(self.away.score == 0)

    @property
    def away_clean_sheet(self) -> int:
        return int(self.home.score == 0)

    @property
    def outcome(self) -> str:
        if not self.finished:
            return 'none'
        if self.home.score > self.away.score:
            return 'home'
        if self.home.score == self.away.score:
            return 'draw'
        if self.home.score < self.away.score:
            return 'away'
        raise ValueError('Cannot define the outcome.')

    def __repr__(self):
        return f'({self.home.difficulty}){Teams.get_one(team_id=self.home.team_id)} {self.home.score}:{self.away.score} {Teams.get_one(team_id=self.away.team_id)}({self.away.difficulty})'


@dataclass
class PlayerFixture:

    player_id: int
    fixture_id: int
    gameweek: int
    was_home: bool
    total_points: int | None = None
    minutes: int | None = None
    goals_scored: int | None = None
    assists: int | None = None
    clean_sheets: int | None = None
    defensive_contribution: int | None = None
    expected_goals: float | None = None
    expected_assists: float | None = None
    expected_goal_involvements: float | None = None
    expected_goals_conceded: float | None = None
    value: int | None = None

    @property
    def side(self) -> str:
        return 'home' if self.was_home else 'away'

    @property
    def player(self) -> 'Player':
        return Players.by_id(self.player_id)

    @property
    def fixture(self) -> 'Fixture':
        return Fixtures.get_one(fixture_id=self.fixture_id)

    @property
    def team_id(self) -> int:
        return self.fixture.home.team_id if self.was_home else self.fixture.away.team_id

    @property
    def team(self) -> 'Team':
        return Teams.get_one(team_id=self.team_id)

    @property
    def opponent_team_id(self) -> int:
        return self.fixture.away.team_id if self.was_home else self.fixture.home.team_id

    @property
    def opponent_team(self) -> 'Team':
        return Teams.get_one(team_id=self.opponent_team_id)

    @property
    def team_fixture(self) -> 'TeamFixture':
        return self.fixture.home if self.was_home else self.fixture.away

    @property
    def expected_goals_share(self) -> float:
        team_xg = self.team_fixture.expected_goals
        return self.expected_goals / team_xg if team_xg > 0 else 0.

    @property
    def expected_assists_share(self) -> float:
        team_xa = self.team_fixture.expected_assists
        return self.expected_assists / team_xa if team_xa > 0 else 0.

    def __repr__(self):
        return (
            f'{self.player} in {self.fixture}: {self.minutes=}, {self.total_points=}, '
            f'xG: {self.expected_goals=} ({int(100 * self.expected_goals_share)}%), {self.goals_scored=} '
            f'xA: {self.expected_assists=} ({int(100 * self.expected_assists_share)}%), {self.assists=}'
        )


class PlayerType(Enum):

    GKP = 1
    DEF = 2
    MID = 3
    FWD = 4
    MNG = 5


@dataclass
class Player:

    player_id: int
    web_name: str
    player_type: PlayerType
    team_id: int
    now_cost: float

    @property
    def team(self) -> Team:
        return Teams.get_one(team_id=self.team_id)

    @property
    def clean_sheet_points(self) -> int:
        return {
            PlayerType.GKP: 4,
            PlayerType.DEF: 4,
            PlayerType.MID: 1,
        }.get(self.player_type, 0)

    @property
    def goal_points(self) -> int:
        return {
            PlayerType.GKP: 6,
            PlayerType.DEF: 6,
            PlayerType.MID: 5,
            PlayerType.FWD: 4,
        }.get(self.player_type, 0)

    @property
    def assist_points(self) -> int:
        return 3

    @property
    def dc_points(self) -> float:
        return {
            PlayerType.DEF: .1 / 10.,
            PlayerType.MID: .1 / 12.,
            PlayerType.FWD: .1 / 12.,
        }.get(self.player_type, 0.)

    def __repr__(self):
        return f'{self.web_name} ({self.player_type.name}) - {self.team.name}'


Teams = Collection[Team]([SimpleIndex('team_id')])

Fixtures = Collection[Fixture](
    simple_indices=[SimpleIndex('fixture_id')],
    list_indices=[ListIndex('gameweek')],
)


class PlayerFixtures:

    items: list[PlayerFixture] = []
    _by_fixture_and_team: dict[tuple[int, int], list[PlayerFixture]] = defaultdict(list)
    _by_fixture_and_player: dict[tuple[int, int], PlayerFixture] = {}

    @classmethod
    def add(cls, pf: PlayerFixture):
        cls.items.append(pf)
        cls._by_fixture_and_team[(pf.fixture_id, pf.team_id)].append(pf)
        assert (pf.fixture_id, pf.player_id) not in cls._by_fixture_and_player
        cls._by_fixture_and_player[(pf.fixture_id, pf.player_id)] = pf

    @classmethod
    def by_fixture_and_team(cls, fixture_id: int, team_id: int) -> list[PlayerFixture]:
        return cls._by_fixture_and_team[(fixture_id, team_id)]

    @classmethod
    def by_fixture_and_player(cls, fixture_id: int, player_id: int) -> PlayerFixture:
        return cls._by_fixture_and_player[(fixture_id, player_id)]

    @classmethod
    def by_player(cls, player_id: int):
        return [i for i in cls.items if i.player_id == player_id]

    @classmethod
    def by_fixture(cls, fixture_id: int) -> list[PlayerFixture]:
        return [i for i in cls.items if i.fixture_id == fixture_id]

    @classmethod
    def by_team(cls, team_id: int):
        return [
            i for i in cls.items
            if (
                Fixtures.get_one(fixture_id=i.fixture_id).home.team_id == team_id
                if i.was_home
                else Fixtures.get_one(fixture_id=i.fixture_id).away.team_id == team_id
            )
        ]

    @classmethod
    def by_gw(cls, gw: int):
        return [i for i in cls.items if Fixtures.get_one(fixture_id=i.fixture_id).gameweek == gw]

    @classmethod
    def by_team_and_gw(cls, team_id: int, gw: int):
        return [
            i for i in cls.items if (
                (
                    Fixtures.get_one(fixture_id=i.fixture_id).home.team_id == team_id
                    if i.was_home
                    else Fixtures.get_one(fixture_id=i.fixture_id).away.team_id == team_id
                )
                & (Fixtures.get_one(fixture_id=i.fixture_id).gameweek == gw)
            )
        ]


class Players:

    items_by_id: dict[int, Player] = {}

    @classmethod
    def add(cls, pl: Player):
        assert pl.player_id not in cls.items_by_id
        cls.items_by_id[pl.player_id] = pl

    @classmethod
    def by_id(cls, player_id: int) -> Player:
        return cls.items_by_id[player_id]

    @classmethod
    def by_team(cls, team_id: int):
        return [i for i in cls.items_by_id.values() if i.team_id == team_id]
