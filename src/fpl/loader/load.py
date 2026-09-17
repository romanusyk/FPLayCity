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
    fpl_presence_json_to_player_presence,
    draft_presence_json_to_player_presence,
    team_json_to_team,
)
from src.fpl.loader.baseline import (
    build_prior_season_baseline,
    load_prior_season_baseline,
    persist_prior_season_baseline,
    top_up_prior_season_from_history,
)
from src.fpl.loader.draft_league import load_ownership as load_league_ownership
from src.fpl.loader import fpl_squad
from src.fpl.loader.http import BASE_DRAFT_URL, BASE_FPL_URL, fetch_json
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

def _fpl_entry() -> int | None:
    """Our classic-FPL entry id, from `data/fpl_entry.json`, or None when none is connected.

    Read from disk rather than from a constant. This used to be `FplManager.ME = 2486591`, written
    into source in December 2025; checked against the live API on 2026-08-26 that entry belongs to
    somebody else, and every presence filed under `is_mine=True` since then described a stranger's
    squad without a murmur. Classic ids being permanent made it worse, not better: a stable wrong
    answer never breaks loudly enough to be noticed. See `src/fpl/loader/fpl_squad.py`.

    Returns None, loudly, when no team is connected - a legitimate state for a fresh checkout.
    """
    try:
        return fpl_squad.load_config().entry_id
    except (FileNotFoundError, ValueError) as exc:
        logger.warning("Skipping classic-FPL manager picks: %s", exc)
        return None


def _draft_entries(season: str) -> list[tuple[int, bool]]:
    """Entry ids in our draft league for `season`, each flagged as ours or not.

    Read from `data/<season>/draft_league.json` and the league snapshot beside it, never from a
    constant. Draft entry ids are re-issued every season: this used to be a hardcoded enum, and
    the id that meant "us" in 2025/26 now serves a stranger's team, which `bootstrap()` would
    have filed as our squad without a murmur.

    Returns an empty list when no league is connected, logging what was skipped and how to fix
    it. That is a legitimate state - a fresh checkout, or a season before the draft - and the
    projection does not need presences at all; only the waivers screen does.
    """
    try:
        ownership = load_league_ownership(season)
    except (FileNotFoundError, ValueError) as exc:
        logger.warning(
            "Skipping draft manager picks: %s", exc,
        )
        return []
    return [(entry.entry_id, entry.is_mine) for entry in ownership.entries.values()]


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


