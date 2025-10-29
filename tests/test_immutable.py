"""
Unit tests for immutable.py collections and Query facade.

Tests cover:
- Collection lookups (supported indices)
- Query facade methods
- Unsupported index combinations (should raise KeyError)
"""
import pytest

from src.fpl.models.immutable import (
    Teams, Fixtures, Players, PlayerFixtures, Query,
    Team, Fixture, Player, PlayerFixture, PlayerType,
)


class TestTeamsCollection:
    """Test Teams collection."""
    
    def test_get_team_by_id(self):
        """Can get team by ID."""
        team = Teams.get_one(team_id=1)
        assert isinstance(team, Team)
        assert team.team_id == 1
        assert team.name  # Has a name
    
    def test_get_nonexistent_team_raises(self):
        """Getting non-existent team raises KeyError."""
        with pytest.raises(KeyError):
            Teams.get_one(team_id=99999)


class TestFixturesCollection:
    """Test Fixtures collection."""
    
    def test_get_fixture_by_id(self):
        """Can get fixture by ID."""
        # Get first fixture from items
        first_fixture = Fixtures.items[0]
        fixture = Fixtures.get_one(fixture_id=first_fixture.fixture_id)
        assert isinstance(fixture, Fixture)
        assert fixture.fixture_id == first_fixture.fixture_id
    
    def test_get_fixtures_by_gameweek(self):
        """Can get all fixtures in a gameweek."""
        fixtures = Fixtures.get_list(gameweek=1)
        assert len(fixtures) > 0
        assert all(f.gameweek == 1 for f in fixtures)
        assert all(isinstance(f, Fixture) for f in fixtures)
    
    def test_fixtures_have_teams(self):
        """Fixtures have home and away teams."""
        fixture = Fixtures.items[0]
        assert fixture.home.team_id > 0
        assert fixture.away.team_id > 0


class TestPlayersCollection:
    """Test Players collection."""
    
    def test_get_player_by_id(self):
        """Can get player by ID."""
        player = Players.get_one(player_id=1)
        assert isinstance(player, Player)
        assert player.player_id == 1
    
    def test_get_players_by_team(self):
        """Can get all players in a team."""
        players = Players.get_list(team_id=1)
        assert len(players) > 0
        assert all(p.team_id == 1 for p in players)
        assert all(isinstance(p, Player) for p in players)
    
    def test_player_has_type_and_cost(self):
        """Players have type and cost."""
        player = Players.items[0]
        assert isinstance(player.player_type, PlayerType)
        assert player.now_cost > 0


class TestPlayerFixturesCollection:
    """Test PlayerFixtures collection with various indices."""
    
    def test_get_player_fixture_unique(self):
        """Can get unique player fixture by fixture_id + player_id."""
        # Get a sample to test with
        sample = PlayerFixtures.items[0]
        pf = PlayerFixtures.get_one(
            fixture_id=sample.fixture_id,
            player_id=sample.player_id,
        )
        assert isinstance(pf, PlayerFixture)
        assert pf.fixture_id == sample.fixture_id
        assert pf.player_id == sample.player_id
    
    def test_get_player_fixtures_by_fixture_and_team(self):
        """Can get all players from a team in a fixture."""
        sample = PlayerFixtures.items[0]
        pfs = PlayerFixtures.get_list(
            fixture_id=sample.fixture_id,
            team_id=sample.team_id,
        )
        assert len(pfs) > 0
        assert all(pf.fixture_id == sample.fixture_id for pf in pfs)
        assert all(pf.team_id == sample.team_id for pf in pfs)
    
    def test_get_player_fixtures_by_player(self):
        """Can get all fixtures for a player."""
        sample = PlayerFixtures.items[0]
        pfs = PlayerFixtures.get_list(player_id=sample.player_id)
        assert len(pfs) > 0
        assert all(pf.player_id == sample.player_id for pf in pfs)
    
    def test_get_player_fixtures_by_fixture(self):
        """Can get all player fixtures in a fixture."""
        sample = PlayerFixtures.items[0]
        pfs = PlayerFixtures.get_list(fixture_id=sample.fixture_id)
        assert len(pfs) > 0
        assert all(pf.fixture_id == sample.fixture_id for pf in pfs)
    
    def test_get_player_fixtures_by_team(self):
        """Can get all player fixtures for a team (computed property!)."""
        pfs = PlayerFixtures.get_list(team_id=1)
        assert len(pfs) > 0
        assert all(pf.team_id == 1 for pf in pfs)
    
    def test_get_player_fixtures_by_gameweek(self):
        """Can get all player fixtures in a gameweek."""
        pfs = PlayerFixtures.get_list(gameweek=1)
        assert len(pfs) > 0
        assert all(pf.gameweek == 1 for pf in pfs)
    
    def test_get_player_fixtures_by_team_and_gameweek(self):
        """Can get player fixtures for team in gameweek."""
        pfs = PlayerFixtures.get_list(team_id=1, gameweek=1)
        assert len(pfs) > 0
        assert all(pf.team_id == 1 and pf.gameweek == 1 for pf in pfs)


