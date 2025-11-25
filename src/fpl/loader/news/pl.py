import json
import os
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

from httpx import AsyncClient

from src.fpl.loader.load import Season
from src.fpl.loader.utils import ensure_dir_exists


API_BASE = "https://api.premierleague.com/content/premierleague/en"
NEWS_DIR = f"data/{Season.s2526}/news"


def _build_article_url(item: Dict[str, Any]) -> str:
    canonical = item.get("canonicalUrl") or ""
    if canonical:
        return canonical
    article_id = item.get("id")
    slug = item.get("titleUrlSegment") or ""
    if slug:
        return f"https://www.premierleague.com/en/news/{article_id}/{slug}"
    return f"https://www.premierleague.com/en/news/{article_id}"


def _ms_to_iso8601(ms: Optional[int]) -> Optional[str]:
    if not ms:
        return None
    try:
        return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()
    except Exception:
        return None


def _news_filepath(news_id: int) -> str:
    return os.path.join(NEWS_DIR, f"{news_id}.json")


def _is_known(news_id: int, current_date: Optional[str]) -> bool:
    """Return True if article file exists and stored date matches current.

    If the file exists but the date differs, treat as unknown (False) to force
    a rewrite, as the article was updated on the source.
    """
    path = _news_filepath(news_id)
    if not os.path.exists(path):
        return False
    try:
        with open(path, "r") as f:
            saved = json.load(f)
        saved_date = saved.get("date")
        if saved_date == current_date:
            return True
        print(f"[news] known id={news_id} but date changed: saved={saved_date} current={current_date}; will overwrite")
        return False
    except Exception as exc:
        print(f"[news] failed to read existing file for id={news_id}: {exc}; will overwrite")
        return False


def _extract_record(item: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": item.get("id"),
        "url": _build_article_url(item),
        "date": item.get("date"),
        "lastUpdated": _ms_to_iso8601(item.get("lastModified")),
        "title": item.get("title"),
        "summary": item.get("summary") or item.get("description"),
        "body": item.get("body"),
    }


def _parse_article_date(date_str: Optional[str]) -> Optional[datetime]:
    if not date_str:
        return None
    try:
        # Handle trailing 'Z' by replacing with +00:00 for fromisoformat
        if date_str.endswith('Z'):
            date_str = date_str[:-1] + '+00:00'
        return datetime.fromisoformat(date_str)
    except Exception:
        return None


async def fetch_news(client: AsyncClient, offset: int = 0, limit: int = 10) -> Dict[str, Any]:
    """Fetch a page of detailed fantasy news authored by The Scout.

    Mirrors the cURL the user supplied, but uses query params directly.
    """
    params = {
        "contentTypes": "TEXT",
        "offset": str(offset),
        "limit": str(limit),
        "onlyRestrictedContent": "false",
        "detail": "DETAILED",
        # label:Tactics%20and%20Analysis
        "tagExpression": '("series:fantasy")and("content-creator:The-Scout")',
    }
    print(f"[news] GET {API_BASE} offset={offset} limit={limit}")
    response = await client.get(API_BASE, params=params)
    print(f"[news] <- status={response.status_code} bytes={len(response.content)}")
    response.raise_for_status()
    data = json.loads(response.content)
    page_info = data.get("pageInfo") or {}
    content_len = len(data.get("content", []))
    print(f"[news] pageInfo={page_info} content_items={content_len}")
    return data


def read_known_news() -> List[Dict[str, Any]]:
    """Read all saved news metadata (without body)."""
    if not os.path.isdir(NEWS_DIR):
        return []
    results: List[Dict[str, Any]] = []
    for file_name in os.listdir(NEWS_DIR):
        if not file_name.endswith(".json"):
            continue
        full_path = os.path.join(NEWS_DIR, file_name)
        try:
            with open(full_path, "r") as f:
                data = json.load(f)
            data.pop("body", None)
            results.append(data)
        except Exception:
            # Skip malformed files rather than failing the whole read
            continue
    # Sort by date (desc) when available
    results.sort(key=lambda x: x.get("date") or "", reverse=True)
    return results


def read_known_news_content() -> List[Dict[str, Any]]:
    """Read all saved news including full body HTML."""
    if not os.path.isdir(NEWS_DIR):
        return []
    results: List[Dict[str, Any]] = []
    for file_name in os.listdir(NEWS_DIR):
        if not file_name.endswith(".json"):
            continue
        full_path = os.path.join(NEWS_DIR, file_name)
        try:
            with open(full_path, "r") as f:
                data = json.load(f)
            results.append(data)
        except Exception:
            continue
    results.sort(key=lambda x: x.get("date") or "", reverse=True)
    return results


