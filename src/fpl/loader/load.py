"""
Data loader for FPL API with caching and single-snapshot storage.

Responsibilities:
- `store`: Owns timestamped JSON snapshot management (naming, freshness, persistence)
- `load`: Fetches from the FPL API, coordinates with `store`, and populates registries
- `convert`: Converts between raw JSON payloads and immutable dataclasses (used by bootstrap)

Main functions:
- bootstrap(): Initial data load - fetches and populates all global collections (Teams, Fixtures, Players, PlayerFixtures, News)
- load(): Incremental data refresh - fetches latest data respecting freshness parameter

Storage format:
- Each resource stores a single latest snapshot: `<prefix>_<ISO8601_timestamp>.json`
- Old snapshots are automatically deleted when new ones are created
- Freshness checks determine if existing snapshots need refresh
"""
import asyncio
import json
import logging
import os

from httpx import AsyncClient
from src.fpl.loader.convert import (
    element_json_to_player,
    event_json_to_gameweek,
    fixture_json_to_fixture,
    future_fixture_to_player_fixture,
    history_entry_to_player_fixture,
    team_json_to_team,
)
from src.fpl.loader.news.pl import list_saved_news
from src.fpl.loader.news.validate import list_saved_facts
from src.fpl.loader.store import JsonSnapshotStore, SnapshotSpec
from src.fpl.loader.utils import Season
from src.fpl.models.immutable import Fixtures, Gameweeks, News, NewsFacts, PlayerFixtures, Players, Teams

BASE_URL = "https://fantasy.premierleague.com/api/"
NEXT_GAMEWEEK = 15


async def fetch_json(client: AsyncClient, url_path: str, sleep_sec: float = 0.5) -> dict:
    """Fetch JSON from the FPL API and throttle requests slightly."""
    logging.info("Calling %s", url_path)
    response = await client.get(url=BASE_URL + url_path)
    response.raise_for_status()
    response_body = json.loads(response.content)
    await asyncio.sleep(sleep_sec)
    return response_body


async def fetch_player_summaries(
        client: AsyncClient,
        season: str,
        element_ids: list[str],
        freshness: int,
        sleep_sec: float = 0.5,
) -> dict[str, dict]:
    """Fetch per-player element summaries sequentially and persist snapshots."""
    responses: dict[str, dict] = {}
    for element_id in element_ids:
        store = JsonSnapshotStore(
            SnapshotSpec(base_path=f"data/{season}/elements/{element_id}")
        )

        async def _fetch(resource_id: str = element_id) -> dict:
            return await fetch_json(
                client,
                f"element-summary/{resource_id}/",
                sleep_sec=sleep_sec,
            )

        responses[element_id] = await store.get_or_fetch(freshness, _fetch)

    aggregate_store = JsonSnapshotStore(
        SnapshotSpec(base_path=f"data/{season}/elements")
    )
    aggregate_store.write(responses)
    return responses


async def load(client: AsyncClient, freshness: int = 1):
    season = Season.s2526

    bootstrap_store = JsonSnapshotStore(
        SnapshotSpec(base_path=f"data/{season}/bootstrap")
    )
    fixtures_store = JsonSnapshotStore(
        SnapshotSpec(base_path=f"data/{season}/fixtures")
    )

    main_response_body = await bootstrap_store.get_or_fetch(
        freshness,
        lambda: fetch_json(client, "bootstrap-static/"),
    )
    await fixtures_store.get_or_fetch(
        freshness,
        lambda: fetch_json(client, "fixtures/"),
    )

    await fetch_player_summaries(
        client,
        season,
        [str(element["id"]) for element in main_response_body["elements"]],
        freshness,
    )


async def bootstrap(client: AsyncClient):
    season = Season.s2526
    freshness = 1000

    bootstrap_store = JsonSnapshotStore(
        SnapshotSpec(base_path=f"data/{season}/bootstrap")
    )
    fixtures_store = JsonSnapshotStore(
        SnapshotSpec(base_path=f"data/{season}/fixtures")
    )

    main_response_body = await bootstrap_store.get_or_fetch(
        freshness,
        lambda: fetch_json(client, "bootstrap-static/"),
    )
    fixtures_response_body = await fixtures_store.get_or_fetch(
        freshness,
        lambda: fetch_json(client, "fixtures/"),
    )

    player_response_bodies = await fetch_player_summaries(
        client,
        season,
        [str(element["id"]) for element in main_response_body["elements"]],
        freshness,
    )

    for event in main_response_body['events']:
        Gameweeks.add(event_json_to_gameweek(event))

    for row in main_response_body['teams']:
        Teams.add(team_json_to_team(row))

    for row in fixtures_response_body:
        Fixtures.add(fixture_json_to_fixture(row))

    for player in main_response_body['elements']:
        Players.add(element_json_to_player(player))

    for player_id, row in player_response_bodies.items():
        for fixture in row['history']:
            if not Fixtures.get_one(fixture_id=fixture['fixture']).finished:
                continue
            PlayerFixtures.add(history_entry_to_player_fixture(fixture))
        for fixture in row['fixtures']:
            PlayerFixtures.add(
                future_fixture_to_player_fixture(int(player_id), fixture)
            )
    
    # Load news articles from disk for the next gameweek
    # Only load "fpl_scout" collection
    news_items = list_saved_news(
        collection="fpl_scout",
        gameweek=NEXT_GAMEWEEK,
        include_body=True,
        season=season,
    )
    # Populate News collection from loaded items
    for news_model in news_items:
        News.add(news_model)

    # Load news facts
    news_facts = list_saved_facts(
        season=season,
        gameweek=NEXT_GAMEWEEK,
        collection="fpl_scout"
    )
    for fact in news_facts:
        NewsFacts.add(fact)
