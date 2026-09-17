"""Who owns whom in an FPL Draft league.

Why this exists
---------------
A waiver is a squad decision, not a board decision. The only players you can sign are the ones
nobody in your league owns, and the only way to sign one is to drop someone you already have
(FPL Draft rules, "Transactions (unsigned players)"). Both halves of that need the league's
ownership map, and it has to be the league's rather than a guess.

`league/{league_id}/element-status` answers it in one request: every element in the game, the
entry that owns it, and whether it can be claimed at all. That is the whole input this module
persists. The older per-entry path (`entry/{id}/event/{gw}`, still used by `bootstrap()` for
`PlayerPresences`) describes a squad *at a past deadline*, so every waiver processed since is
invisible in it - fine for scoring history, wrong for deciding a transfer.

The identity trap
-----------------
**Draft entry ids and league ids are re-issued every season.** They are not stable identities
like a classic-FPL entry id, which is why `FplManager` in `src/fpl/loader/load.py` can hardcode
one and this cannot. Checked against the live API on 2026-08-26: entry 52242, hardcoded as
`DraftManager.ME` for the whole of 2025/26, now serves a stranger's 2026/27 team ("Spartak
Gotham", 35 points, a league we are not in). Nothing would have raised - `bootstrap()` would have
filed his fifteen players as ours. The ids therefore live in `data/<season>/draft_league.json`,
under the season they belong to, written by `./run.sh -m src.fpl.league`. Same class of bug as the
element-id tables in `CLAUDE.md`: an id written into source is a time bomb with a July fuse.

Statuses
--------
`element_status` reports three things per element, and the difference between them decides
whether a player can appear in a waiver suggestion at all:

- `a` - available. No squad holds him; he is claimable.
- `o` - owned. Some squad in the league holds him; `owner` says which.
- `l` - locked. Newly added to the game, or dropped by another squad within the current waiver
  window. Not claimable until the next deadline, however good he looks.

An unrecognised code is counted and reported rather than assumed available, because quietly
treating "locked" as claimable produces a waiver list whose top pick cannot be submitted.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime

from httpx import AsyncClient

from src.fpl.loader.http import BASE_DRAFT_URL, fetch_json
from src.fpl.loader.store import JsonSnapshotStore, SnapshotSpec
from src.fpl.loader.utils import Season, ensure_dir_exists


logger = logging.getLogger(__name__)


AVAILABLE = 'a'
OWNED = 'o'
LOCKED = 'l'

STATUS_MEANINGS = {
    AVAILABLE: 'available',
    OWNED: 'owned by a squad in the league',
    LOCKED: 'locked until the next waiver deadline',
}

SQUAD_SIZE = 15
"""Every entry in a draft league holds exactly fifteen players at all times."""


def config_path(season: str) -> str:
    return os.path.join('data', season, 'draft_league.json')


def league_dir(season: str, league_id: int) -> str:
    return os.path.join('data', season, 'draft_league', str(league_id))


@dataclass(frozen=True)
class LeagueConfig:
    """Which draft league and which team in it are ours, for one season.

    Season-scoped by construction: the file lives under `data/<season>/`, so last season's ids
    cannot be read by accident.
    """

    season: str
    league_id: int
    entry_id: int
    entry_name: str
    manager: str
    league_name: str
    transaction_mode: str

    def as_dict(self) -> dict:
        return {
            'season': self.season,
            'league_id': self.league_id,
            'entry_id': self.entry_id,
            'entry_name': self.entry_name,
            'manager': self.manager,
            'league_name': self.league_name,
            'transaction_mode': self.transaction_mode,
        }


def save_config(config: LeagueConfig) -> str:
    path = config_path(config.season)
    ensure_dir_exists(path)
    with open(path, 'w', encoding='utf-8') as handle:
        json.dump(config.as_dict(), handle, indent=2)
    logger.info(
        "Draft league saved: %s (league %d), our entry %d '%s'",
        config.league_name, config.league_id, config.entry_id, config.entry_name,
    )
    return path


def load_config(season: str | None = None) -> LeagueConfig:
    """Read the stored league configuration.

    Raises:
    - FileNotFoundError: when the league has never been connected, with the command that does
      it. Defaulting to any league id would be worse than useless - it would silently value
      somebody else's squad.
    - ValueError: when the file exists but is unreadable, or was written for another season.
    """
    season = season or Season.CURRENT
    path = config_path(season)
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"No draft league connected for {season} ({path} does not exist). Connect one with: "
            f"./run.sh -m src.fpl.league --entry <your draft entry id>. Your entry id is the "
            f"number in the URL of your team page on draft.premierleague.com."
        )
    try:
        with open(path, encoding='utf-8') as handle:
            body = json.load(handle)
        config = LeagueConfig(
            season=body['season'],
            league_id=int(body['league_id']),
            entry_id=int(body['entry_id']),
            entry_name=body['entry_name'],
            manager=body['manager'],
            league_name=body['league_name'],
            transaction_mode=body['transaction_mode'],
        )
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"{path} is not a readable draft league config: {exc}") from exc
    if config.season != season:
        raise ValueError(
            f"{path} holds ids for {config.season} but was read as {season}. Draft entry and "
            f"league ids are re-issued every season, so these are not the same league. "
            f"Re-connect with: ./run.sh -m src.fpl.league --entry <id>"
        )
    return config


@dataclass(frozen=True)
class DraftEntry:
    """One manager's team in the league."""

    entry_id: int
    entry_name: str
    manager: str
    is_mine: bool
    waiver_pick: int | None
    """Waiver priority, lowest number first. `None` before the league publishes an order."""

    def as_dict(self) -> dict:
        return {
            'entry_id': self.entry_id,
            'entry_name': self.entry_name,
            'manager': self.manager,
            'is_mine': self.is_mine,
            'waiver_pick': self.waiver_pick,
        }