async def load_recent_news(
    client: AsyncClient,
    page_size: int = 10,
    sleep_sec: float = 0.0,
    limit: Optional[int] = None,
) -> List[int]:
    """Paginate through news feed and persist until first known item.

    - Fetches in pages of `page_size`.
    - For each article, if it's already saved, stop pagination and return IDs saved in this run.
    - Otherwise, save under data/2025-2026/news/<news_id>.json with required fields.
    - Returns list of saved news IDs (in the order processed).
    """
    print(f"[news] load_recent_news page_size={page_size} limit={limit} sleep_sec={sleep_sec}")
    saved_ids: List[int] = []

    # Ensure base dir exists before we start
    ensure_dir_exists(os.path.join(NEWS_DIR, "_"))

    offset = 0
    while True:
        page = await fetch_news(client, offset=offset, limit=page_size)
        items: List[Dict[str, Any]] = page.get("content", [])
        if not items:
            print("[news] no items returned; stopping")
            break

        encountered_known = False
        reached_limit = False
        for item in items:
            news_id = item.get("id")
            if news_id is None:
                continue

            if _is_known(news_id, item.get("date")):
                print(f"[news] encountered known id={news_id}; stopping pagination")
                encountered_known = True
                break

            record = _extract_record(item)
            filepath = _news_filepath(news_id)
            ensure_dir_exists(filepath)
            print(f"[news] saving id={news_id} -> {filepath}")
            with open(filepath, "w") as f:
                json.dump(record, f, indent=2)
            saved_ids.append(news_id)

            if limit is not None and len(saved_ids) >= limit:
                print(f"[news] reached --limit ({limit}); stopping")
                reached_limit = True
                break

        if encountered_known or reached_limit:
            break

        offset += page_size
        print(f"[news] advancing offset to {offset}")

        if sleep_sec > 0:
            # Lazy import to avoid making module async dependent
            import asyncio  # noqa: WPS433 local import is intentional
            print(f"[news] sleeping {sleep_sec}s before next page")
            await asyncio.sleep(sleep_sec)

    return saved_ids


def main():
    """CLI entry point for fetching and listing Fantasy news."""
    import argparse
    import asyncio
    import httpx

    parser = argparse.ArgumentParser(description="Fetch and persist Fantasy Premier League news (The Scout)")
    parser.add_argument("--page-size", type=int, default=10, help="Page size for API pagination (default: 10)")
    parser.add_argument("--sleep-sec", type=float, default=0.0, help="Delay between page requests in seconds")
    parser.add_argument("--limit", type=int, default=None, help="Maximum number of new articles to save (default: unlimited)")

    action_group = parser.add_mutually_exclusive_group()
    action_group.add_argument("--list-known", action="store_true", help="List known news (without body)")
    action_group.add_argument("--list-known-content", action="store_true", help="List known news (including body)")

    # Filters for listing
    parser.add_argument("--min-days", type=int, default=None, help="Only include articles from the last N days (inclusive)")
    parser.add_argument("--max-days", type=int, default=None, help="Only include articles older than N days (inclusive)")
    parser.add_argument("--output-json", type=str, default=None, help="Optional path to write selected articles as a JSON list")

    args = parser.parse_args()

    if args.list_known or args.list_known_content:
        items = read_known_news() if args.list_known else read_known_news_content()

        # Apply date filters
        now = datetime.now(timezone.utc)
        filtered: List[dict] = []
        for it in items:
            dt = _parse_article_date(it.get("date"))
            if dt is None:
                continue
            # Normalize to aware UTC if naive
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)

            include = True
            if args.min_days is not None:
                # from last N days => dt >= now - N days
                threshold = now.replace(microsecond=0) - timedelta(days=args.min_days)
                if dt < threshold:
                    include = False
            if include and args.max_days is not None:
                # older than N days => dt <= now - N days
                threshold = now.replace(microsecond=0) - timedelta(days=args.max_days)
                if dt > threshold:
                    include = False
            if include:
                filtered.append(it)

        print(f"Found {len(filtered)} saved articles in {NEWS_DIR} after filtering (from {len(items)})")
        for item in filtered:
            print(f"- {item.get('id')} | {item.get('date')} | {item.get('title')}")

        if args.output_json:
            try:
                ensure_dir_exists(args.output_json)
                with open(args.output_json, "w") as f:
                    json.dump(filtered, f, indent=2)
                print(f"Wrote {len(filtered)} articles to {args.output_json}")
            except Exception as exc:
                print(f"Failed to write JSON to {args.output_json}: {exc}")
        return

    async def _run() -> None:
        async with httpx.AsyncClient() as client:
            saved = await load_recent_news(
                client,
                page_size=args.page_size,
                sleep_sec=args.sleep_sec,
                limit=args.limit,
            )
            if saved:
                print(f"Saved {len(saved)} new articles: {saved}")
            else:
                print("No new articles to save")

    asyncio.run(_run())


if __name__ == "__main__":
    main()


