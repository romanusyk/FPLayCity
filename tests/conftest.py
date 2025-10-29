"""
Pytest configuration and shared fixtures.
"""
import asyncio
import pytest

from httpx import AsyncClient
from src.fpl.loader.load import bootstrap
from src.fpl.models.immutable import Teams, Fixtures, Players, PlayerFixtures


def pytest_configure(config):
    """Load data once before all tests run."""
    async def _load():
        client = AsyncClient()
        await bootstrap(client)
        await client.aclose()
        
        # Verify data loaded
        assert len(Teams.items) > 0, f"Teams not loaded (got {len(Teams.items)})"
        assert len(Fixtures.items) > 0, f"Fixtures not loaded (got {len(Fixtures.items)})"
        assert len(Players.items) > 0, f"Players not loaded (got {len(Players.items)})"
        assert len(PlayerFixtures.items) > 0, f"PlayerFixtures not loaded (got {len(PlayerFixtures.items)})"
    
    # Run the async function synchronously
    asyncio.run(_load())

