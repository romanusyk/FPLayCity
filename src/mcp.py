import asyncio
import logging
import os

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP
from typing import Annotated

from src.fpl.const import GameMode
from src.fpl.compute.prediction import PredictionPipeline
from src.fpl.core import build_pipeline
from src.fpl.views import TeamPredictionView

logging.basicConfig(level=logging.INFO)

load_dotenv()
next_gameweek = int(os.getenv("NEXT_GAMEWEEK"))
if not next_gameweek:
    raise ValueError("NEXT_GAMEWEEK environment variable is not set")
mcp = FastMCP("fantasy")


class Server:
    """
    Server class for the Fantasy API.
    """

    pipeline: PredictionPipeline | None = None


async def load():
    Server.pipeline = await build_pipeline(next_gameweek)


FPL_DESCRIPTION = f"""
Generates a user request to analyse the data for the {GameMode.fpl.value} ahead of the next gameweek ({next_gameweek}) deadline.
The parameter of this prompt control what data to include in the analysis.
All there parameter are some numbers of gameweeks. The main anchor is the next gameweek ({next_gameweek}).

1. lookback_gameweeks: Number of gameweeks to take into account when building statistics for making predictions.
This parameter also defines how many of the past gameweeks will be included to each Team / Player history.
This this history will include gameweeks from `{next_gameweek} - lookback_gameweeks` to `{next_gameweek - 1}`, inclusively.
The default value is `3`.

2. prediction_horizon: Number of gameweeks to predict for. Usually this number equals to how long the user is going to keep the selected players.
The default value is `1`.

3. prediction_horizon_shift: Number of gameweeks to shift the prediction horizon. This parameter is used to shift the prediction horizon forward or backward.
The default value is `0`.

Therefore, the prediction horizon will be from `{next_gameweek} + prediction_horizon_shift` to `{next_gameweek} + prediction_horizon_shift + prediction_horizon - 1`, inclusively.

The result of this prompt will contain the requested data about the Teams and Players for the prediction horizon.
The ultimate goal is to help the user to make the best decision for the next gameweek ({next_gameweek}).
"""

