import asyncio
import httpx
import logging

from mcp.server.fastmcp import FastMCP

from src.fpl.models.immutable import Team, Teams, Player, Players, Fixtures, PlayerFixtures
from src.fpl.loader.load import bootstrap
from src.fpl.forecast.models import (
    UltimateCleanSheetModel, SimpleXGModel, SimpleXAModel,
    PlayerXGUltimateModel, PlayerXAUltimateModel,
)
from src.fpl.models.prediction import (
    PlayerFixtureXgPrediction,
    PlayerFixtureXaPrediction,
    GameweekPredictions,
)
from src.fpl.models.season import Season

logging.basicConfig(level=logging.INFO)

mcp = FastMCP("FPL")


class Server:
    """
    Server class for the Fantasy Premier League API.
    """

    season: Season | None = None
    predictions: GameweekPredictions | None = None


@mcp.tool()
async def get_teams() -> list[dict]:
    """
    Retrieve a list of all teams in the English Premier League.

    Returns:
        list[dict]: A list of team summary dictionaries, each containing:
            - id (int): Unique team identifier
            - name (str): Team name
            - uri (str): Resource URI to fetch detailed team information

    Example:
        [
            {
                'id': 1,
                'name': 'Arsenal',
                'uri': 'epl://team/1'
            },
            ...
        ]
    """
    return [
        {
            'id': team.team_id,
            'name': team.name,
            'uri': f'epl://team/{team.team_id}'
        }
        for team in Teams.items
    ]

@mcp.tool()
async def get_team(team_id: int) -> Team:
    """
    Retrieve detailed information for a specific EPL team.

    Args:
        team_id (int): The unique identifier of the team

    Returns:
        Team: Complete team object with all available team attributes

    Raises:
        ValueError: If team_id does not exist
    """
    return Teams.by_id(team_id)

@mcp.tool()
async def get_team_players(team_id: int) -> list[dict]:
    """
    Retrieve a list of all players belonging to a specific EPL team.

    Args:
        team_id (int): The unique identifier of the team

    Returns:
        list[dict]: A list of player summary dictionaries, each containing:
            - id (int): Unique player identifier
            - name (str): Player's display name (web_name)
            - uri (str): Resource URI to fetch detailed player information

    Raises:
        ValueError: If team_id does not exist

    Example:
        [
            {
                'id': 123,
                'name': 'Saka',
                'uri': 'epl://player/123'
            },
            ...
        ]
    """
    return [
        {
            'id': player.player_id,
            'name': player.web_name,
            'uri': f'epl://player/{player.player_id}'
        }
        for player in Players.by_team(team_id)
    ]

@mcp.tool()
async def get_player(player_id: int) -> Player:
    """
    Retrieve detailed information for a specific EPL player.

    Args:
        player_id (int): The unique identifier of the player

    Returns:
        Player: Complete player object with all available player attributes

    Raises:
        ValueError: If player_id does not exist
    """
    return Players.by_id(player_id)

@mcp.tool()
async def get_predicted_player_points() -> list[str]:
    """
    Retrieve a list of predicted points for players in upcoming gameweeks.

    Returns:
        list[str]: A list of string representations of player point predictions,
                   sorted in descending order by predicted points. Each string
                   contains player name, team, fixture information, and point prediction.

    Example output:
        [
            "Haaland (Manchester City) vs NFO (H): 8.2pts",
            "Salah (Liverpool) vs BHA (A): 7.5pts",
            ...
        ]

    Note:
        Predictions are based on models incorporating clean sheet probabilities,
        expected goals (xG), and expected assists (xA) from historical performance data.
    """
    return [p.__repr__() for p in Server.predictions.players_points_desc]


if __name__ == "__main__":
    client = httpx.AsyncClient()
    asyncio.run(bootstrap(client))
    season = Season()
    for game_week in range(1, 36):
        season.play(Fixtures.get_list(gameweek=game_week))
    cs_model = UltimateCleanSheetModel(season)
    xg_model = SimpleXGModel(season)
    xa_model = SimpleXAModel(season)
    player_xg_model = PlayerXGUltimateModel(season, xg_model)
    player_xa_model = PlayerXAUltimateModel(season, xa_model)

    gw_predictions = GameweekPredictions(season)
    for gw in range(36, 39):
        for fixture in Fixtures.get_list(gameweek=gw):
            gw_predictions.add_team_prediction(cs_model.predict(fixture))
            for pf in PlayerFixtures.by_fixture(fixture.fixture_id):
                gw_predictions.add_player_xg_prediction(
                    PlayerFixtureXgPrediction(player_xg_model.predict(pf))
                    )
                gw_predictions.add_player_xa_prediction(
                    PlayerFixtureXaPrediction(player_xa_model.predict(pf))
                    )
    logging.info('Bootstrap complete.')
    Server.season = season
    Server.predictions = gw_predictions
    print(asyncio.run(get_predicted_player_points())[:3])
    mcp.run(transport='stdio')
