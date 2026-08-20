# Test Suite for FPLayCity

## Overview

Three groups of tests. `./run.sh -m pytest` runs all 187 in under two seconds; nothing here touches
the network.

- **Offline unit tests** (`test_prior_season.py`, `test_fotmob_friendlies.py`,
  `test_projection_models.py`, `test_opportunity.py`, `test_artifacts.py`) use hand-built payloads
  or a temporary working directory. They need no cached data at all.
- **Dataset tests** (`test_immutable.py`, `test_scoring.py`) read the real cached snapshots under
  `data/<season>/`. `test_immutable.py` depends on the `fpl_data` fixture, which runs
  `bootstrap()` once per session; `test_scoring.py` reads the element snapshots directly and
  skips if they are absent.
- **HTTP tests** (`test_web_api.py`) start the FastAPI app against the real snapshots and
  exercise every route. They are read-only: no run is written and no feedback is saved. Where a
  route needs a run that may not exist on a given machine, the test skips rather than writing
  artifacts into the real data directory.

## Test Structure

```
tests/
├── __init__.py                  # Package marker
├── conftest.py                  # `fpl_data` session fixture
├── test_immutable.py            # Collections + Query facade (needs dataset)
├── test_prior_season.py         # Prior-season reconciliation (offline)
├── test_fotmob_friendlies.py    # Match kinds, weighting, season rosters (offline)
├── test_scoring.py              # FPL scoring rules, reconciled against real matches
├── test_projection_models.py    # Minutes blend, DC shrinkage, VORP, tiers, methods (offline)
├── test_opportunity.py          # Cost of a single pick: gap to next, cost of waiting (offline)
├── test_artifacts.py            # Runs, feedback, draft state (temp directory)
├── test_web_api.py              # Every HTTP route, against the real snapshots
└── README.md                    # This file
```

## The load-bearing test

`test_scoring_reconciles_with_every_stored_player_match` re-scores all 23,165 stored 2025/26
player-matches from their raw fields and asserts exact equality with the `total_points` FPL
awarded. It is exact rather than tolerant on purpose: a single mismatch means the projection is
measuring its own arithmetic instead of the game, and every component model is built on top of
that function.

## Projection and app tests

`test_projection_models.py` pins the behaviour that is easy to break silently — which way
shrinkage pulls (two teammates on the same mean and different hit rates keep their ordering),
what a player with no evidence gets (zero, with `starts=0` visible), and how replacement level
moves during a draft. That last one is counter-intuitive and the tests spell it out: taking the
four best forwards leaves replacement level unchanged, and only a reach below the line raises it.
It also pins the *shape* of the pre-season role curve rather than just its direction — a hump,
highest for a player whose place was open — because a monotonic version was measured and rejected,
and a test asserting only "nailed starters get less" would pass on the wrong model.

`test_opportunity.py` covers the other half of the draft arithmetic, the part that does move on
every pick: the gap to the next player at a position, and what waiting a round costs. Its
load-bearing test is directional — taking the player *below* someone makes him more urgent, taking
the player *above* him changes nothing — which is the whole content of the metric.

`test_artifacts.py` runs entirely in a `tmp_path`, so nothing touches the real `data/`. It covers
the refusals: runs cannot be overwritten, a malformed run id never reaches the filesystem,
pruning never deletes a run that feedback refers to, and a corrupt draft-state file raises rather
than silently starting from empty.

`test_web_api.py` asserts, among other things, that components sum to the projected total, that
sample sizes travel with every number on the board, and that an unknown run id is a 404 rather
than an empty table.

## Offline tests

`test_prior_season.py` pins the reconciliation invariant behind the prior-season baseline:
bootstrap mirrors `history_past` exactly for players who stayed at a club, and is unreliable
(zeroed or truncated) for players who moved. It covers both repair paths, the loud failures,
and the deliberate `None` for players with no prior Premier League season.

`test_fotmob_friendlies.py` covers `MatchKind` classification, the weighted `PlayerSquadRole`
(friendly starts count 0.35, availability is never discounted), lineup-less friendlies, the
per-season club rosters including the 2026/27 promotion and relegation, and `PreseasonRole`
weighting — including a regression test that one Community Shield start beats four friendly ones.

## Test Coverage — `test_immutable.py` (39 tests)

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
- ⚠️ **Unsupported index combinations raise KeyError** (2 tests)
  - This is expected and validates the Collection system works correctly
  - `(player_id, gameweek)` on `PlayerFixtures` is *supported* and returns an empty list, because
    `Query.player_fixtures_by_player_and_gameweeks` walks ranges that include blank gameweeks

## Test Data

Tests use real FPL data loaded via `bootstrap()` in `conftest.py`:
- **Teams:** 20 teams
- **Fixtures:** ~380 fixtures across 38 gameweeks
- **Players:** ~600 players
- **PlayerFixtures:** ~28,000 player-fixture records

Data is loaded once at test session start. The collections are process-level singletons, so a
second loader in the same session calls `Collection.clear()` first — which is why the HTTP tests
can build the app after `test_immutable.py` has already run `bootstrap()`.

## Related Docs

- What the projection produces — `src/fpl/projection/README.md`
- What the app serves — `src/web/README.md`
- Repo conventions and known traps — `CLAUDE.md`

