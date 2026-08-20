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
from src.fpl.loader.baseline import (
    build_prior_season_baseline,
    load_prior_season_baseline,
    persist_prior_season_baseline,
)
from src.fpl.loader.news.pl import list_saved_news
from src.fpl.loader.news.validate import list_saved_facts
from src.fpl.loader.store import JsonSnapshotStore, SnapshotSpec
from src.fpl.loader.utils import Season
from src.fpl.models.immutable import (
    Fixtures,
    TeamFixtures,
    Gameweeks,
    News,
    NewsFacts,
    PlayerFixtures,
    Players,
    PlayerPresences,
    PlayerSeasons,
    Teams,
)


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


async def load(client: AsyncClient, next_gameweek: int, freshness: int = 1, season: str | None = None):
    season = season or Season.CURRENT

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


def _load_news(season: str, next_gameweek: int) -> None:
    """Populate the News and NewsFacts collections from disk for `next_gameweek`."""
    logger.info("Loading news articles...")
    news_items = list_saved_news(
        collection="fpl_scout",
        gameweek=next_gameweek,
        include_body=True,
        season=season,
    )
    logger.info("Populating News collection...")
    for news_model in news_items:
        News.add(news_model)

    logger.info("Loading news facts...")
    news_facts = list_saved_facts(
        season=season,
        gameweek=next_gameweek,
        collection="fpl_scout",
    )
    logger.info("Populating News facts...")
    for fact in news_facts:
        NewsFacts.add(fact)


async def capture_prior_season(client: AsyncClient, season: str | None = None, freshness: int = 1) -> str:
    """Fetch what is needed to snapshot the previous season's per-player totals.

    Run this **before** the new season's first kickoff. Until then `bootstrap-static` still
    carries last season's totals against each player's new club, so this is the last chance to
    capture a complete baseline from the live API.

    Parameters:
    - client: HTTP client for the FPL API.
    - season: Season being loaded. Defaults to `Season.CURRENT`.
    - freshness: Days before a cached snapshot is considered stale.

    Returns:
    - Path of the derived baseline snapshot.
    """
    season = season or Season.CURRENT
    bootstrap_store = JsonSnapshotStore(SnapshotSpec(base_path=f"data/{season}/bootstrap"))
    main_response_body = await bootstrap_store.get_or_fetch(
        freshness,
        lambda: fetch_json(client, "bootstrap-static/"),
    )
    logger.info("Fetching element summaries for %d players...", len(main_response_body["elements"]))
    player_response_bodies = await fetch_player_summaries(
        client,
        season,
        [str(element["id"]) for element in main_response_body["elements"]],
        freshness,
    )
    build_prior_season_baseline(
        element_rows=main_response_body["elements"],
        player_summaries=player_response_bodies,
        team_rows=main_response_body["teams"],
        season=season,
    ).log()
    return persist_prior_season_baseline(season)


def load_from_snapshots(season: str | None = None) -> None:
    """Populate the core collections from stored snapshots, with no network access.

    `bootstrap()` is the loader for a live session: it fetches, refreshes, reads manager picks
    and news, and needs an HTTP client. Projection runs and the web app need none of that -
    they need Teams, Gameweeks, Fixtures, Players and the prior-season baseline exactly as they
    were captured, and they need it to be reproducible. Two callers, two entry points.

    Parameters:
    - season: season to load. Defaults to `Season.CURRENT`.

    The collections are process-level singletons, so they are cleared first. Loading twice in
    one process is a legitimate thing to do - a test session, or serving a different season -
    and appending would collide on every key.

    Raises:
    - FileNotFoundError: if the bootstrap or fixtures snapshot is missing. Fetch them with
      `uv run -m src.fpl.fetch`.
    """
    season = season or Season.CURRENT
    bootstrap_body = JsonSnapshotStore(SnapshotSpec(base_path=f"data/{season}/bootstrap")).load_latest()
    fixtures_body = JsonSnapshotStore(SnapshotSpec(base_path=f"data/{season}/fixtures")).load_latest()

    for collection in (Gameweeks, Teams, Fixtures, TeamFixtures, Players, PlayerSeasons):
        collection.clear()

    for event in bootstrap_body['events']:
        Gameweeks.add(event_json_to_gameweek(event))
    for row in bootstrap_body['teams']:
        Teams.add(team_json_to_team(row))
    for row in fixtures_body:
        fixture = fixture_json_to_fixture(row)
        Fixtures.add(fixture)
        TeamFixtures.add(fixture.home)
        TeamFixtures.add(fixture.away)
    for element in bootstrap_body['elements']:
        Players.add(element_json_to_player(element))

    load_prior_season_baseline(season)
    logger.info(
        "Loaded %s from snapshots: %d teams, %d players, %d fixtures",
        season, len(Teams.items), len(Players.items), len(Fixtures.items),
    )


async def bootstrap(client: AsyncClient, next_gameweek: int, season: str | None = None):
    season = season or Season.CURRENT
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

    logger.info("Building prior-season baseline...")
    build_prior_season_baseline(
        element_rows=main_response_body['elements'],
        player_summaries=player_response_bodies,
        team_rows=main_response_body['teams'],
        season=season,
    ).log()
    persist_prior_season_baseline(season)

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

    if next_gameweek <= 1:
        # Nobody has picked a squad before GW1, so `entry/.../event/0/...` does not exist.
        # This is a structural property of a fresh season, not missing data.
        logger.info("Skipping manager presences: no squads are picked before GW1.")
        _load_news(season, next_gameweek)
        return

    logger.info("Building fpl presences...")
    for fpl_manager in FplManager:
        json_store = JsonSnapshotStore(
            SnapshotSpec(base_path=f"data/{season}/fpl_managers/{fpl_manager.value}/picks/{next_gameweek - 1}")
        )
        squad = await json_store.get_or_fetch(
            freshness,
            lambda: fetch_json(client, f"entry/{fpl_manager.value}/event/{next_gameweek - 1}/picks/", base_url=BASE_FPL_URL)
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
    
    _load_news(season, next_gameweek)
