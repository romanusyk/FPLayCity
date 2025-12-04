from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List

from src.fpl.models.immutable import NewsClass as NewsModel, Tag


def _build_article_url(item: Dict[str, Any]) -> str:
    canonical = item.get("canonicalUrl") or ""
    if canonical:
        return canonical
    article_id = item.get("id")
    slug = item.get("titleUrlSegment") or ""
    if slug:
        return f"https://www.premierleague.com/en/news/{article_id}/{slug}"
    return f"https://www.premierleague.com/en/news/{article_id}"


def _ms_to_iso8601(ms: int | None) -> str | None:
    if ms is None:
        return None
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()


def tags_json_to_tags(tags_data: List[Dict[str, Any]]) -> List[Tag]:
    """Convert a list of tag dicts to Tag dataclasses."""
    tags: List[Tag] = []
    for tag_item in tags_data:
        tag_id = tag_item.get("id")
        tag_label = tag_item.get("label")
        if tag_id is None or tag_label is None:
            continue
        tags.append(Tag(id=tag_id, label=tag_label))
    return tags


def news_json_to_model(item: Dict[str, Any], collection: str) -> NewsModel:
    """Convert a PL news API record to a NewsModel without assigning gameweek."""
    if item.get("id") is None:
        raise ValueError("News item is missing required 'id' field.")
    tags = tags_json_to_tags(item.get("tags", []))
    return NewsModel(
        id=item["id"],
        url=_build_article_url(item),
        date=item.get("date") or "",
        lastUpdated=_ms_to_iso8601(item.get("lastModified")) or "",
        title=item.get("title") or "",
        summary=item.get("summary") or item.get("description") or "",
        body=item.get("body") or "",
        tags=tags,
        gameweek=0,
        collection=collection,
    )


def news_model_to_json(news: NewsModel) -> Dict[str, Any]:
    """Convert a NewsModel back to a JSON-serializable dict."""
    return {
        "id": news.id,
        "url": news.url,
        "date": news.date,
        "lastUpdated": news.lastUpdated,
        "title": news.title,
        "summary": news.summary,
        "body": news.body,
        "tags": [{"id": tag.id, "label": tag.label} for tag in news.tags],
        "gameweek": news.gameweek,
        "collection": news.collection,
    }


def news_stored_json_to_model(item: Dict[str, Any], default_gameweek: int = 0, default_collection: str = "") -> NewsModel:
    """Convert stored JSON (from news_model_to_json) back to a NewsModel."""
    if item.get("id") is None:
        raise ValueError("News item is missing required 'id' field.")
    tags = tags_json_to_tags(item.get("tags", []))
    return NewsModel(
        id=item["id"],
        url=item.get("url", ""),
        date=item.get("date", ""),
        lastUpdated=item.get("lastUpdated", ""),
        title=item.get("title", ""),
        summary=item.get("summary", ""),
        body=item.get("body", ""),
        tags=tags,
        gameweek=item.get("gameweek", default_gameweek),
        collection=item.get("collection", default_collection),
    )

