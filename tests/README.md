# Test Suite for FPLayCity

## Overview

Unit tests for `immutable.py` covering Collections, Query facade, and data integrity.

## Test Structure

```
tests/
├── __init__.py              # Package marker
├── conftest.py              # Pytest configuration + data loading
├── test_immutable.py        # Main test file (39 tests)
└── README.md                # This file
```

## Test Coverage (39 tests)

### 1. Collections (15 tests)

**TestTeamsCollection** (2 tests):
- ✅ Get team by ID
- ✅ Non-existent team raises KeyError

**TestFixturesCollection** (3 tests):
- ✅ Get fixture by ID
- ✅ Get fixtures by gameweek
- ✅ Fixtures have home/away teams

**TestPlayersCollection** (3 tests):
- ✅ Get player by ID
- ✅ Get players by team
- ✅ Players have type and cost

**TestPlayerFixturesCollection** (7 tests):
- ✅ Get unique player fixture (fixture_id + player_id)
- ✅ Get by fixture and team
- ✅ Get by player
- ✅ Get by fixture
- ✅ Get by team (computed property!)
- ✅ Get by gameweek
- ✅ Get by team and gameweek

### 2. Unsupported Indices (3 tests)

**TestUnsupportedIndices** - Verify these combinations raise `KeyError`:
- ⚠️ PlayerFixtures by player_id + gameweek (NOT supported)
- ⚠️ Fixtures by team_id (NOT supported)
- ⚠️ Players by gameweek (NOT supported)

### 3. Query Facade (16 tests)

**TestQueryFacade** - All Query methods:
- ✅ `Query.team(id)` - Team lookup
- ✅ `Query.fixture(id)` - Fixture lookup
- ✅ `Query.fixtures_by_gameweek(gw)` - Fixtures in gameweek
- ✅ `Query.player(id)` - Player lookup
- ✅ `Query.players_by_team(id)` - Team roster
- ✅ `Query.player_by_name(name)` - Name search (case-insensitive)
- ✅ `Query.players_by_name(name)` - Multiple matches
- ✅ All 7 PlayerFixture query methods

### 4. Data Integrity (5 tests)

**TestDataIntegrity** - Relationships and computed properties:
- ✅ Player.team property
- ✅ PlayerFixture.player property
- ✅ PlayerFixture.fixture property
- ✅ PlayerFixture.team_id (computed from fixture + was_home)
- ✅ PlayerFixture.opponent_team_id (opposite team)

## Running Tests

See main [README.md](../README.md#testing) for commands.

## Key Insights

### What Works
- ✅ **All supported indices work perfectly** (15 tests)
- ✅ **Query facade provides clean API** (16 tests)
- ✅ **Computed properties can be indexed** (team_id from fixture)
- ✅ **Data relationships are solid** (5 integrity tests)

### What Fails (By Design)
- ⚠️ **Unsupported index combinations raise KeyError** (3 tests)
  - This is expected and validates the Collection system works correctly

## Test Data

Tests use real FPL data loaded via `bootstrap()` in `conftest.py`:
- **Teams:** 20 teams
- **Fixtures:** ~380 fixtures across 38 gameweeks
- **Players:** ~700 players
- **PlayerFixtures:** ~28,000 player-fixture records

Data is loaded once at test session start for fast test execution (~1.2s total).

