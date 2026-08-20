"""Serve the review app.

    uv run -m src.web.serve                  # http://127.0.0.1:8000
    uv run -m src.web.serve --port 8123
    uv run -m src.web.serve --season 2025-2026

Localhost only, single user, no authentication. Binding to 127.0.0.1 is deliberate: the app
writes files - feedback and draft state - and nothing here is built to be reachable from
another machine.

The app reads run artifacts written by `uv run -m src.fpl.project`. It never projects anything
itself, so a model change is visible as soon as you regenerate, without restarting the server.
"""
from __future__ import annotations

import argparse
import logging
import os

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from src.fpl.loader.utils import Season
from src.web.api import router
from src.web.context import AppContext


logging.basicConfig(level=logging.INFO, format='%(levelname)s %(name)s: %(message)s')
logger = logging.getLogger(__name__)

STATIC_DIR = os.path.join(os.path.dirname(__file__), 'static')
INDEX = os.path.join(STATIC_DIR, 'index.html')


def create_app(season: str | None = None, next_gameweek: int | None = None) -> FastAPI:
    """Build the ASGI app with its context already loaded.

    Loading at construction rather than on first request means a missing snapshot fails at
    startup, in the terminal, instead of as a 500 on the first page view.
    """
    AppContext.initialise(season, next_gameweek)
    app = FastAPI(title='FPLayCity review app', docs_url='/api/docs', openapi_url='/api/openapi.json')
    app.include_router(router)
    app.mount('/static', StaticFiles(directory=STATIC_DIR), name='static')

    @app.get('/', include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(INDEX)

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--season', default=Season.CURRENT)
    parser.add_argument('--host', default='127.0.0.1', help='Localhost by default; the app writes files.')
    parser.add_argument('--port', type=int, default=8000)
    parser.add_argument('--next-gameweek', type=int, help='Overrides NEXT_GAMEWEEK from .env.')
    args = parser.parse_args()

    import uvicorn
    from dotenv import load_dotenv

    load_dotenv()
    app = create_app(args.season, args.next_gameweek)
    logger.info("Serving %s on http://%s:%d", args.season, args.host, args.port)
    uvicorn.run(app, host=args.host, port=args.port, log_level='info')


if __name__ == '__main__':
    main()