@mcp.prompt(description=FPL_DESCRIPTION)
def fantasy_premier_league_analysis(
        lookback_gameweeks: Annotated[int, "Number of gameweeks to look back when building statistics for making predictions"] = 3,
        prediction_horizon: Annotated[int, "Number of gameweeks to predict for"] = 1,
        prediction_horizon_shift: Annotated[int, "Number of gameweeks to shift the prediction horizon"] = 0,
) -> str:
    target_gameweeks = [
        next_gameweek + prediction_horizon_shift + i
        for i in range(prediction_horizon)
    ]
    logging.info(f"\n=== Predictions for GWs {target_gameweeks} ===")
    predictions = Server.pipeline.predict(
        next_gameweek=next_gameweek,
        target_gameweeks=target_gameweeks,
        min_history_gws=lookback_gameweeks,
    )
    teams = "\n\n".join([
        TeamPredictionView.build(
            p,
            next_gameweek=next_gameweek,
            history_gws=lookback_gameweeks,
        ).to_markdown()
        for p in predictions.teams_xgc_exp_asc
    ])
    result = f"""
    Your goal is to analyse this data and provide a recommendation for the next gameweek ({next_gameweek}) of {GameMode.fpl.value}.
    The data has past data and predictions in two sections: Teams and Players.
    The data in each section was prepared specifically for the next gameweek ({next_gameweek}).
    All the presented predictions are based on the past {lookback_gameweeks} gameweeks' statistics.
    All the gameweeks' statistics are presented in the data.
    We are interested in selecting the best Teams / Players for the gameweeks of {target_gameweeks}.

    Here are the questions for you to answer.
    In addition, do not hesitate to provide any feedback on the quality of the provided data.
    If some essential data is missing, I would not be able to fix this immediately, but I am going to improve the data over time.

    1. Which teams have the best attaching potential?
    2. Which teams have the best defending potential?

    # Shared knowledge (common for Teams and Players)

    Metrics:
    - xg: Expected goals
    - xgc: Expected goals conceded
    - xa: Expected assists (for teams: does not make much sense, but can be included as may contain some indirect insights; calculated as the total for the players)
    - cs: Clean sheets
    - dc: Defensive contribution (for teams: does not make much sense, but can be included as may contain some indirect insights; calculated as the total for the players)
    - pts: Points (for teams: does not make much sense, but can be included as may contain some indirect insights; calculated as the total for the players)
    - mp: Minutes played (for teams: does not make much sense, but can be included as may contain some indirect insights; calculated as the total for the players)

    Terms:
    - fdr: Fixture Difficulty Rating (1-easy, 5-hard)

    Modelling approach:

    For most metrics of both Teams and Players, the following approach is used:

    1. To calculate a metric form, we attach a season average for the metric from similar games (this is called metric avg, like "xg_avg").
    Then we calculate the difference between the metric and the metric avg (this is called metric exc, like "xg_exc").
    Finally, the metric form is the average of the metric excs.
    2. Making "predictions": for each target gameweek, we attach it's metric average in exactly the same way as we did for the form.
    Finally, the metric prediction is the sum of this attached average and the metric form.

    Example (team xg calculation):
    - GW 1 (past) was tough FDR (4) and away, team xg was 1.5, but team xg avg was 1.1 (in average this season, teams had 1.1 xg away in FDR 4 matches), so team xg exc was 0.4, indication avobe average results.
    - GW 2 (past) was easy FDR (2) and home, team xg was 2.0, but team xg avg was 3.0 (in average this season, teams had 3.0 xg home in FDR 2 matches), so team xg exc was -1.0, indication below average results.
    - So team form is (0.4 + -1.0) / 2 = -0.3.
    - GW 3 (target) is easy FDR (2) and away, team xg avg is 2.5, team xg form is -0.3, so team xg prediction is 2.5 - 0.3 = 2.2.

    # Teams

    Each team has some attributes and overall statistics, as well as per-gameweek statistics.
    For past gameweeks, the statistics are the actual statistics.
    For future gameweeks, the statistics are some aggeregations of the past gameweeks and the predicted statistics.

    Overall team statistics:

    1. Average expected xg, xgc, and cs: averages for the target gameweeks.

    Per-gameweek team columns:

    - game: is a concatenation of the gameweek, the home team, the home team score, the away team score, the away team, and the difficulty of the game w.r.t this team.
    - xg_diff: difference between expected goals and expected goals conceded.
    - xg: expected goals.
    - xg_avg: average expected goals produced by teams in matches of the same FDR and side as this fixture.
    - xg_exc: expected goals minus average expected goals.
    - xgc: expected goals conceded.
    - xgc_avg: average expected goals conceded by teams in matches of the same FDR and side as this fixture.
    - xgc_exc: expected goals conceded minus average expected goals conceded.
    - cs: clean sheets.
    - cs_avg: average clean sheets by teams in matches of the same FDR and side as this fixture.
    - xg_exc_form: expected goals minus average expected goals for the last {lookback_gameweeks} gameweeks.
    - xgc_exc_form: expected goals conceded minus average expected goals conceded for the last {lookback_gameweeks} gameweeks.
    - cs_exc_form: clean sheets minus average clean sheets for the last {lookback_gameweeks} gameweeks.
    - xg_exp: expected goals for this gameweek.
    - xgc_exp: expected goals conceded for this gameweek.
    - cs_exp: expected clean sheets for this gameweek.

    Please, pay attention to the following:

    1. For teams data, there is some discrepancy between cs anc xgc metrics.
    However, this is how it is in reality: sometimes teams manage to keep clean sheets despite conceding expected goals.
    Therefore, the cs metric is not always equal to the xgc metric.
    During the analysis, you should prioritise xgc over cs. This will draw more conservative predictions, which is good considering the highly random cs nature.

    {teams}

    # Players

    Currently, there players data is not presented. Therefore, you should focus on the Teams data.
    """
    with open("last_prompt.md", "w") as f:
        f.write(result)
    return result


if __name__ == "__main__":
    asyncio.run(load())
    mcp.run(transport='stdio')
