# Lazy Computation System

Typed, lazy computation graph for FPL predictions with automatic caching.

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│ LazyNode[T]                                                  │
│ - Generic base class with type safety                        │
│ - Automatic caching based on parameters                      │
│ - Abstract compute() method                                  │
└─────────────────────────────────────────────────────────────┘
                            ↓
        ┌──────────────────┬────────────────┬─────────────────────┐
        │                  │                │                     │
┌───────▼──────┐  ┌────────▼─────────┐  ┌──▼──────────────────┐│
│ SeasonNode   │  │ GameweekPrediction│  │ GameweekPredictions ││
│              │  │      Node         │  │       Node          ││
│ Replays GWs  │──▶ Predicts single  │──▶ Aggregates multiple ││
│ 1 to N-1     │  │ gameweek         │  │ gameweeks           ││
└──────────────┘  └───────────────────┘  └─────────────────────┘
```

## Key Features

### 1. Type Safety ✅
```python
class SeasonNode(LazyNode[Season]):
    #                      ^^^^^^^^
    #                      Output type declared

    def compute(self, next_gameweek: int, **params) -> Season:
        #                                             ^^^^^^^^
        #                                             Return type enforced
        ...
```

### 2. Automatic Caching ✅
```python
pipeline = PredictionPipeline()

# First call: computes everything
predictions1 = pipeline.predict(next_gameweek=6, target_gameweeks=[6,7,8])

# Second call with same params: instant (cache hit)
predictions2 = pipeline.predict(next_gameweek=6, target_gameweeks=[6,7,8])

# Different params: recomputes
predictions3 = pipeline.predict(next_gameweek=7, target_gameweeks=[7,8,9])
```

### 3. Lazy Evaluation ✅
```python
# Creating pipeline doesn't compute anything
pipeline = PredictionPipeline()

# Computation only happens when you call predict()
predictions = pipeline.predict(...)  # ← Computes here
```

### 4. Composable ✅
```python
# Can access intermediate nodes
season = pipeline.season(next_gameweek=6)  # Just the season

# Or get full predictions
predictions = pipeline.predict(next_gameweek=6, ...)  # Everything
```

## Files

### `base.py`
- `LazyNode[T]`: Abstract base class for typed lazy nodes
- Implements caching, provides abstract `compute()` method

### `prediction.py`
- `SeasonNode`: Replays season to given gameweek
- `GameweekPredictionNode`: Generates predictions for single gameweek
- `GameweekPredictionsNode`: Aggregates multiple gameweeks
- `PredictionPipeline`: Public API for predictions

## Refactored Prediction Models

### Old Structure (Eager, Grouped by Team/Player)
```python
# Old: Group by team/player first, then gameweek
GameweekPredictions:
  - team_predictions: dict[int, TeamPredictions]
  - player_cs_predictions: dict[int, PlayerPredictions]
  - ...
```

### New Structure (Lazy, Grouped by Gameweek)
```python
# New: Group by gameweek first, aggregate on demand
GameweekPrediction:  # Single gameweek
  - fixture_predictions: list[FixturePrediction]
  - player_cs_predictions: list[PlayerFixtureCsPrediction]
  - ...

GameweekPredictions:  # Multiple gameweeks
  - gameweek_predictions: list[GameweekPrediction]
  - players_points_desc: @property (aggregates on access)
  - ...
```

**Benefits:**
- Lazy evaluation: Only compute what's needed
- Flexible: Can predict any combination of gameweeks
- Cacheable: Each gameweek cached independently

## Usage Examples

### Basic Prediction
```python
from src.fpl.compute.prediction import PredictionPipeline

pipeline = PredictionPipeline()

# Predict next 3 gameweeks
predictions = pipeline.predict(
    next_gameweek=6,  # Played GWs 1-5
    target_gameweeks=[6, 7, 8]
)

# Get top players
top_players = predictions.players_points_desc[:10]
```

### Calculate Score
```python
# Reuses cached predictions if parameters match
score = pipeline.score(
    next_gameweek=6,
    target_gameweeks=[6, 7, 8],
    squad_size=11
)
```

### Using Horizon
```python
# Predict next 5 gameweeks starting from GW 10
predictions = pipeline.predict(
    next_gameweek=10,
    target_gameweek=10,
    horizon=5  # GWs 10-14
)
```

### Cache Management
```python
# Check cache sizes
print(pipeline.cache_info)
# {'season': 2, 'gameweek_prediction': 5, 'gameweek_predictions': 1}

# Clear all caches
pipeline.clear_cache()
```

## Comparison: Old vs New

| Aspect | Old (Eager) | New (Lazy) |
|--------|------------|-----------|
| **Computation** | Immediate | On demand |
| **Caching** | Manual | Automatic |
| **Parameters** | Hidden in objects | Explicit in calls |
| **Reuse** | Create new objects | Reuse pipeline |
| **Type Safety** | Partial | Full (Generic[T]) |
| **REPL-Friendly** | Medium | High |
| **Flexibility** | Fixed structure | Arbitrary gameweeks |

## Implementation Details

### Node Dependencies
```python
class GameweekPredictionNode(LazyNode[GameweekPrediction]):
    def __init__(self, season: SeasonNode):
        #                  ^^^^^^^^^^^^^^^^^
        #                  Typed dependency
        super().__init__()
        self.season = season
    
    def compute(self, next_gameweek: int, target_gameweek: int, **params):
        season = self.season(next_gameweek=next_gameweek)  # ← Cached call
        # ... use season ...
```

### Cache Keys
- Based on **all parameters** passed to `compute()`
- Same params = same cache key = cache hit
- Different params = different cache key = recompute

### Parameter Flow
```python
pipeline.predict(next_gameweek=6, target_gameweeks=[6,7,8])
  ↓
gameweek_predictions(next_gameweek=6, target_gameweeks=[6,7,8])
  ↓
gameweek_prediction(next_gameweek=6, target_gameweek=6)  # 3 times
  ↓
season(next_gameweek=6)  # Cached after first call
```

## Next Steps

1. **Add More Nodes** (if needed):
   - `TeamStatsNode` for extracting team statistics
   - `PlayerStatsNode` for player statistics
   - `OptimalSquadNode` for squad selection

2. **Optimization**:
   - Add parallel execution for independent gameweeks
   - Implement cache persistence (save to disk)
   - Add graph visualization

3. **Integration**:
   - Update `main.py` to use pipeline
   - Update MCP tools to use pipeline
   - Add HTTP API endpoints with pipeline

## Testing

See `examples/lazy_prediction.py` for complete examples.

Run with:
```bash
uv run python examples/lazy_prediction.py
```