class TestUnsupportedIndices:
    """Test that unsupported index combinations raise KeyError."""
    
    def test_player_fixtures_by_player_and_gameweek_unsupported(self):
        """
        Querying by player_id + gameweek is NOT supported.
        
        This index combination doesn't exist in PlayerFixtures.
        Should raise KeyError.
        """
        with pytest.raises(KeyError):
            PlayerFixtures.get_list(player_id=1, gameweek=1)
    
    def test_fixtures_by_team_unsupported(self):
        """
        Querying fixtures by team_id is NOT supported.
        
        Fixtures don't have a direct team_id field.
        Should raise KeyError.
        """
        with pytest.raises(KeyError):
            Fixtures.get_list(team_id=1)
    
    def test_players_by_gameweek_unsupported(self):
        """
        Querying players by gameweek is NOT supported.
        
        Players don't have gameweek information.
        Should raise KeyError.
        """
        with pytest.raises(KeyError):
            Players.get_list(gameweek=1)


class TestQueryFacade:
    """Test Query facade for easy data access."""
    
    def test_query_team(self):
        """Query.team() returns team by ID."""
        team = Query.team(1)
        assert isinstance(team, Team)
        assert team.team_id == 1
    
    def test_query_fixture(self):
        """Query.fixture() returns fixture by ID."""
        sample = Fixtures.items[0]
        fixture = Query.fixture(sample.fixture_id)
        assert isinstance(fixture, Fixture)
        assert fixture.fixture_id == sample.fixture_id
    
    def test_query_fixtures_by_gameweek(self):
        """Query.fixtures_by_gameweek() returns all fixtures."""
        fixtures = Query.fixtures_by_gameweek(1)
        assert len(fixtures) > 0
        assert all(f.gameweek == 1 for f in fixtures)
    
    def test_query_player(self):
        """Query.player() returns player by ID."""
        player = Query.player(1)
        assert isinstance(player, Player)
        assert player.player_id == 1
    
    def test_query_players_by_team(self):
        """Query.players_by_team() returns all team players."""
        players = Query.players_by_team(1)
        assert len(players) > 0
        assert all(p.team_id == 1 for p in players)
    
    def test_query_player_by_name(self):
        """Query.player_by_name() finds player by partial name match."""
        # Get first player's name
        first_player = Players.items[0]
        partial_name = first_player.web_name[:3]  # First 3 chars
        
        player = Query.player_by_name(partial_name)
        assert isinstance(player, Player)
        assert partial_name.lower() in player.web_name.lower()
    
    def test_query_player_by_name_case_insensitive(self):
        """Query.player_by_name() is case-insensitive."""
        first_player = Players.items[0]
        partial_name = first_player.web_name[:3].upper()  # UPPERCASE
        
        player = Query.player_by_name(partial_name)
        assert isinstance(player, Player)
    
    def test_query_player_by_name_not_found_raises(self):
        """Query.player_by_name() raises if no match found."""
        with pytest.raises(StopIteration):
            Query.player_by_name("ZZZZNONEXISTENTPLAYERZZZ")
    
    def test_query_players_by_name(self):
        """Query.players_by_name() returns all matching players."""
        # Use a common name part that might match multiple players
        first_player = Players.items[0]
        partial_name = first_player.web_name[:2]  # Very short to get multiple
        
        players = Query.players_by_name(partial_name)
        assert len(players) >= 1
        assert all(partial_name.lower() in p.web_name.lower() for p in players)
    
    def test_query_player_fixture(self):
        """Query.player_fixture() returns unique player fixture."""
        sample = PlayerFixtures.items[0]
        pf = Query.player_fixture(sample.fixture_id, sample.player_id)
        assert isinstance(pf, PlayerFixture)
        assert pf.fixture_id == sample.fixture_id
        assert pf.player_id == sample.player_id
    
    def test_query_player_fixtures_by_fixture_and_team(self):
        """Query.player_fixtures_by_fixture_and_team() returns team's players."""
        sample = PlayerFixtures.items[0]
        pfs = Query.player_fixtures_by_fixture_and_team(
            sample.fixture_id,
            sample.team_id,
        )
        assert len(pfs) > 0
    
    def test_query_player_fixtures_by_player(self):
        """Query.player_fixtures_by_player() returns all player fixtures."""
        sample = PlayerFixtures.items[0]
        pfs = Query.player_fixtures_by_player(sample.player_id)
        assert len(pfs) > 0
        assert all(pf.player_id == sample.player_id for pf in pfs)
    
    def test_query_player_fixtures_by_fixture(self):
        """Query.player_fixtures_by_fixture() returns all in fixture."""
        sample = PlayerFixtures.items[0]
        pfs = Query.player_fixtures_by_fixture(sample.fixture_id)
        assert len(pfs) > 0
    
    def test_query_player_fixtures_by_team(self):
        """Query.player_fixtures_by_team() uses computed property."""
        pfs = Query.player_fixtures_by_team(1)
        assert len(pfs) > 0
        assert all(pf.team_id == 1 for pf in pfs)
    
    def test_query_player_fixtures_by_gameweek(self):
        """Query.player_fixtures_by_gameweek() returns all in gameweek."""
        pfs = Query.player_fixtures_by_gameweek(1)
        assert len(pfs) > 0
        assert all(pf.gameweek == 1 for pf in pfs)
    
    def test_query_player_fixtures_by_team_and_gameweek(self):
        """Query.player_fixtures_by_team_and_gameweek() filters both."""
        pfs = Query.player_fixtures_by_team_and_gameweek(1, 1)
        assert len(pfs) > 0
        assert all(pf.team_id == 1 and pf.gameweek == 1 for pf in pfs)


