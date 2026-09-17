# Test Suite for FPLayCity

## Overview

Three groups of tests. `./run.sh -m pytest` runs all 249 in a few seconds. Nothing here fetches
anything it already has: the dataset fixture calls `bootstrap()` with `freshness=1000`, so every
snapshot comes off disk. A snapshot that is genuinely *missing* is still fetched - which is what
makes a fresh checkout work, and the one case where the suite touches the network.

- **Offline unit tests** (`test_prior_season.py`, `test_fotmob_friendlies.py`,
  `test_projection_models.py`, `test_opportunity.py`, `test_waivers.py`,
  `test_draft_league.py`, `test_fpl_squad.py`, `test_artifacts.py`,
  `test_project_horizon.py`, `test_transfers.py`, `test_news_extraction.py`) use hand-built
  payloads
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
├── test_fpl_squad.py            # Our own classic-FPL fifteen, and refusing to guess the id
├── test_project_horizon.py      # The horizon defaults to the gameweeks still to come (offline)
├── test_transfers.py            # Classic-FPL swaps: budget, three-per-club, the hit (offline)
├── test_news_extraction.py      # The claude -p client, and who counts as the same player (offline)
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
It pins both current-form ramps too, including the one the tests caught: a promoted club's rating is
an already-shrunk placeholder, so blending this season's goals in rate space and shrinking again
made Coventry *better* after conceding three. Promoted clubs blend in multiplier space, and the
directional assertion excludes them for that reason rather than by accident.

It also pins the *shape* of the pre-season role curve rather than just its direction — a hump,
highest for a player whose place was open — because a monotonic version was measured and rejected,
and a test asserting only "nailed starters get less" would pass on the wrong model.

`test_opportunity.py` covers the other half of the draft arithmetic, the part that does move on
every pick: the gap to the next player at a position, and what waiting a round costs. Its
load-bearing test is directional — taking the player *below* someone makes him more urgent, taking
the player *above* him changes nothing — which is the whole content of the metric.

`test_waivers.py` pins the property that separates a waiver from a pick: value is *squad* value.
It also covers the browsing list beside it - every projected player, tagged `mine`/`rival`/`free`/
`locked` and ranked by points rather than by swap gain, never truncated by the claims `limit`.
Its load-bearing pair is "upgrading a starter is worth the difference" against "upgrading a bench
player is worth almost nothing" - and the second one carries a comment about where the boundary
actually is, because the optimiser will happily switch to three defenders and five midfielders to
fit a good signing in. It also pins the refusals the user asked for by name: a 12-gameweek horizon
is rejected, not clamped to 10.

The horizon rules themselves are tested once, in `TestHorizonsAgainstARun`, because both the
waivers screen and the FPL board read them: the span a horizon covers, the refusal when a run is
too short, which of the three decides by default, and the one case where defaults are allowed to
shrink instead of raising - nothing was asked for, so nothing can be refused. `test_web_api.py`
then checks the board actually serves them, and that rank and the deciding column never disagree.

`test_transfers.py` is the classic-FPL half of `test_waivers.py`, and deliberately does not
re-test the metric they share - `SquadValuation` is exercised once, on the waivers side. What it
pins is the three rules the classic game adds, each of which is a way to put an illegal or
worthless transfer on a board that looks correct: a player you cannot afford, a fourth from one
club, and a swap whose gain a 4-point hit would wipe out. Two of its tests are there because the
answers surprise people - upgrading a player who never starts gains exactly zero rather than the
difference between the two players, and a missing bank raises rather than defaulting to £0.0,
because zero is itself a budget and the tightest one.

`test_news_extraction.py` never spawns a subprocess or calls a model: the CLI is faked at the
`asyncio.create_subprocess_exec` boundary, so what is pinned is our handling of what comes back -
code fences, error envelopes, a missing required key, a hung process. Its two load-bearing
parametrised tests are the identity rule, and both come from the first live run: a transliterated
spelling is the same player (`Odegaard`/`Ødegaard`, which halted a whole validation pass) and a
different name is not (`Murillo` filed under Aina's id, which was a genuine mis-mapping). One test
also pins that the extractor runs with tools disabled and outside this repo, because `claude -p`
started here reads `CLAUDE.md` and refuses to extract.

`test_project_horizon.py` pins a default rather than a model. `ProjectionParams` carries
`gameweek_from=1`, and for three weekly refreshes after GW1 that meant every generated run summed
gameweeks already played, reported `played_gameweeks=0` so the `current_season_ramp` never engaged,
and read role evidence as if no match had happened - all while writing a perfectly valid artifact.
The tests cover the defaulting only: that the start follows `resolve_next_gameweek()`, that the
method's horizon *length* survives a later start (the part that had to be fixed by hand each week),
that an explicit `--gw-from`/`--gw-to` still wins, that GW38 is a ceiling, that an empty range
raises instead of writing nothing, and - the one that protects every stored comparison - that
pre-season behaviour is byte-for-byte unchanged and no field but the two gameweek bounds moves.

`test_fpl_squad.py` is the classic-FPL half of the same identity problem, and its point is that
"permanent" was never the same as "correct": `FplManager.ME` sat in source for eight months naming
somebody else's team, so the tests pin that no id means an error carrying the command, that a
corrupt config raises even on the lenient path, and that the stored *names* travel with the id -
"that is not my team" is a far easier thing to notice than a wrong number. The rest is squad
validation: fifteen players, slots 1-15 once each, a 2/5/5/3 shape, no duplicates. Each of those
would otherwise filter a board by the wrong set of players and look completely normal doing it.

`test_draft_league.py` covers the ownership map and the identity trap under it. Draft entry ids
are re-issued every season, so a config written for another season raises rather than valuing the
wrong squad, and an entry that is not in the league says so with the league's actual entries
listed.

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
- Where league ownership comes from — `src/fpl/loader/README.md`
- Repo conventions and known traps — `CLAUDE.md`

