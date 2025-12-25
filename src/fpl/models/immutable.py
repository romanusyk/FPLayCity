"""
Core immutable data models for FPL data.

Classes:
- Team: FPL team with strength ratings (home/away, attack/defense)
- TeamFixture: Team's view of a specific fixture (score, difficulty, aggregated xG/xA from players)
- Fixture: Complete fixture with home/away teams, outcome, clean sheets
- PlayerFixture: Player's performance in a specific fixture (points, minutes, xG, xA, goals, assists)
- PlayerType: Enum for player positions (GKP, DEF, MID, FWD)
- Player: FPL player with type, team, cost, and position-specific point values
- Tag: News article tag with id and label
- NewsModel: News article with metadata, content, tags, gameweek assignment, and collection source
- NewsFact: Extracted fact about a player from a news article

Collections (singletons):
- Teams: Indexed collection of all teams
- Fixtures: Indexed collection of all fixtures (by ID and gameweek)
- PlayerFixtures: Collection of all player-fixture records with lookup by fixture/team/player/gw
- Players: Indexed collection of all players
- Gameweeks: Indexed collection of all gameweeks
- News: Indexed collection of all news articles (by ID, gameweek, collection, and gameweek+collection)
- NewsFacts: Indexed collection of all news facts

Facade:
- Query: Convenient facade providing readable methods for all collection indices
  - Team lookups: team(id)
  - Fixture lookups: fixture(id), fixtures_by_gameweek(gw)
  - Player lookups: player(id), players_by_team(id), player_by_name(name)
  - PlayerFixture lookups: All supported index combinations
  - Gameweek lookups: gameweek(id), all_gameweeks()
  - News lookups: news(id), news_by_gameweek(gw), news_by_collection(collection), news_by_gameweek_and_collection(gw, collection)
  - NewsFact lookups: news_facts_by_player(id, gw), news_facts_by_gameweek(gw)
"""
from dataclasses import asdict, dataclass
from enum import Enum
from datetime import datetime

from src.fpl.const import GameMode
from src.fpl.collection import Collection, SimpleIndex, ListIndex


@dataclass
class Team:

    team_id: int
    short_name: str
    name: str
    strength_overall_home: int
    strength_overall_away: int
    strength_attack_home: int
    strength_attack_away: int
    strength_defence_home: int
    strength_defence_away: int

    def __repr__(self):
        return f'{self.name}'


class Metric(Enum):
    XGC = 'egc'
    CS = 'cs'
    XG = 'xg'
    XA = 'xa'
    DC = 'dc'
    MP = 'mp'
    PTS = 'pts'


class Measurable:

    def get_metric(self, metric: Metric) -> float:
        raise NotImplementedError


@dataclass
class TeamFixture(Measurable):

    fixture_id: int
    team_id: int
    difficulty: int
    gameweek: int
    score: int | None

    @property
    def fixture(self) -> 'Fixture':
        return Fixtures.get_one(fixture_id=self.fixture_id)

    @property
    def team(self) -> Team:
        return Teams.get_one(team_id=self.team_id)

    @property
    def opponent_team_fixture(self) -> 'TeamFixture':
        return self.fixture.away if self.team_id == self.fixture.home.team_id else self.fixture.home

    @property
    def opponent_team(self) -> Team:
        return self.opponent_team_fixture.team

    @property
    def side(self) -> str:
        return 'home' if self.team_id == self.fixture.home.team_id else 'away'

    @property
    def clean_sheet(self) -> int:
        return int(self.opponent_team_fixture.score == 0)

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

    @property
    def opponent_expected_goals(self) -> float:
        return self.opponent_team_fixture.expected_goals

    def get_metric(self, metric: Metric) -> float:
        return {
            Metric.XGC: self.opponent_expected_goals,
            Metric.CS: self.clean_sheet,
            Metric.XG: self.expected_goals,
            Metric.XA: self.expected_assists,
            Metric.DC: self.defensive_contribution,
            Metric.PTS: self.total_points,
        }[metric]