class TestDataIntegrity:
    """Test data relationships and computed properties."""
    
    def test_player_has_team(self):
        """Player.team property returns valid Team."""
        player = Players.items[0]
        team = player.team
        assert isinstance(team, Team)
        assert team.team_id == player.team_id
    
    def test_player_fixture_has_player(self):
        """PlayerFixture.player property returns valid Player."""
        pf = PlayerFixtures.items[0]
        player = pf.player
        assert isinstance(player, Player)
        assert player.player_id == pf.player_id
    
    def test_player_fixture_has_fixture(self):
        """PlayerFixture.fixture property returns valid Fixture."""
        pf = PlayerFixtures.items[0]
        fixture = pf.fixture
        assert isinstance(fixture, Fixture)
        assert fixture.fixture_id == pf.fixture_id
    
    def test_player_fixture_team_id_computed(self):
        """PlayerFixture.team_id is computed from fixture + was_home."""
        pf = PlayerFixtures.items[0]
        team_id = pf.team_id
        
        # Verify it matches fixture home/away based on was_home
        if pf.was_home:
            assert team_id == pf.fixture.home.team_id
        else:
            assert team_id == pf.fixture.away.team_id
    
    def test_player_fixture_opponent_team_id_computed(self):
        """PlayerFixture.opponent_team_id is opposite of team_id."""
        pf = PlayerFixtures.items[0]
        opponent_id = pf.opponent_team_id
        
        # Verify it's the opposite team
        if pf.was_home:
            assert opponent_id == pf.fixture.away.team_id
        else:
            assert opponent_id == pf.fixture.home.team_id

