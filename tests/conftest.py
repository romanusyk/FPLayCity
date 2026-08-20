"""
Pytest configuration and shared fixtures.

Tests split into two groups:
- Tests that need the real FPL dataset request the `fpl_data` fixture, which runs `bootstrap()`
  once per session against cached snapshots under `data/<season>/`.
- Pure unit tests (converters, parsers, metadata) request nothing and run offline.

The next gameweek is resolved the same way every entry point resolves it: `NEXT_GAMEWEEK` when
set, otherwise derived from the stored fixtures.
"""
import asyncio

import pytest
from dotenv import load_dotenv
from httpx import AsyncClient

from src.fpl.loader.load import bootstrap
from src.fpl.loader.utils import Season, resolve_next_gameweek
from src.fpl.models.immutable import Teams, Fixtures, Players, PlayerFixtures


@pytest.fixture(scope="session")
def fpl_data():
    """Load the FPL dataset once per session and populate the global collections."""
    async def _load():
        load_dotenv()
        next_gameweek = resolve_next_gameweek(Season.CURRENT)
        client = AsyncClient(timeout=30.0)
        try:
            await bootstrap(client, next_gameweek=next_gameweek, season=Season.CURRENT)
        finally:
            await client.aclose()

        assert len(Teams.items) > 0, f"Teams not loaded (got {len(Teams.items)})"
        assert len(Fixtures.items) > 0, f"Fixtures not loaded (got {len(Fixtures.items)})"
        assert len(Players.items) > 0, f"Players not loaded (got {len(Players.items)})"
        assert len(PlayerFixtures.items) > 0, f"PlayerFixtures not loaded (got {len(PlayerFixtures.items)})"

    asyncio.run(_load())
    return True


@pytest.fixture(autouse=True)
def _require_fpl_data(request):
    """Give every test in `test_immutable.py` the dataset without editing each test."""
    if request.node.fspath.basename == "test_immutable.py":
        request.getfixturevalue("fpl_data")

