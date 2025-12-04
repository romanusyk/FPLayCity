import argparse
import asyncio
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

import httpx
from httpx import AsyncClient

from src.fpl.loader.convert import event_json_to_gameweek
from src.fpl.loader.convert.news import news_json_to_model, news_model_to_json
from src.fpl.loader.load import Season
from src.fpl.loader.store import JsonSnapshotStore, SnapshotSpec
from src.fpl.models.immutable import Gameweek, News as NewsCollection, NewsClass as NewsModel


API_BASE = "https://api.premierleague.com/content/premierleague/en"
SEASON = Season.s2526
NEWS_DIR = f"data/{SEASON}/news"
DEFAULT_SLEEP_SEC = 0.5


@dataclass
class NewsCollectionConfig:
    """Configuration for a news collection source."""
    collection_id: str
    api_base: str
    api_params: Dict[str, Any]
    extract_record: Callable[[Dict[str, Any], str], NewsModel]


def load_gameweeks_from_store(season: str = SEASON) -> List[Gameweek]:
    """Load bootstrap snapshot via JsonSnapshotStore and convert to Gameweek metadata."""
    store = JsonSnapshotStore(SnapshotSpec(base_path=f"data/{season}/bootstrap"))
    snapshot = store.load_latest()
    events = snapshot.get("events")
    if not events:
        raise ValueError("Bootstrap snapshot is missing 'events'; cannot derive gameweeks.")
    gameweeks = [event_json_to_gameweek(event) for event in events]
    return sorted(gameweeks, key=lambda gw: gw.gameweek)


def _parse_article_date(date_str: Optional[str]) -> datetime:
    if not date_str:
        raise ValueError("News article is missing a publication timestamp.")
    if date_str.endswith("Z"):
        date_str = date_str[:-1] + "+00:00"
    dt = datetime.fromisoformat(date_str)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _assign_gameweek(timestamp: datetime, gameweeks: List[Gameweek]) -> int:
    if not gameweeks:
        raise ValueError("Cannot assign gameweek without bootstrap metadata.")
    sorted_gws = sorted(gameweeks, key=lambda gw: gw.gameweek)
    first_gw = sorted_gws[0]
    if timestamp <= first_gw.deadline_time:
        return first_gw.gameweek
    for prev, current in zip(sorted_gws, sorted_gws[1:]):
        if prev.deadline_time < timestamp <= current.deadline_time:
            return current.gameweek
    return sorted_gws[-1].gameweek


def _persist_news_article(season: str, news_record: NewsModel) -> str:
    """Persist a news article using JsonSnapshotStore with timestamped filenames."""
    base_path = f"data/{season}/news/{news_record.gameweek}/{news_record.collection}/raw/{news_record.id}"
    store = JsonSnapshotStore(SnapshotSpec(base_path=base_path))
    filepath = store.write(news_model_to_json(news_record), delete_older=True)
    return filepath


def _derive_gameweek_bounds(
    page_gameweeks: List[int],
    first_gw: Optional[int],
    last_gw: Optional[int],
) -> tuple[int, int]:
    if not page_gameweeks:
        raise ValueError("Unable to derive default gameweek bounds from an empty page.")
    derived_last = max(page_gameweeks)
    resolved_last = last_gw if last_gw is not None else derived_last
    resolved_first = first_gw if first_gw is not None else max(1, resolved_last - 1)
    if resolved_last < resolved_first:
        resolved_last = resolved_first
    return resolved_first, resolved_last




def _get_fpl_scout_config() -> NewsCollectionConfig:
    """Get configuration for FPL Scout collection."""
    return NewsCollectionConfig(
        collection_id="fpl_scout",
        api_base=API_BASE,
        api_params={
            "contentTypes": "TEXT",
            "offset": "0",
            "limit": "10",
            "onlyRestrictedContent": "false",
            "detail": "DETAILED",
            "tagExpression": '("series:fantasy")and("content-creator:The-Scout")',
        },
        extract_record=news_json_to_model,
    )