@dataclass
class Fixture:

    fixture_id: int
    finished: bool
    gameweek: int
    home: TeamFixture
    away: TeamFixture

    def get_metric(self, metric: Metric, team_id: int) -> float:
        team_fixture = self.home if team_id == self.home.team_id else self.away
        return team_fixture.get_metric(metric)

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
class PlayerFixture(Measurable):

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
    starts: int | None = None

    def get_metric(self, metric: Metric) -> float:
        return {
            Metric.XGC: self.expected_goals_conceded,
            Metric.CS: self.clean_sheets,
            Metric.XG: self.expected_goals,
            Metric.XA: self.expected_assists,
            Metric.DC: self.defensive_contribution,
            Metric.MP: self.minutes,
            Metric.PTS: self.total_points,
        }[metric]

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
    first_name: str
    second_name: str
    web_name: str
    player_type: PlayerType
    team_id: int
    now_cost: float
    status: str
    chance_of_playing_next_round: int
    chance_of_playing_this_round: int
    news: str
    minutes: int

    @property
    def team(self) -> Team:
        return Teams.get_one(team_id=self.team_id)

    @property
    def full_name(self) -> str:
        return f'{self.first_name} {self.second_name}'.strip()

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
        full_name = f'{self.first_name} {self.second_name}'.strip()
        return f'[{self.player_id}] {self.web_name or full_name} ({self.player_type.name}) - {self.team.name}'


@dataclass
class PlayerPresence:
    game_mode: GameMode
    manager_id: int
    is_mine: bool
    player_id: int
    gameweek: int
    position: int
    is_captain: bool
    is_vice_captain: bool
    multiplier: int


@dataclass
class Gameweek:

    gameweek: int
    deadline_time: datetime

    def __repr__(self):
        return f'GW{self.gameweek} ({self.deadline_time.isoformat()})'


@dataclass
class Tag:

    id: int
    label: str


@dataclass
class NewsModel:

    id: int
    url: str
    date: str
    lastUpdated: str
    title: str
    summary: str
    body: str
    tags: list[Tag]
    gameweek: int
    collection: str

    def __repr__(self):
        return f'NewsModel(id={self.id}, title="{self.title[:50]}...", gw={self.gameweek}, collection={self.collection})'


@dataclass
class NewsFact:

    player_id: int
    news_id: int
    next_gameweek: int
    fact: str
    form: float
    availability: float

    def __repr__(self):
        return f'NewsFact(player={self.player_id}, gw={self.next_gameweek}, form={self.form}, avail={self.availability})'


Teams = Collection[Team]([SimpleIndex('team_id')])

TeamFixtures = Collection[TeamFixture](
    simple_indices=[SimpleIndex('fixture_id', 'team_id')],
    list_indices=[ListIndex('team_id', 'gameweek')],
)

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
        ListIndex('player_id', 'gameweek', default_factory=list),
    ],
)

Players = Collection[Player](
    simple_indices=[SimpleIndex('player_id')],
    list_indices=[ListIndex('team_id')],
)


PlayerPresences = Collection[PlayerPresence](
    simple_indices=[SimpleIndex('manager_id', 'player_id', 'gameweek')],
    list_indices=[
        ListIndex('manager_id', 'gameweek'),
        ListIndex('game_mode', 'is_mine', 'gameweek'),
    ],
)


Gameweeks = Collection[Gameweek](
    simple_indices=[SimpleIndex('gameweek')],
)


News = Collection[NewsModel](
    simple_indices=[SimpleIndex('id', default_value=None)],
    list_indices=[
        ListIndex('gameweek', default_factory=list),
        ListIndex('collection', default_factory=list),
        ListIndex('gameweek', 'collection', default_factory=list),
    ],
)


