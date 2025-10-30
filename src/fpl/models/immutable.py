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
- Players: Indexed collection of all players

Facade:
- Query: Convenient facade providing readable methods for all collection indices
  - Team lookups: team(id)
  - Fixture lookups: fixture(id), fixtures_by_gameweek(gw)
  - Player lookups: player(id), players_by_team(id), player_by_name(name)
  - PlayerFixture lookups: All supported index combinations
"""
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
    def team(self) -> Team:
        return Teams.get_one(team_id=self.team_id)

    @property
    def opponent_team(self) -> Team:
        opponent_team_id = (
            self.fixture.home.team_id
            if self.fixture.away.team_id == self.team_id
            else self.fixture.away.team_id
        )
        return Teams.get_one(team_id=opponent_team_id)

    @property
    def player_fixtures(self) -> list['PlayerFixture']:
        return PlayerFixtures.get_list(fixture_id=self.fixture_id, team_id=self.team_id)

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
        return Players.get_one(player_id=self.player_id)

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


PlayerFixtures = Collection[PlayerFixture](
    simple_indices=[
        SimpleIndex('fixture_id', 'player_id'),
    ],
    list_indices=[
        ListIndex('fixture_id', 'team_id'),
        ListIndex('player_id'),
        ListIndex('fixture_id'),
        ListIndex('team_id'),
        ListIndex('gameweek'),
        ListIndex('team_id', 'gameweek'),
    ],
)


Players = Collection[Player](
    simple_indices=[SimpleIndex('player_id')],
    list_indices=[ListIndex('team_id')],
)


class Query:
    """
    Facade for easy access to all collections.
    
    Provides readable method names for all supported indices.
    All methods are stateless and delegate to the underlying collections.
    """
    
    # --- Teams ---
    
    @staticmethod
    def team(team_id: int) -> Team:
        """Get team by ID."""
        return Teams.get_one(team_id=team_id)
    
    @staticmethod
    def all_teams() -> list[Team]:
        """Get all teams."""
        return Teams.items
    
    # --- Fixtures ---
    
    @staticmethod
    def fixture(fixture_id: int) -> Fixture:
        """Get fixture by ID."""
        return Fixtures.get_one(fixture_id=fixture_id)
    
    @staticmethod
    def fixtures_by_gameweek(gameweek: int) -> list[Fixture]:
        """Get all fixtures in a gameweek."""
        return Fixtures.get_list(gameweek=gameweek)
    
    # --- PlayerFixtures ---
    
    @staticmethod
    def player_fixture(fixture_id: int, player_id: int) -> PlayerFixture:
        """Get specific player's fixture (unique)."""
        return PlayerFixtures.get_one(fixture_id=fixture_id, player_id=player_id)
    
    @staticmethod
    def player_fixtures_by_fixture_and_team(fixture_id: int, team_id: int) -> list[PlayerFixture]:
        """Get all players from a team in a specific fixture."""
        return PlayerFixtures.get_list(fixture_id=fixture_id, team_id=team_id)
    
    @staticmethod
    def player_fixtures_by_player(player_id: int) -> list[PlayerFixture]:
        """Get all fixtures for a player."""
        return PlayerFixtures.get_list(player_id=player_id)
    
    @staticmethod
    def player_fixtures_by_fixture(fixture_id: int) -> list[PlayerFixture]:
        """Get all player fixtures in a specific fixture."""
        return PlayerFixtures.get_list(fixture_id=fixture_id)
    
    @staticmethod
    def player_fixtures_by_team(team_id: int) -> list[PlayerFixture]:
        """Get all player fixtures for a team (uses computed property)."""
        return PlayerFixtures.get_list(team_id=team_id)
    
    @staticmethod
    def player_fixtures_by_gameweek(gameweek: int) -> list[PlayerFixture]:
        """Get all player fixtures in a gameweek."""
        return PlayerFixtures.get_list(gameweek=gameweek)
    
    @staticmethod
    def player_fixtures_by_team_and_gameweek(team_id: int, gameweek: int) -> list[PlayerFixture]:
        """Get all player fixtures for a team in a specific gameweek."""
        return PlayerFixtures.get_list(team_id=team_id, gameweek=gameweek)
    
    # --- Players ---
    
    @staticmethod
    def player(player_id: int) -> Player:
        """Get player by ID."""
        return Players.get_one(player_id=player_id)
    
    @staticmethod
    def players_by_team(team_id: int) -> list[Player]:
        """Get all players in a team."""
        return Players.get_list(team_id=team_id)
    
    @staticmethod
    def all_players() -> list[Player]:
        """Get all players."""
        return Players.items
    
    @staticmethod
    def player_by_name(name: str) -> Player:
        """
        Find player by name (case-insensitive partial match).
        
        Args:
            name: Player name or partial name to search for
            
        Returns:
            Player: First matching player
            
        Raises:
            StopIteration: If no player found with that name
            
        Example:
            >>> Query.player_by_name("Salah")
            >>> Query.player_by_name("haaland")
        """
        return next(
            p for p in Players.items
            if name.lower() in p.web_name.lower()
        )
    
    @staticmethod
    def players_by_name(name: str) -> list[Player]:
        """
        Find all players matching name (case-insensitive partial match).
        
        Useful when multiple players match (e.g., "Silva").
        
        Args:
            name: Player name or partial name to search for
            
        Returns:
            list[Player]: All matching players
            
        Example:
            >>> Query.players_by_name("Silva")  # Returns B. Silva, Nunes, etc.
        """
        return [
            p for p in Players.items
            if name.lower() in p.web_name.lower()
        ]
