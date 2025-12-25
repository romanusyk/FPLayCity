import asyncio
import httpx
import logging
import os

from dotenv import load_dotenv

from src.fpl.loader.load import load

logging.basicConfig(level=logging.INFO)


if __name__ == "__main__":
    client = httpx.AsyncClient()
    load_dotenv()
    next_gameweek = int(os.getenv("NEXT_GAMEWEEK"))
    if not next_gameweek:
        raise ValueError("NEXT_GAMEWEEK environment variable is not set")
    asyncio.run(load(client, next_gameweek))
