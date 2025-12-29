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
from enum import Enum
from httpx import AsyncClient
from src.fpl.loader.convert import (
    element_json_to_player,
    event_json_to_gameweek,
    fixture_json_to_fixture,
    future_fixture_to_player_fixture,
    history_entry_to_player_fixture,
    fpl_presence_json_to_player_presence,
    draft_presence_json_to_player_presence,
    team_json_to_team,
)
from src.fpl.loader.news.pl import list_saved_news
from src.fpl.loader.news.validate import list_saved_facts
from src.fpl.loader.store import JsonSnapshotStore, SnapshotSpec
from src.fpl.loader.utils import Season
from src.fpl.models.immutable import Fixtures, TeamFixtures, Gameweeks, News, NewsFacts, PlayerFixtures, Players, PlayerPresences, Teams


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BASE_FPL_URL = "https://fantasy.premierleague.com/api/"
BASE_DRAFT_URL = "https://draft.premierleague.com/api/"


class FplManager(Enum):
    ME = 2486591


class DraftManager(Enum):
    ME = 52242
    YURII = 52193
    DEVO = 52210
    GEKA = 151167


async def fetch_json(client: AsyncClient, url_path: str, base_url: str = BASE_FPL_URL, sleep_sec: float = 0.5) -> dict:
    """Fetch JSON from the FPL API and throttle requests slightly."""
    logging.info("Calling %s", url_path)
    response = await client.get(url=base_url + url_path)
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
    aggregate_store = JsonSnapshotStore(
        SnapshotSpec(base_path=f"data/{season}/elements")
    )

    async def _fetch_aggregate() -> dict:
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
        return responses

    return await aggregate_store.get_or_fetch(freshness, _fetch_aggregate)


async def load(client: AsyncClient, next_gameweek: int, freshness: int = 1):
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

    for fpl_manager in FplManager:
        for gw in range(1, next_gameweek):
            json_store = JsonSnapshotStore(
                SnapshotSpec(base_path=f"data/{season}/fpl_managers/{fpl_manager.value}/picks/{gw}")
            )
            await json_store.get_or_fetch(
                freshness if gw == next_gameweek - 1 else 1000,
                lambda: fetch_json(client, f"entry/{fpl_manager.value}/event/{gw}/picks/", base_url=BASE_FPL_URL)
            )

    for draft_manager in DraftManager:
        for gw in range(1, next_gameweek):
            json_store = JsonSnapshotStore(
                SnapshotSpec(base_path=f"data/{season}/draft_managers/{draft_manager.value}/picks/{gw}")
            )
            await json_store.get_or_fetch(
                freshness if gw == next_gameweek - 1 else 1000,
                lambda: fetch_json(client, f"entry/{draft_manager.value}/event/{gw}", base_url=BASE_DRAFT_URL)
            )


async def bootstrap(client: AsyncClient, next_gameweek: int):
    season = Season.s2526
    freshness = 1000

    logger.info("Building bootstrap store...")
    bootstrap_store = JsonSnapshotStore(
        SnapshotSpec(base_path=f"data/{season}/bootstrap")
    )
    logger.info("Building fixtures store...")
    fixtures_store = JsonSnapshotStore(
        SnapshotSpec(base_path=f"data/{season}/fixtures")
    )
    logger.info("Loading bootstrap data...")
    main_response_body = await bootstrap_store.get_or_fetch(
        freshness,
        lambda: fetch_json(client, "bootstrap-static/"),
    )
    logger.info("Loading fixtures data...")
    fixtures_response_body = await fixtures_store.get_or_fetch(
        freshness,
        lambda: fetch_json(client, "fixtures/"),
    )
    logger.info("Loading player summaries...")
    player_response_bodies = await fetch_player_summaries(
        client,
        season,
        [str(element["id"]) for element in main_response_body["elements"]],
        freshness,
    )
    logger.info("Building gameweeks...")
    for event in main_response_body['events']:
        Gameweeks.add(event_json_to_gameweek(event))

    logger.info("Building teams...")
    for row in main_response_body['teams']:
        Teams.add(team_json_to_team(row))

    logger.info("Building fixtures...")
    for row in fixtures_response_body:
        fixture = fixture_json_to_fixture(row)
        Fixtures.add(fixture)
        TeamFixtures.add(fixture.home)
        TeamFixtures.add(fixture.away)

    logger.info("Building players...")
    for player in main_response_body['elements']:
        Players.add(element_json_to_player(player))

    logger.info("Building player fixtures...")
    for player_id, row in player_response_bodies.items():
        for fixture in row['history']:
            if not Fixtures.get_one(fixture_id=fixture['fixture']).finished:
                continue
            PlayerFixtures.add(history_entry_to_player_fixture(fixture))
        for fixture in row['fixtures']:
            PlayerFixtures.add(
                future_fixture_to_player_fixture(int(player_id), fixture)
            )

    logger.info("Building fpl presences...")
    for fpl_manager in FplManager:
        json_store = JsonSnapshotStore(
            SnapshotSpec(base_path=f"data/{season}/fpl_managers/{fpl_manager.value}/picks/{next_gameweek - 2}")
        )
        squad = await json_store.get_or_fetch(
            freshness,
            lambda: fetch_json(client, f"entry/{fpl_manager.value}/event/{next_gameweek - 2}/picks", base_url=BASE_FPL_URL)
        )
        for presence in squad['picks']:
            PlayerPresences.add(fpl_presence_json_to_player_presence(
                row=presence,
                gameweek=next_gameweek - 1,
                manager_id=fpl_manager.value,
                is_mine=fpl_manager == FplManager.ME,
            ))

    logger.info("Building draft presences...")
    for draft_manager in DraftManager:
        json_store = JsonSnapshotStore(
            SnapshotSpec(base_path=f"data/{season}/draft_managers/{draft_manager.value}/picks/{next_gameweek - 1}")
        )
        squad = await json_store.get_or_fetch(
            freshness,
            lambda: fetch_json(client, f"entry/{draft_manager.value}/event/{next_gameweek - 1}", base_url=BASE_DRAFT_URL)
        )
        for presence in squad['picks']:
            PlayerPresences.add(draft_presence_json_to_player_presence(
                row=presence,
                gameweek=next_gameweek - 1,
                manager_id=draft_manager.value,
                is_mine=draft_manager == DraftManager.ME,
            ))
    
    logger.info("Loading news articles...")
    # Load news articles from disk for the next gameweek
    # Only load "fpl_scout" collection
    news_items = list_saved_news(
        collection="fpl_scout",
        gameweek=next_gameweek,
        include_body=True,
        season=season,
    )
    logger.info("Populating News collection...")
    # Populate News collection from loaded items
    for news_model in news_items:
        News.add(news_model)

    logger.info("Loading news facts...")
    # Load news facts
    news_facts = list_saved_facts(
        season=season,
        gameweek=next_gameweek,
        collection="fpl_scout",
    )
    logger.info("Populating News facts...")
    for fact in news_facts:
        NewsFacts.add(fact)
