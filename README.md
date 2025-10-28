# FPLayCity
Fantasy Premier League stats & predictions

## System Overview

### Data Types

**Raw Data** (cached JSON from FPL API):
- `bootstrap-static/`: Teams, players, gameweek info
- `fixtures/`: All fixtures with scores, difficulty ratings
- `element-summary/{id}/`: Individual player history and upcoming fixtures

**Core Models** (immutable data structures):
- `Team`: FPL team with strength ratings (attack/defense, home/away)
- `Fixture`: Match with home/away teams, scores, gameweek, outcome
- `Player`: FPL player with position, team, cost
- `PlayerFixture`: Player performance in a fixture (points, minutes, xG, xA, CS, DC)

**Statistics** (aggregated metrics):
- `Aggregate`: Total/count pairs for calculating averages
- `StatsAggregate`: Metrics broken down by FDR (1-5) and side (home/away)
- `TeamStats` / `PlayerStats`: Historical statistics with form metrics (last N games)

**Predictions** (model outputs):
- `FixturePrediction`: Team-level predictions (clean sheets, xG, xA)
- `PlayerFixturePrediction`: Player-level predictions with actual vs predicted comparison

### Design Patterns

**Indexed Collections** (in-memory database):
- Generic `Collection` class with multiple indices for O(1) lookups
- Example: `Fixtures.get_one(fixture_id=42)` or `Fixtures.get_list(gameweek=5)`
- Pattern: Avoid linear searches by pre-building indices on key fields

**Progressive Replay** (time-series simulation):
- `Season.play(fixtures)` replays gameweeks sequentially
- Builds historical statistics incrementally
- Enables backtesting: predict GW N using only data from GW 1 to N-1

**Aggregate Pattern** (statistics computation):
- All metrics stored as `Aggregate(total, count)` 
- Supports weighted averaging: `wa()` and square-root weighted: `swa()`
- Normalized values: `fdr_norm` scales predictions by difficulty

**Model Hierarchy** (composable predictions):
- Base classes: `FixtureModel`, `PlayerFixtureModel`
- Variants: Season avg, form-based, FDR-based, composite
- Composition: `PlayerPointsSimpleModel` combines CS/xG/xA/DC models

### System Components

```
┌─────────────────────────────────────────────────────────────┐
│ 1. DATA LOADING (loader/)                                   │
├─────────────────────────────────────────────────────────────┤
│ • fetch.py: Async HTTP client wrapper                       │
│ • load.py: Fetch & cache FPL API responses                  │
│   - BaseResource: Versioned snapshots with freshness checks │
│   - SimpleResource: Single endpoints                        │
│   - CompoundResource: Multiple related endpoints            │
│   - bootstrap(): Initial load → populates global collections│
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│ 2. CORE DATA (models/immutable.py)                          │
├─────────────────────────────────────────────────────────────┤
│ Global indexed collections:                                 │
│ • Teams: Collection[Team] by team_id                        │
│ • Fixtures: Collection[Fixture] by fixture_id, gameweek     │
│ • Players: Dict[player_id → Player]                         │
│ • PlayerFixtures: Custom collection with multiple lookups   │
│                                                              │
│ Pattern: Indexed collections (collection.py) for O(1) access│
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│ 3. STATISTICS (models/season.py, models/stats.py)           │
├─────────────────────────────────────────────────────────────┤
│ • Season: Main state container, replays fixtures GW-by-GW   │
│   - Maintains global & per-team/player statistics           │
│   - Provides form metrics (last N games)                    │
│ • TeamStats: CS/xG/xA/DC aggregated by FDR and side         │
│ • PlayerStats: xG/xA/DC with team share calculations        │
│                                                              │
│ Pattern: Progressive replay builds stats incrementally      │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│ 4. PREDICTION MODELS (forecast/models.py)                   │
├─────────────────────────────────────────────────────────────┤
│ Fixture-level (team predictions):                           │
│ • CleanSheetModel variants (avg, form, FDR, composite)      │
│ • XGModel, XAModel: Scaled by FDR + form                    │
│                                                              │
│ Player-level (individual predictions):                      │
│ • PlayerXGModel: Team xG × player share OR player form      │
│ • PlayerXAModel: Team xA × player share OR player form      │
│ • PlayerCSModel: Team CS × minutes played probability       │
│ • PlayerPointsModel: Combines CS/xG/xA/DC → total points    │
│                                                              │
│ Pattern: Models compose (aggregate pattern + weighted avg)  │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│ 5. EVALUATION (main.py)                                     │
├─────────────────────────────────────────────────────────────┤
│ Backtesting loop:                                           │
│ 1. For each gameweek 2..N:                                  │
│    - Replay previous GW → update statistics                 │
│    - Make predictions for current GW                        │
│    - Select optimal squad by position                       │
│    - Compare: model vs form vs cost-based selection         │
│ 2. Report total points across evaluation period             │
│                                                              │
│ Loss functions (forecast/loss.py): MAE, LogLoss, AvgDiff    │
└─────────────────────────────────────────────────────────────┘
```