async def fetch_news(
    client: AsyncClient,
    config: NewsCollectionConfig,
    offset: int = 0,
    limit: int = 10,
) -> Dict[str, Any]:
    """Fetch a page of news using collection-specific configuration."""
    params = config.api_params.copy()
    params["offset"] = str(offset)
    params["limit"] = str(limit)
    print(f"[news] GET {config.api_base} offset={offset} limit={limit}")
    response = await client.get(config.api_base, params=params)
    print(f"[news] <- status={response.status_code} bytes={len(response.content)}")
    response.raise_for_status()
    data = json.loads(response.content)
    page_info = data.get("pageInfo") or {}
    content_len = len(data.get("content", []))
    print(f"[news] pageInfo={page_info} content_items={content_len}")
    return data


def list_saved_news(
    *,
    collection: str,
    gameweek: int,
    include_body: bool,
    season: str = SEASON,
) -> List[Dict[str, Any]]:
    """Load articles from disk for a specific gameweek and collection, return serialized dicts for CLI output."""
    news_dir = f"data/{season}/news/{gameweek}/{collection}/raw"
    if not os.path.isdir(news_dir):
        return []

    loaded_articles: List[Dict[str, Any]] = []
    
    # Scan for timestamped article files: {article_id}_{timestamp}.json
    # Extract unique article IDs from filenames
    seen_article_ids: set[str] = set()
    
    for filename in os.listdir(news_dir):
        if not filename.endswith(".json"):
            continue
        
        # Extract article ID from filename (format: {id}_{timestamp}.json)
        # Find the last underscore before .json to split ID from timestamp
        parts = filename[:-5].rsplit("_", 1)  # Remove .json, split on last _
        if len(parts) != 2:
            # Skip files that don't match the expected format
            continue
        
        article_id_str = parts[0]
        if article_id_str in seen_article_ids:
            # Already processed this article (shouldn't happen with delete_older=True, but handle it)
            continue
        
        seen_article_ids.add(article_id_str)
        
        # Use JsonSnapshotStore to load the latest snapshot for this article
        base_path = os.path.join(news_dir, article_id_str)
        try:
            store = JsonSnapshotStore(SnapshotSpec(base_path=base_path))
            article_data = store.load_latest()
            loaded_articles.append(article_data)
        except FileNotFoundError:
            # Skip if no snapshot found (shouldn't happen, but handle gracefully)
            continue
        except Exception as exc:
            # Fail loudly on other errors (per project policy)
            raise ValueError(f"Failed to load article {article_id_str} from {news_dir}: {exc}") from exc

    # Filter and format results
    filtered: List[Dict[str, Any]] = []
    for article_data in loaded_articles:
        payload = article_data.copy()
        if not include_body:
            payload["body"] = None
        filtered.append(payload)

    filtered.sort(key=lambda item: item.get("date") or "", reverse=True)
    return filtered


async def load_recent_news(
    client: AsyncClient,
    collection_config: NewsCollectionConfig,
    gameweeks: List[Gameweek],
    season: str = SEASON,
    page_size: int = 10,
    sleep_sec: float = DEFAULT_SLEEP_SEC,
    limit: Optional[int] = None,
    first_gw: Optional[int] = None,
    last_gw: Optional[int] = None,
) -> List[int]:
    """Fetch pages sequentially, persist every article, and stop at the requested window."""
    print(
        "[news] load_recent_news collection=%s page_size=%s limit=%s sleep_sec=%s first_gw=%s last_gw=%s"
        % (
            collection_config.collection_id,
            page_size,
            limit,
            sleep_sec,
            first_gw,
            last_gw,
        )
    )
    saved_ids: List[int] = []
    offset = 0
    pending_bounds = first_gw is None or last_gw is None
    effective_first = first_gw
    effective_last = last_gw

    while True:
        page = await fetch_news(client, collection_config, offset=offset, limit=page_size)

        items: List[Dict[str, Any]] = page.get("content", [])
        if not items:
            print("[news] no items returned; stopping pagination")
            break

        page_gameweeks: List[int] = []
        stop_after_page = False

        for item in items:
            news_record = collection_config.extract_record(item, collection_config.collection_id)
            timestamp_source = news_record.lastUpdated or news_record.date
            timestamp = _parse_article_date(timestamp_source)
            gameweek = _assign_gameweek(timestamp, gameweeks)
            news_record.gameweek = gameweek
            page_gameweeks.append(gameweek)

            filepath = _persist_news_article(season, news_record)
            print(f"[news] saved id={news_record.id} gw={gameweek} -> {filepath}")
            saved_ids.append(news_record.id)

            if effective_first is not None and gameweek < effective_first:
                stop_after_page = True

        if pending_bounds:
            effective_first, effective_last = _derive_gameweek_bounds(page_gameweeks, effective_first, effective_last)
            pending_bounds = False
            print(f"[news] default gw window: first_gw={effective_first} last_gw={effective_last}")

        limit_reached = limit is not None and len(saved_ids) >= limit

        if stop_after_page:
            print(f"[news] reached gw<{effective_first}; stopping after offset {offset}")
            break
        if limit_reached:
            print(f"[news] reached --limit={limit}; stopping after offset {offset}")
            break

        offset += page_size
        if sleep_sec > 0:
            print(f"[news] sleeping {sleep_sec}s before next page")
            await asyncio.sleep(sleep_sec)

    return saved_ids