def _restore_or_build_prior_season(
    season: str,
    events: list[dict],
    element_rows: list[dict],
    player_summaries: dict[str, dict],
    team_rows: list[dict],
) -> None:
    """Get last season's totals into `PlayerSeasons`, from the snapshot if there is one.

    The reconciliation in `build_prior_season_baseline` only works **before the season's first
    kickoff**: until then `bootstrap-static` carries each element's previous-season totals, so it
    can be checked against `history_past`. Once a gameweek has been played those fields hold *this*
    season's totals instead, and the check fails on every player who has kicked a ball - Raya read
    as "90 minutes, 1 start" against last season's 3,330.

    So: the persisted snapshot wins whenever it exists. It was captured before kickoff, which is
    the only moment the two sources could be reconciled, and that is exactly why it is written.
    Anyone registered *since* that capture is then topped up from `history_past`, which does not
    rot - otherwise a mid-season signing with a real Premier League past would be projected as if
    he had none.

    Raises:
    - ValueError: when there is no snapshot and the season has already started. Rebuilding from a
      live in-season bootstrap cannot work, and the per-player reconciliation failure it produces
      does not say so. `history_past` is still authoritative, so the fix is a real one - see
      `docs/prediction_roadmap.md` - but it is not a thing to paper over here.
    """
    try:
        load_prior_season_baseline(season)
        added = top_up_prior_season_from_history(
            element_rows=element_rows,
            team_rows=team_rows,
            season=season,
            player_summaries=player_summaries,
        )
        if added:
            logger.info(
                "Prior-season baseline topped up from history_past for %d player(s) registered "
                "after it was captured: %s", len(added), ', '.join(sorted(added)),
            )
            persist_prior_season_baseline(season)
        return
    except FileNotFoundError:
        pass
    if any(event.get('finished') for event in events):
        played = [event['id'] for event in events if event.get('finished')]
        raise ValueError(
            f"No prior-season baseline snapshot for {season}, and gameweek(s) "
            f"{played[0]}-{played[-1]} have already been played. `bootstrap-static` now carries "
            f"*this* season's totals, so last season's cannot be reconciled from it any more - the "
            f"capture had to happen before the first kickoff "
            f"(./run.sh -m src.fpl.fetch --baseline-only). Restore "
            f"data/{season}/prior_season/ from a backup, or accept a season with no prior-season "
            f"evidence by building it from history_past alone."
        )
    build_prior_season_baseline(
        element_rows=element_rows,
        player_summaries=player_summaries,
        team_rows=team_rows,
        season=season,
    ).log()
    persist_prior_season_baseline(season)


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

    fpl_entry = _fpl_entry()
    if fpl_entry is not None:
        for gw in range(1, next_gameweek):
            # Only the last played gameweek is re-fetched: an entry's picks for a finished
            # gameweek never change again, so the older ones are read from disk.
            await fpl_squad.fetch_picks(
                client, fpl_entry, gw, season=season,
                freshness=freshness if gw == next_gameweek - 1 else 1000,
            )

    for entry_id, _ in _draft_entries(season):
        for gw in range(1, next_gameweek):
            json_store = JsonSnapshotStore(
                SnapshotSpec(base_path=f"data/{season}/draft_managers/{entry_id}/picks/{gw}")
            )
            await json_store.get_or_fetch(
                freshness if gw == next_gameweek - 1 else 1000,
                lambda entry_id=entry_id, gw=gw: fetch_json(
                    client, f"entry/{entry_id}/event/{gw}", base_url=BASE_DRAFT_URL
                )
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
    added = top_up_prior_season_from_history(
        element_rows=bootstrap_body['elements'],
        team_rows=bootstrap_body['teams'],
        season=season,
    )
    if added:
        # Not persisted here: this path is the read-only one, and a projection run rewriting a
        # captured baseline as a side effect is exactly the kind of thing that makes an old run
        # stop reproducing. `bootstrap()` persists it.
        logger.info(
            "Prior-season baseline topped up in memory from history_past for %d player(s) "
            "registered after it was captured: %s", len(added), ', '.join(sorted(added)),
        )
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
    _restore_or_build_prior_season(
        season=season,
        events=main_response_body['events'],
        element_rows=main_response_body['elements'],
        player_summaries=player_response_bodies,
        team_rows=main_response_body['teams'],
    )

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
    fpl_entry = _fpl_entry()
    if fpl_entry is not None:
        squad = await fpl_squad.fetch_picks(
            client, fpl_entry, next_gameweek - 1, season=season, freshness=freshness,
        )
        for presence in squad['picks']:
            PlayerPresences.add(fpl_presence_json_to_player_presence(
                row=presence,
                gameweek=next_gameweek - 1,
                manager_id=fpl_entry,
                # The only classic entry we track is our own. When none is connected we file
                # nobody, rather than filing somebody else's fifteen as ours.
                is_mine=True,
            ))

    logger.info("Building draft presences...")
    for entry_id, is_mine in _draft_entries(season):
        json_store = JsonSnapshotStore(
            SnapshotSpec(base_path=f"data/{season}/draft_managers/{entry_id}/picks/{next_gameweek - 1}")
        )
        squad = await json_store.get_or_fetch(
            freshness,
            lambda entry_id=entry_id: fetch_json(
                client, f"entry/{entry_id}/event/{next_gameweek - 1}", base_url=BASE_DRAFT_URL
            )
        )
        for presence in squad['picks']:
            PlayerPresences.add(draft_presence_json_to_player_presence(
                row=presence,
                gameweek=next_gameweek - 1,
                manager_id=entry_id,
                is_mine=is_mine,
            ))
    
    _load_news(season, next_gameweek)
