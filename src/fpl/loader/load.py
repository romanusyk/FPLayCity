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
    news_stored_json_to_model,
    team_json_to_team,
)
from src.fpl.loader.store import JsonSnapshotStore, SnapshotSpec
from src.fpl.models.immutable import Fixtures, Gameweeks, PlayerFixtures, Players, Teams

BASE_URL = "https://fantasy.premierleague.com/api/"
NEXT_GAMEWEEK = 15


class Season:

    s2425 = '2024-2025'
    s2526 = '2025-2026'


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
    try:
        from src.fpl.loader.news.pl import list_saved_news
        news_items = list_saved_news(
            collection="fpl_scout",
            gameweek=NEXT_GAMEWEEK,
            include_body=True,
            season=season,
        )
        # Populate News collection from loaded items
        from src.fpl.models.immutable import News as NewsCollection
        
        for item in news_items:
            # Convert stored JSON to NewsModel using converter
            news_model = news_stored_json_to_model(
                item,
                default_gameweek=NEXT_GAMEWEEK,
                default_collection="fpl_scout",
            )
            NewsCollection.add(news_model)
    except FileNotFoundError:
        # No news directory or no articles for this gameweek - this is fine
        pass
