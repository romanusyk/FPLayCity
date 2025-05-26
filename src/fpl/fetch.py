import asyncio
import httpx
import logging

from src.fpl.loader.load import load

logging.basicConfig(level=logging.INFO)


if __name__ == "__main__":
    client = httpx.AsyncClient()
    asyncio.run(load(client))
