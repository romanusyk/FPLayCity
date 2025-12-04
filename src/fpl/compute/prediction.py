"""
Lazy computation nodes for FPL predictions.

Nodes:
- SeasonNode: Replays season up to next_gameweek
- GameweekPredictionNode: Generates predictions for a single target gameweek
- GameweekPredictionsNode: Aggregates predictions across multiple gameweeks

Pipeline:
- PredictionPipeline: Coordinates all nodes, provides predict() and score() API
"""
from src.fpl.compute.base import LazyNode
from src.fotmob.rotation.fotmob_adapter import FotmobAdapter
from src.fpl.models.season import Season
from src.fpl.models.immutable import Query
from src.fpl.models.prediction import (
    GameweekPrediction,
    GameweekPredictions,
    TeamFixturePrediction,
    PlayerFixturePrediction,
)
from src.fpl.forecast.models import (
    UltimateCleanSheetModel,
    SimpleXGModel,
    SimpleXAModel,
    SimpleDCModel,
    PlayerCSSimpleModel,
    PlayerXGSimpleModel,
    PlayerXASimpleModel,
    PlayerDCSimpleModel,
)


class SeasonNode(LazyNode[Season]):
    """
    Replays season up to next_gameweek.
    
    Params:
        next_gameweek: Gameweek to stop at (e.g., 6 = play GWs 1-5)
    
    Returns:
        Season with statistics up to (but not including) next_gameweek
    """
    
    def __init__(self, rotation_adapter: FotmobAdapter | None = None):
        super().__init__()
        self.rotation_adapter = rotation_adapter

    def compute(self, next_gameweek: int, **params) -> Season:
        season = Season()
        if self.rotation_adapter is not None:
            season.attach_rotation_adapter(self.rotation_adapter)
        for gw in range(1, next_gameweek):
            fixtures = Query.fixtures_by_gameweek(gw)
            if fixtures:  # Only play if fixtures exist
                season.play(fixtures)
        return season


class GameweekPredictionNode(LazyNode[GameweekPrediction]):
    """
    Generates predictions for a single gameweek.
    
    Creates all necessary models internally and generates predictions
    for all fixtures and players in the target gameweek.
    
    Params:
        next_gameweek: Season state (e.g., 6 = played GWs 1-5)
        target_gameweek: Gameweek to predict (must be >= next_gameweek)
        min_history_gws: Minimum history for player models
    
    Returns:
        GameweekPrediction with all fixture and player predictions
    """
    
    def __init__(self, season: SeasonNode):
        super().__init__()
        self.season = season
    
    def compute(
        self,
        next_gameweek: int,
        target_gameweek: int,
        min_history_gws: int = 5,
        **params
    ) -> GameweekPrediction:
        season = self.season(next_gameweek=next_gameweek)

        cs_model = UltimateCleanSheetModel(season)
        xg_model = SimpleXGModel(season)
        xa_model = SimpleXAModel(season)
        dc_model = SimpleDCModel(season)

        player_cs_model = PlayerCSSimpleModel(season, cs_model, min_history_gws)
        player_xg_model = PlayerXGSimpleModel(season, xg_model, min_history_gws)
        player_xa_model = PlayerXASimpleModel(season, xa_model, min_history_gws)
        player_dc_model = PlayerDCSimpleModel(season, dc_model, min_history_gws)

        gw_prediction = GameweekPrediction(gameweek=target_gameweek)

        fixtures = Query.fixtures_by_gameweek(target_gameweek)
        for fixture in fixtures:
            home_cs, away_cs = cs_model.predict(fixture)
            gw_prediction.add_team_fixture_prediction(TeamFixturePrediction(fixture.home, home_cs))
            gw_prediction.add_team_fixture_prediction(TeamFixturePrediction(fixture.away, away_cs))
            for pf in Query.player_fixtures_by_fixture(fixture.fixture_id):
                cs_prediction = player_cs_model.predict(pf)
                xg_prediction = player_xg_model.predict(pf)
                xa_prediction = player_xa_model.predict(pf)
                dc_prediction = player_dc_model.predict(pf)
                gw_prediction.add_player_fixture_prediction(
                    PlayerFixturePrediction(
                        fixture=pf,
                        cs_prediction=cs_prediction,
                        xg_prediction=xg_prediction,
                        xa_prediction=xa_prediction,
                        dc_prediction=dc_prediction,
                    )
                )

        return gw_prediction