### Data Flow

```
FPL API → load.py → JSON cache → bootstrap() → Collections (Teams/Fixtures/Players)
                                                      ↓
                                        Season.play(fixtures) → Statistics
                                                      ↓
                                        Models.predict() → Predictions
                                                      ↓
                                        Evaluation → Points comparison
```

### Key Innovations

1. **Indexed Collections**: O(1) lookups without database overhead
2. **Versioned Snapshots**: All API responses timestamped for reproducibility
3. **Progressive Replay**: Simulate season to build realistic statistics
4. **FDR Normalization**: Scale predictions by fixture difficulty
5. **Component-Based Points**: CS + xG + xA + DC → total points

## Architecture Analysis

### What Works Well

✅ **Indexed Collections**: O(1) lookups are fast and elegant
✅ **Progressive Replay**: Backtesting is realistic and prevents data leakage
✅ **Immutable Data**: Core models are stable and don't change unexpectedly
✅ **Versioned Snapshots**: Reproducibility is built-in

### Current Pain Points

**1. Boilerplate for Experimentation**
```python
# To try a new model, need 10+ lines:
cs_model = UltimateCleanSheetModel(season)
xg_model = SimpleXGModel(season)
xa_model = SimpleXAModel(season)
dc_model = SimpleDCModel(season)
player_cs_model = PlayerCSSimpleModel(season, cs_model, min_history_gws)
player_xg_model = PlayerXGSimpleModel(season, xg_model, min_history_gws)
# ... repeat for each experiment
```

**2. Repetitive Prediction Generation**
```python
# Lines 112-116 and 125-137 are nearly identical
for pf in PlayerFixtures.by_fixture(fixture.fixture_id):
    gw_predictions.add_player_cs_prediction(PlayerFixtureCsPrediction(player_cs_model.predict(pf)))
    gw_predictions.add_player_xg_prediction(PlayerFixtureXgPrediction(player_xg_model.predict(pf)))
    # ... 4 lines of similar code
```

**3. Hard to Access Data Interactively**
- Global collections (Teams, Fixtures, Players) are singletons but scattered
- Season state is passed through deep hierarchies
- No easy way to query "show me top 10 players for GW 20"
- Can't easily tweak parameters and re-run

**4. Script-Based, Not Interactive**
- main.py runs full evaluation loop (30+ gameweeks)
- Hard to inspect intermediate results
- No REPL-friendly workflow for exploration

### Improvement Principles: Stateless On-Demand Computation

**Core Philosophy:** Define operations as composable functions, not materialized state. Compute only when results are requested.

#### Principles

**1. Laziness Over Eagerness**
- Don't precompute and store predictions for all players
- Define HOW to compute, not WHAT is computed
- Let consumers request exactly what they need

**2. Pure Functions Over State**
- Predictions are pure functions: `f(immutable_data, parameters) → result`
- Same inputs always produce same outputs
- No hidden state, no cache invalidation, no staleness

**3. Composition Over Inheritance**
- Small, focused functions that combine naturally
- Filter → Map → Reduce pipelines
- Example: `filter(by_position) | map(predict) | sort(by_points) | take(n)`

**4. Parameters as First-Class Citizens**
- Position, gameweek, top-N are query parameters, not object state
- Change parameter → get new result, instantly
- No need to mutate state and remember to recalculate

#### Implementation: Query-Oriented API