@dataclass(frozen=True)
class LeagueOwnership:
    """Every element in the game, mapped to the squad that holds it.

    Invariants, all checked in `build_ownership`:
    - Exactly one entry is ours.
    - Every entry holds exactly `SQUAD_SIZE` players.
    - Element ids are this season's. They are never comparable across seasons, and neither are
      the entry ids they map to.
    """

    season: str
    league_id: int
    league_name: str
    transaction_mode: str
    fetched_at: str
    entries: dict[int, DraftEntry]
    owner_by_element: dict[int, int | None]
    status_by_element: dict[int, str]
    unknown_statuses: dict[str, int]
    """Status codes the API returned that this module does not model, and how many carried them."""

    @property
    def my_entry(self) -> DraftEntry:
        return next(entry for entry in self.entries.values() if entry.is_mine)

    def squad_of(self, entry_id: int) -> set[int]:
        return {element for element, owner in self.owner_by_element.items() if owner == entry_id}

    @property
    def my_squad(self) -> set[int]:
        return self.squad_of(self.my_entry.entry_id)

    def is_claimable(self, element: int) -> bool:
        """Unowned *and* not locked. Both conditions are required to submit a waiver."""
        return (
            self.owner_by_element.get(element, None) is None
            and self.status_by_element.get(element) == AVAILABLE
        )

    def owner_label(self, element: int) -> str:
        """Who holds this player, in words, for a table cell."""
        owner = self.owner_by_element.get(element)
        if owner is None:
            status = self.status_by_element.get(element, AVAILABLE)
            return 'free agent' if status == AVAILABLE else STATUS_MEANINGS.get(status, status)
        entry = self.entries.get(owner)
        if entry is None:
            return f'entry {owner}'
        return 'you' if entry.is_mine else entry.entry_name

    def as_dict(self) -> dict:
        return {
            'season': self.season,
            'league_id': self.league_id,
            'league_name': self.league_name,
            'transaction_mode': self.transaction_mode,
            'fetched_at': self.fetched_at,
            'my_entry_id': self.my_entry.entry_id,
            'entries': [entry.as_dict() for entry in self.entries.values()],
            'claimable': sum(1 for element in self.owner_by_element if self.is_claimable(element)),
            'locked': sum(1 for status in self.status_by_element.values() if status == LOCKED),
            'unknown_statuses': self.unknown_statuses,
        }