class GameweekPredictionsNode(LazyNode[GameweekPredictions]):
    """
    Aggregates predictions across multiple gameweeks.
    
    Params:
        next_gameweek: Season state
        target_gameweeks: List of gameweeks to predict
        min_history_gws: Minimum history for player models
    
    Returns:
        GameweekPredictions aggregating all target gameweeks
    """
    
    def __init__(self, season: SeasonNode, gameweek_prediction: GameweekPredictionNode):
        super().__init__()
        self.season = season
        self.gameweek_prediction = gameweek_prediction
    
    def compute(
        self,
        next_gameweek: int,
        target_gameweeks: list[int],
        min_history_gws: int = 5,
        **params
    ) -> GameweekPredictions:
        gw_predictions = []
        for target_gw in target_gameweeks:
            gw_pred = self.gameweek_prediction(
                next_gameweek=next_gameweek,
                target_gameweek=target_gw,
                min_history_gws=min_history_gws,
            )
            gw_predictions.append(gw_pred)
        
        return GameweekPredictions(self.season(next_gameweek=next_gameweek), gw_predictions, min_history_gws)


class PredictionPipeline:
    """
    Lazy computation pipeline for FPL predictions.
    
    All computations are deferred until needed and cached.
    
    Example:
        pipeline = PredictionPipeline()
        
        # Predict next 3 gameweeks after playing first 5
        predictions = pipeline.predict(
            next_gameweek=6,
            target_gameweeks=[6, 7, 8],
            min_history_gws=5
        )
        
        # Get top players
        top_players = predictions.players_points_desc[:10]
        
        # Calculate score (reuses cached predictions)
        score = pipeline.score(
            next_gameweek=6,
            target_gameweeks=[6, 7, 8],
            squad_size=11
        )
    """
    
    def __init__(self, rotation_adapter: FotmobAdapter | None = None):
        self.season = SeasonNode(rotation_adapter=rotation_adapter)
        self.gameweek_prediction = GameweekPredictionNode(self.season)
        self.gameweek_predictions = GameweekPredictionsNode(self.season, self.gameweek_prediction)
    
    def predict(
        self,
        next_gameweek: int,
        target_gameweeks: list[int] | None = None,
        target_gameweek: int | None = None,
        horizon: int = 1,
        min_history_gws: int = 5,
    ) -> GameweekPredictions:
        """
        Generate predictions for target gameweeks.
        
        Args:
            next_gameweek: Season state (e.g., 6 = played GWs 1-5)
            target_gameweeks: List of gameweeks to predict (overrides target_gameweek/horizon)
            target_gameweek: Starting gameweek for prediction (used with horizon)
            horizon: Number of gameweeks to predict (used with target_gameweek)
            min_history_gws: Minimum history for player models
        
        Returns:
            GameweekPredictions with all predictions
        
        Examples:
            # Predict GWs 6, 7, 8
            predict(next_gameweek=6, target_gameweeks=[6, 7, 8])
            
            # Predict next 3 GWs starting from 6
            predict(next_gameweek=6, target_gameweek=6, horizon=3)
            
            # Predict single GW
            predict(next_gameweek=6, target_gameweek=6)
        """
        if target_gameweeks is None:
            if target_gameweek is None:
                target_gameweek = next_gameweek
            target_gameweeks = list(range(target_gameweek, target_gameweek + horizon))
        
        return self.gameweek_predictions(
            next_gameweek=next_gameweek,
            target_gameweeks=target_gameweeks,
            min_history_gws=min_history_gws,
        )
    
    def score(
        self,
        next_gameweek: int,
        target_gameweeks: list[int] | None = None,
        target_gameweek: int | None = None,
        horizon: int = 1,
        min_history_gws: int = 5,
        squad_size: int = 11,
    ) -> float:
        """
        Calculate total points for top squad.
        
        Reuses cached predictions if parameters match.
        
        Args:
            (same as predict)
            squad_size: Number of players to include in score
        
        Returns:
            Total actual points for top squad_size players
        """
        predictions = self.predict(
            next_gameweek=next_gameweek,
            target_gameweeks=target_gameweeks,
            target_gameweek=target_gameweek,
            horizon=horizon,
            min_history_gws=min_history_gws,
        )
        
        top_players = predictions.players_total_points_desc[:squad_size]
        return sum(p.actual_points for p in top_players)
    
    def clear_cache(self):
        """Clear all cached computations."""
        self.season.clear_cache()
        self.gameweek_prediction.clear_cache()
        self.gameweek_predictions.clear_cache()
    
    @property
    def cache_info(self) -> dict[str, int]:
        """Get cache sizes for all nodes."""
        return {
            'season': self.season.cache_size,
            'gameweek_prediction': self.gameweek_prediction.cache_size,
            'gameweek_predictions': self.gameweek_predictions.cache_size,
        }