NewsFacts = Collection[NewsFact](
    simple_indices=[],
    list_indices=[
        ListIndex('player_id', default_factory=list),
        ListIndex('next_gameweek', default_factory=list),
        ListIndex('player_id', 'next_gameweek', default_factory=list),
    ],
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

    # --- TeamFixtures ---

    @staticmethod
    def team_fixture(fixture_id: int, team_id: int) -> TeamFixture:
        """Get team fixture by fixture and team."""
        return TeamFixtures.get_one(fixture_id=fixture_id, team_id=team_id)

    @staticmethod
    def team_fixtures_by_team_and_gameweek(team_id: int, gameweek: int) -> list[TeamFixture]:
        """Get all team fixtures for a team in a specific gameweek."""
        return TeamFixtures.get_list(team_id=team_id, gameweek=gameweek)

    @staticmethod
    def team_fixtures_by_team_and_gameweeks(team_id: int, first_gw: int, last_gw: int) -> list[TeamFixture]:
        """Get all team fixtures for a team in a range of gameweeks."""
        return [
            tf for gw in range(first_gw, last_gw + 1)
            for tf in TeamFixtures.get_list(team_id=team_id, gameweek=gw)
        ]

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

    @staticmethod
    def player_fixtures_by_player_and_gameweek(player_id: int, gameweek: int) -> list[PlayerFixture]:
        """Get all player fixtures for a player in a specific gameweek."""
        return PlayerFixtures.get_list(player_id=player_id, gameweek=gameweek)

    @staticmethod
    def player_fixtures_by_player_and_gameweeks(player_id: int, first_gw: int, last_gw: int) -> list[PlayerFixture]:
        """Get all player fixtures for a player in a range of gameweeks."""
        return [
            pf 
            for gw in range(first_gw, last_gw + 1)
            for pf in PlayerFixtures.get_list(player_id=player_id, gameweek=gw)
        ]

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

    # --- Player Presences ---

    @staticmethod
    def my_fpl_presence_ids(gameweek: int) -> set[int]:
        """Get all my FPL presence player IDs for a gameweek."""
        return {
            pp.player_id for pp in PlayerPresences.get_list(game_mode=GameMode.fpl, is_mine=True, gameweek=gameweek)
        }

    @staticmethod
    def my_draft_presence_ids(gameweek: int) -> set[int]:
        """Get all my draft presence player IDs for a gameweek."""
        return {
            pp.player_id for pp in PlayerPresences.get_list(game_mode=GameMode.draft, is_mine=True, gameweek=gameweek)
        }

    @staticmethod
    def all_draft_presence_ids(gameweek: int) -> set[int]:
        """Get all draft presence player IDs for a gameweek."""
        return {
            pp.player_id for pp in (
                PlayerPresences.get_list(game_mode=GameMode.draft, is_mine=True, gameweek=gameweek) +
                PlayerPresences.get_list(game_mode=GameMode.draft, is_mine=False, gameweek=gameweek)
            )
        }

    # --- Gameweeks ---

    @staticmethod
    def gameweek(gameweek: int) -> Gameweek:
        """Get gameweek by ID."""
        return Gameweeks.get_one(gameweek=gameweek)

    @staticmethod
    def all_gameweeks() -> list[Gameweek]:
        """Get all gameweeks."""
        return Gameweeks.items

    # --- News ---

    @staticmethod
    def news(news_id: int) -> NewsModel:
        """Get news by ID."""
        return News.get_one(id=news_id)

    @staticmethod
    def news_by_gameweek(gameweek: int) -> list[NewsModel]:
        """Get all news for a gameweek."""
        return News.get_list(gameweek=gameweek)

    @staticmethod
    def news_by_collection(collection: str) -> list[NewsModel]:
        """Get all news from a collection."""
        return News.get_list(collection=collection)

    @staticmethod
    def news_by_gameweek_and_collection(gameweek: int, collection: str) -> list[NewsModel]:
        """Get all news for a gameweek from a specific collection."""
        return News.get_list(gameweek=gameweek, collection=collection)

    @staticmethod
    def raw_news(news_id: int) -> dict:
        """Get full raw article by ID (as dict)."""
        return asdict(News.get_one(id=news_id))

    @staticmethod
    def raw_news_by_gameweek(gameweek: int) -> list[dict]:
        """Get all raw articles for a gameweek (as dicts)."""
        return [asdict(n) for n in News.get_list(gameweek=gameweek)]

    # --- News Facts ---

    @staticmethod
    def news_facts_by_player(player_id: int, gameweek: int | None = None) -> list[NewsFact]:
        """Get all facts for a player, optionally filtered by gameweek."""
        if gameweek is not None:
            return NewsFacts.get_list(player_id=player_id, next_gameweek=gameweek)
        return NewsFacts.get_list(player_id=player_id)

    @staticmethod
    def news_facts_by_gameweek(gameweek: int) -> list[NewsFact]:
        """Get all facts relevant to a gameweek."""
        return NewsFacts.get_list(next_gameweek=gameweek)