def build_ownership(
    season: str,
    my_entry_id: int,
    details: dict,
    element_status: dict,
    fetched_at: str,
) -> LeagueOwnership:
    """Assemble ownership from the two raw payloads.

    Parameters:
    - details: body of `league/{id}/details`.
    - element_status: body of `league/{id}/element-status`.

    Raises:
    - ValueError: if our entry is not in the league, or any squad does not hold exactly fifteen
      players. Both mean the two payloads disagree with each other or with the config, and a
      waiver suggestion built on half a squad is worse than no suggestion.
    """
    league = details['league']
    entries: dict[int, DraftEntry] = {}
    for row in details['league_entries']:
        entry_id = row['entry_id']
        manager = ' '.join(part for part in (row.get('player_first_name'), row.get('player_last_name')) if part)
        entries[entry_id] = DraftEntry(
            entry_id=entry_id,
            entry_name=row.get('entry_name') or f'entry {entry_id}',
            manager=manager,
            is_mine=entry_id == my_entry_id,
            waiver_pick=row.get('waiver_pick'),
        )
    if my_entry_id not in entries:
        raise ValueError(
            f"Entry {my_entry_id} is not in league {league['id']} ('{league['name']}'). Draft "
            f"entry ids are re-issued every season - re-connect with "
            f"./run.sh -m src.fpl.league --entry <id>. Entries in this league: "
            f"{', '.join(f'{e.entry_id} ({e.entry_name})' for e in entries.values())}"
        )

    owner_by_element: dict[int, int | None] = {}
    status_by_element: dict[int, str] = {}
    unknown_statuses: dict[str, int] = {}
    for row in element_status['element_status']:
        element = row['element']
        status = row['status']
        owner_by_element[element] = row['owner']
        status_by_element[element] = status
        if status not in STATUS_MEANINGS:
            unknown_statuses[status] = unknown_statuses.get(status, 0) + 1

    held: dict[int, int] = {}
    for owner in owner_by_element.values():
        if owner is not None:
            held[owner] = held.get(owner, 0) + 1
    wrong = {
        entries[entry_id].entry_name if entry_id in entries else f'entry {entry_id}': count
        for entry_id, count in held.items()
        if count != SQUAD_SIZE
    }
    missing = [entry.entry_name for entry_id, entry in entries.items() if entry_id not in held]
    if wrong or missing:
        raise ValueError(
            f"League {league['id']} ownership does not add up: expected {SQUAD_SIZE} players per "
            f"entry. Wrong counts: {wrong or 'none'}. Entries holding nobody: "
            f"{missing or 'none'}. The two payloads were fetched at different times, or the "
            f"league is mid-draft."
        )
    if unknown_statuses:
        logger.warning(
            "element-status returned %d player(s) with status codes this module does not model: "
            "%s. They are treated as not claimable.",
            sum(unknown_statuses.values()), unknown_statuses,
        )

    return LeagueOwnership(
        season=season,
        league_id=league['id'],
        league_name=league['name'],
        transaction_mode=league.get('transaction_mode', 'unknown'),
        fetched_at=fetched_at,
        entries=entries,
        owner_by_element=owner_by_element,
        status_by_element=status_by_element,
        unknown_statuses=unknown_statuses,
    )


async def discover_leagues(client: AsyncClient, entry_id: int) -> tuple[dict, list[int]]:
    """Look up an entry and the draft leagues it plays in.

    Returns the `entry` payload and its league ids, so `./run.sh -m src.fpl.league --entry <id>`
    can name the team it found before writing any config - the cheapest possible guard against a
    mistyped id pointing at a stranger.
    """
    body = await fetch_json(client, f"entry/{entry_id}/public", base_url=BASE_DRAFT_URL)
    entry = body['entry']
    return entry, list(entry.get('league_set') or [])


async def fetch_league(
    client: AsyncClient,
    league_id: int,
    season: str | None = None,
    freshness: int = 0,
) -> tuple[dict, dict, str]:
    """Fetch and snapshot a league's details and ownership.

    `freshness=0` means always re-fetch, which is the right default here: ownership changes the
    moment a waiver clears, and a cached map is the one thing this module must not serve.

    Returns:
    - (details, element_status, fetched_at ISO timestamp).
    """
    season = season or Season.CURRENT
    directory = league_dir(season, league_id)
    details_store = JsonSnapshotStore(SnapshotSpec(base_path=os.path.join(directory, 'details')))
    status_store = JsonSnapshotStore(SnapshotSpec(base_path=os.path.join(directory, 'element_status')))

    details = await details_store.get_or_fetch(
        freshness,
        lambda: fetch_json(client, f"league/{league_id}/details", base_url=BASE_DRAFT_URL),
    )
    element_status = await status_store.get_or_fetch(
        freshness,
        lambda: fetch_json(client, f"league/{league_id}/element-status", base_url=BASE_DRAFT_URL),
    )
    latest = status_store.find_latest()
    fetched_at = latest[0].isoformat(timespec='seconds') if latest else datetime.now().isoformat(timespec='seconds')
    return details, element_status, fetched_at


def load_ownership(season: str | None = None) -> LeagueOwnership:
    """Read the stored ownership map for the connected league.

    Raises:
    - FileNotFoundError: when the league is not connected, or was connected but never fetched.
      Both messages name the command that fixes it.
    - ValueError: when the payloads disagree (see `build_ownership`).
    """
    season = season or Season.CURRENT
    config = load_config(season)
    directory = league_dir(season, config.league_id)
    details_store = JsonSnapshotStore(SnapshotSpec(base_path=os.path.join(directory, 'details')))
    status_store = JsonSnapshotStore(SnapshotSpec(base_path=os.path.join(directory, 'element_status')))
    latest = status_store.find_latest()
    if latest is None or details_store.find_latest() is None:
        raise FileNotFoundError(
            f"League {config.league_id} is connected but has never been fetched into "
            f"{directory}. Fetch it with: ./run.sh -m src.fpl.league --refresh"
        )
    return build_ownership(
        season=season,
        my_entry_id=config.entry_id,
        details=details_store.load_latest(),
        element_status=status_store.load_latest(),
        fetched_at=latest[0].isoformat(timespec='seconds'),
    )