def main():
    """CLI entry point for fetching and listing Fantasy news."""
    # Collection registry
    ALLOWED_COLLECTIONS = {
        "fpl_scout": _get_fpl_scout_config,
    }

    parser = argparse.ArgumentParser(
        description="Fetch and persist Fantasy Premier League news",
        epilog="Examples:\n"
               "  %(prog)s fpl_scout --last-gw=15\n"
               "  %(prog)s fpl_scout --last-gw=15 --first-gw=14\n"
               "  %(prog)s fpl_scout --last-gw=15 --list-known-content",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "news_collection",
        choices=list(ALLOWED_COLLECTIONS.keys()),
        help="News collection to use (currently only 'fpl_scout' is supported)",
    )
    parser.add_argument("--page-size", type=int, default=10, help="Page size for API pagination (default: 10)")
    parser.add_argument("--sleep-sec", type=float, default=DEFAULT_SLEEP_SEC, help="Delay between page requests in seconds")
    parser.add_argument("--limit", type=int, default=None, help="Maximum number of freshly saved articles (default: unlimited)")
    parser.add_argument("--first-gw", type=int, default=None, help="Lower bound for gameweek window")
    parser.add_argument("--last-gw", type=int, required=True, help="Upper bound for gameweek window (required)")

    action_group = parser.add_mutually_exclusive_group()
    action_group.add_argument("--list-known", action="store_true", help="List saved news metadata (body omitted)")
    action_group.add_argument("--list-known-content", action="store_true", help="List saved news including body HTML")

    args = parser.parse_args()

    # Resolve collection config
    collection_config = ALLOWED_COLLECTIONS[args.news_collection]()

    if args.list_known or args.list_known_content:
        include_body = args.list_known_content
        # If first_gw is None, set it to last_gw (load only that gameweek)
        first_gw = args.first_gw if args.first_gw is not None else args.last_gw
        
        # Load articles for each gameweek in the range
        all_items: List[Dict[str, Any]] = []
        for gw in range(first_gw, args.last_gw + 1):
            items = list_saved_news(
                collection=args.news_collection,
                gameweek=gw,
                include_body=include_body,
            )
            all_items.extend(items)
        
        print(f"Found {len(all_items)} saved articles in {NEWS_DIR}")
        for record in all_items:
            print(f"- {record.get('id')} | {record.get('date')} | GW{record.get('gameweek')} | {record.get('title')}")
        return

    async def _run() -> None:
        gameweeks = load_gameweeks_from_store(SEASON)
        async with httpx.AsyncClient() as client:
            saved = await load_recent_news(
                client,
                collection_config=collection_config,
                gameweeks=gameweeks,
                season=SEASON,
                page_size=args.page_size,
                sleep_sec=args.sleep_sec,
                limit=args.limit,
                first_gw=args.first_gw,
                last_gw=args.last_gw,
            )
            if saved:
                print(f"Saved {len(saved)} new articles: {saved}")
            else:
                print("No new articles to save")

    asyncio.run(_run())


if __name__ == "__main__":
    main()