```python
# query_engine.py - Pure functions for on-demand computation

class Query:
    """Stateless query functions - compute on demand from immutable data"""
    
    # --- Data Access (Pure Lookups) ---
    
    @staticmethod
    def player(name: str) -> Player:
        """Find player by name"""
        return next(p for p in Players.items_by_id.values() 
                   if name.lower() in p.web_name.lower())
    
    @staticmethod
    def team_fixtures(team_name: str, gw_range: range) -> list[Fixture]:
        """Get fixtures for a team"""
        team = next(t for t in Teams.items if team_name.lower() in t.name.lower())
        return [f for gw in gw_range for f in Fixtures.get_list(gameweek=gw)
                if f.home.team_id == team.team_id or f.away.team_id == team.team_id]
    
    # --- Predictions (Compute on Demand) ---
    
    @staticmethod
    def predict_player(
        season: Season,
        models: dict,  # cs_model, xg_model, xa_model, dc_model
        player_id: int,
        gameweek: int,
    ) -> PlayerTotalPrediction:
        """Predict single player for single gameweek (pure function)"""
        player_fixtures = [pf for pf in PlayerFixtures.by_gw(gameweek)
                          if pf.player_id == player_id]
        
        # Compute components on demand
        cs = sum(models['cs'].predict(pf).p for pf in player_fixtures)
        xg = sum(models['xg'].predict(pf).p for pf in player_fixtures)
        xa = sum(models['xa'].predict(pf).p for pf in player_fixtures)
        dc = sum(models['dc'].predict(pf).p for pf in player_fixtures)
        
        player = Players.by_id(player_id)
        return PlayerTotalPrediction(
            player=player,
            cs_points=cs * player.clean_sheet_points,
            xg_points=xg * player.goal_points,
            xa_points=xa * player.assist_points,
            dc_points=dc * player.dc_points,
        )
    
    @staticmethod
    def top_players(
        season: Season,
        models: dict,
        gameweek: int,
        n: int = 10,
        position: PlayerType = None,
        min_cost: float = None,
        max_cost: float = None,
    ) -> list[PlayerTotalPrediction]:
        """
        Query top players with filters (compute on demand).
        
        Composable: filter by position/cost, predict, sort, take top N.
        Change any parameter → recompute with new filter.
        """
        # Filter candidates
        candidates = Players.items_by_id.values()
        if position:
            candidates = [p for p in candidates if p.player_type == position]
        if min_cost:
            candidates = [p for p in candidates if p.now_cost >= min_cost]
        if max_cost:
            candidates = [p for p in candidates if p.now_cost <= max_cost]
        
        # Predict on demand (only for filtered candidates)
        predictions = [
            Query.predict_player(season, models, p.player_id, gameweek)
            for p in candidates
        ]
        
        # Sort and take top N
        return sorted(predictions, key=lambda p: -p.total_points)[:n]
    
    @staticmethod
    def compare_gameweeks(
        season: Season,
        models: dict,
        player_name: str,
        gameweeks: range,
    ) -> list[tuple[int, float]]:
        """Compare predictions across gameweeks for one player"""
        player = Query.player(player_name)
        return [
            (gw, Query.predict_player(season, models, player.player_id, gw).total_points)
            for gw in gameweeks
        ]
    
    @staticmethod
    def best_value(
        season: Season,
        models: dict,
        gameweek: int,
        position: PlayerType = None,
        n: int = 10,
    ) -> list[tuple[Player, float]]:
        """Top N players by points per cost (value picks)"""
        predictions = Query.top_players(season, models, gameweek, n=100, position=position)
        with_value = [(p.player, p.total_points / p.player.now_cost) for p in predictions]
        return sorted(with_value, key=lambda x: -x[1])[:n]

# Usage in debugger/REPL:
>>> models = {
...     'cs': UltimateCleanSheetModel(season),
...     'xg': SimpleXGModel(season),
...     'xa': SimpleXAModel(season),
...     'dc': SimpleDCModel(season),
... }

# Quick queries (all parameters explicit, compute on demand)
>>> Query.top_players(season, models, gameweek=20, n=5, position=PlayerType.MID)
>>> Query.top_players(season, models, gameweek=20, n=5, position=PlayerType.FWD)

# Compare across gameweeks
>>> Query.compare_gameweeks(season, models, "Salah", range(20, 25))

# Value picks
>>> Query.best_value(season, models, gameweek=20, position=PlayerType.MID)

# Compose queries (chain operations)
>>> top_mids = Query.top_players(season, models, 20, position=PlayerType.MID, n=20)
>>> expensive_mids = [p for p in top_mids if p.player.now_cost > 10.0]
>>> sorted(expensive_mids, key=lambda p: p.xg_points)[:5]
```

**Benefits:**
- ✅ No precomputed state to invalidate
- ✅ All parameters explicit (no hidden state like `season.pos`)
- ✅ Easy to compose (filter, map, sort, reduce)
- ✅ Perfect for debugger: change parameter, call function, see new result
- ✅ Memory efficient: compute only what's requested
- ⚠️ Trade-off: Recomputes on every call (acceptable for interactive use)

### Recommended Approach

**Embrace stateless computation with Query API:**

1. **Refactor `Query` class** with pure, composable functions
   - Move prediction logic into stateless functions
   - Make all parameters explicit (no hidden state)
   - Support chaining and composition

2. **Add helper for season replay**
   ```python
   def setup_season(up_to_gw: int) -> Season:
       season = Season()
       for gw in range(1, up_to_gw):
           season.play(Fixtures.get_list(gameweek=gw))
       return season
   ```

3. **Use in debugger workflow**
   - Set breakpoint after season setup
   - Call `Query.top_players()` with different parameters
   - Compose queries as needed
   - No state to invalidate, no stale data possible

**Why this works:**
- ✅ Minimal refactoring (extract functions, no complex infrastructure)
- ✅ Works perfectly with PyCharm debugger
- ✅ Stateless = no cache invalidation complexity
- ✅ Composable = flexible experimentation
- ✅ Pure functions = easy to test and reason about

## Usage

Load data from FPL API:
```bash
uv run -m src.fpl.loader.load
```

Run predictions & evaluation:
```bash
uv run -m src.fpl.main
```
