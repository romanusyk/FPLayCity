"""Shared HTTP access to the two Premier League fantasy APIs.

Both games are read-only JSON over HTTPS with no authentication, and both are polite-rate-limit
territory rather than hard-quota territory, so one helper with a small sleep covers every caller.

The two base URLs are separate games, not two paths of one API:
- `BASE_FPL_URL` - classic Fantasy Premier League. Entry ids here are permanent.
- `BASE_DRAFT_URL` - FPL Draft. Entry and league ids here are **re-issued every season**; see
  `src/fpl/loader/draft_league.py` for what that costs if you store one in source.
"""
from __future__ import annotations

import asyncio
import json
import logging

from httpx import AsyncClient


logger = logging.getLogger(__name__)

BASE_FPL_URL = "https://fantasy.premierleague.com/api/"
BASE_DRAFT_URL = "https://draft.premierleague.com/api/"


async def fetch_json(
    client: AsyncClient,
    url_path: str,
    base_url: str = BASE_FPL_URL,
    sleep_sec: float = 0.5,
) -> dict:
    """Fetch JSON from a fantasy API and throttle requests slightly.

    Raises:
    - httpx.HTTPStatusError: on any non-2xx response. Never returns a partial or empty body:
      a 404 that became an empty dict would look like "this manager owns nobody".
    """
    logger.info("Calling %s", url_path)
    response = await client.get(url=base_url + url_path)
    response.raise_for_status()
    response_body = json.loads(response.content)
    await asyncio.sleep(sleep_sec)
    return response_body
