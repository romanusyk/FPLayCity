"""Which classic-FPL team is ours, and which fifteen players are in it.

Why this exists
---------------
The FPL board answers "who should I buy", and that question is only half answerable without
knowing who you already own. A squad of fifteen is not a filter you can guess at: it decides
which rows are transfer targets, which are already yours, and which of yours are sitting on the
bench doing nothing.

The identity trap, classic-FPL edition
--------------------------------------
`CLAUDE.md` says draft entry ids are re-issued every season and classic-FPL ids are permanent, so
a classic id may safely be hardcoded. The first half is true. The second half is true and
*irrelevant*, which this module exists to fix: **permanent is not the same as correct.** This repo
carried

    class FplManager(Enum):
        ME = 2486591

in `src/fpl/loader/load.py` from 2025-12-25. Checked against the live API on 2026-08-26, entry
2486591 is "Reddevil", managed by somebody who is not us. Every `PlayerPresences` row filed under
`is_mine=True` since December described a stranger's team, and nothing raised, because nothing ever
asked the API whose team it was. A permanent id is a *stable* wrong answer, which is worse than a
seasonal one: it never breaks, so it never gets noticed.

So the id lives in `data/fpl_entry.json`, written by `./run.sh -m src.fpl.squad --entry <id>`,
which fetches the entry first and prints the team and manager name it found. A mistyped digit then
shows up as somebody else's name in the terminal instead of as fifteen wrong players on a board.

Not season-scoped, unlike the draft
-----------------------------------
`data/<season>/draft_league.json` is under a season because draft ids are re-issued. Classic entry
ids genuinely are permanent, so one file serves every season. What *is* season-scoped is the picks
themselves: they are element ids, and those are reassigned every July, which is why they stay under
`data/<season>/fpl_managers/<entry>/picks/<gameweek>_<ts>.json`.

The publication lag is real, and is not a data gap
--------------------------------------------------
FPL publishes an entry's picks only *after* that gameweek's deadline. Before the GW2 deadline,
`entry/{id}/event/2/picks/` is a 404 and the freshest squad that exists anywhere is GW1's. So a
GW2-11 board is filtered by your GW1 squad, and any transfer you have already made for GW2 is
invisible to it. That is a property of the API, not of our snapshots, so `FplSquad` carries the
gameweek it describes and every screen that uses it says which one it is. Silently labelling last
week's fifteen "your squad" is the failure this docstring is here to prevent.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime

from httpx import AsyncClient

from src.fpl.loader.http import BASE_FPL_URL, fetch_json
from src.fpl.loader.store import JsonSnapshotStore, SnapshotSpec
from src.fpl.loader.utils import MAX_GAMEWEEK, Season, ensure_dir_exists
from src.fpl.models.immutable import PlayerType


logger = logging.getLogger(__name__)


SQUAD_SIZE = 15
XI_SLOTS = 11
"""Slots 1-11 are the eleven that score; 12-15 are the bench, in the order they come on."""

SQUAD_SHAPE = {'GKP': 2, 'DEF': 5, 'MID': 5, 'FWD': 3}
"""A classic-FPL squad, always. Same shape as a draft squad, for unrelated reasons."""


def config_path() -> str:
    """Where the entry id lives. Deliberately not under `data/<season>/` - see the module doc."""
    return os.path.join('data', 'fpl_entry.json')


def picks_store(season: str, entry_id: int, gameweek: int) -> JsonSnapshotStore:
    """The snapshot store for one entry's picks in one gameweek.

    The single definition of that path. `load()` in `src/fpl/loader/load.py` writes through this
    and `load_squad` reads through it, so a fetcher and a reader cannot disagree about where a
    squad is filed.
    """
    return JsonSnapshotStore(SnapshotSpec(
        base_path=os.path.join('data', season, 'fpl_managers', str(entry_id), 'picks', str(gameweek))
    ))


@dataclass(frozen=True)
class FplEntryConfig:
    """Our classic-FPL entry: the id, plus the names the API gave for it when we connected.

    The names are stored so a wrong id is legible later. `./run.sh -m src.fpl.squad --show` prints
    them, and "that is not my team" is a much easier thing to notice than a bare number.
    """

    entry_id: int
    entry_name: str
    manager: str
    connected_at: str

    def as_dict(self) -> dict:
        return {
            'entry_id': self.entry_id,
            'entry_name': self.entry_name,
            'manager': self.manager,
            'connected_at': self.connected_at,
        }


def save_config(config: FplEntryConfig) -> str:
    path = config_path()
    ensure_dir_exists(path)
    with open(path, 'w', encoding='utf-8') as handle:
        json.dump(config.as_dict(), handle, indent=2)
    logger.info(
        "Classic-FPL team saved: entry %d '%s' (%s)",
        config.entry_id, config.entry_name, config.manager,
    )
    return path


def load_config() -> FplEntryConfig:
    """Read the stored entry.

    Raises:
    - FileNotFoundError: when no team has been connected, carrying the command that connects one.
      There is no sensible default: any other entry id is somebody else's squad.
    - ValueError: when the file exists but cannot be read as a config.
    """
    path = config_path()
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"No classic-FPL team connected ({path} does not exist). Connect yours with: "
            f"./run.sh -m src.fpl.squad --entry <your entry id>. Your entry id is the number in "
            f"the URL of your team page on fantasy.premierleague.com "
            f"(fantasy.premierleague.com/entry/<id>/event/<gw>)."
        )
    try:
        with open(path, encoding='utf-8') as handle:
            body = json.load(handle)
        return FplEntryConfig(
            entry_id=int(body['entry_id']),
            entry_name=body['entry_name'],
            manager=body['manager'],
            connected_at=body['connected_at'],
        )
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"{path} is not a readable classic-FPL entry config: {exc}") from exc


@dataclass(frozen=True)
class SquadPick:
    """One of the fifteen, in the slot the manager put him in."""

    element: int
    position: str
    slot: int
    is_captain: bool
    is_vice_captain: bool
    multiplier: int

    @property
    def is_bench(self) -> bool:
        return self.slot > XI_SLOTS

    def as_dict(self) -> dict:
        return {
            'element': self.element,
            'position': self.position,
            'slot': self.slot,
            'is_captain': self.is_captain,
            'is_vice_captain': self.is_vice_captain,
            'multiplier': self.multiplier,
            'is_bench': self.is_bench,
        }


@dataclass(frozen=True)
class FplSquad:
    """Our fifteen, as they stood at the end of one gameweek.

    `gameweek` is load-bearing rather than decoration: FPL publishes picks only after a deadline,
    so this is always a *past* squad, and a board projecting later gameweeks must say so.
    """

    entry_id: int
    entry_name: str
    manager: str
    season: str
    gameweek: int
    captured_at: str
    active_chip: str | None
    picks: tuple[SquadPick, ...]
    bank: float | None = None
    """Money not spent, in millions. `None` when the payload carried no `entry_history`.

    Load-bearing for transfer valuation: with an empty bank every upgrade must be funded by
    selling, which is a far tighter constraint than the board's raw ranking suggests.
    """
    squad_value: float | None = None
    """Selling value of the fifteen, in millions. Together with `bank` this is the budget."""

    @property
    def elements(self) -> set[int]:
        return {pick.element for pick in self.picks}

    def pick(self, element: int) -> SquadPick | None:
        """The slot this element occupies in our squad, or None when we do not own him."""
        for candidate in self.picks:
            if candidate.element == element:
                return candidate
        return None

    def as_dict(self) -> dict:
        return {
            'entry_id': self.entry_id,
            'entry_name': self.entry_name,
            'manager': self.manager,
            'season': self.season,
            'gameweek': self.gameweek,
            'captured_at': self.captured_at,
            'active_chip': self.active_chip,
            'bank': self.bank,
            'squad_value': self.squad_value,
            'picks': [pick.as_dict() for pick in self.picks],
        }


async def fetch_entry(client: AsyncClient, entry_id: int) -> dict:
    """Read one entry's public summary. Raises through `fetch_json` on any non-2xx."""
    return await fetch_json(client, f"entry/{entry_id}/", base_url=BASE_FPL_URL)


async def fetch_picks(
    client: AsyncClient, entry_id: int, gameweek: int, season: str | None = None,
    freshness: float = 1,
) -> dict:
    """Fetch (or reuse) one gameweek's picks and persist the snapshot.

    Raises:
    - HTTPStatusError through `fetch_json`: a 404 here means the gameweek's deadline has not
      passed yet, so those picks do not exist publicly. Callers that walk a range of gameweeks
      should stop at the last *started* one rather than swallowing it.
    """
    season = season or Season.CURRENT
    store = picks_store(season, entry_id, gameweek)
    return await store.get_or_fetch(
        freshness,
        lambda: fetch_json(client, f"entry/{entry_id}/event/{gameweek}/picks/", base_url=BASE_FPL_URL),
    )


def stored_gameweeks(season: str, entry_id: int) -> list[int]:
    """Every gameweek we hold picks for, ascending."""
    return [
        gameweek for gameweek in range(1, MAX_GAMEWEEK + 1)
        if picks_store(season, entry_id, gameweek).find_latest() is not None
    ]


def load_squad(
    season: str | None = None, gameweek: int | None = None,
    config: FplEntryConfig | None = None,
) -> FplSquad:
    """Read our squad from the newest stored picks, offline.

    Parameters:
    - gameweek: which gameweek's squad. Defaults to the latest one on disk, which is the freshest
      that exists at all - FPL does not publish the upcoming one.

    Raises:
    - FileNotFoundError: when no team is connected, or nothing has been fetched for it yet. Both
      messages name the command that fixes them.
    - ValueError: when the payload is not a fifteen-player 2/5/5/3 squad with slots 1-15. Every
      one of those would otherwise show up as a quietly wrong filter rather than an error.
    """
    season = season or Season.CURRENT
    config = config or load_config()
    if gameweek is None:
        available = stored_gameweeks(season, config.entry_id)
        if not available:
            raise FileNotFoundError(
                f"No stored picks for entry {config.entry_id} ('{config.entry_name}') in {season}. "
                f"Fetch them with: ./run.sh -m src.fpl.squad --refresh. Before GW1's deadline "
                f"there is nothing to fetch - FPL publishes a squad only once its gameweek has "
                f"started."
            )
        gameweek = available[-1]

    latest = picks_store(season, config.entry_id, gameweek).find_latest()
    if latest is None:
        raise FileNotFoundError(
            f"No stored picks for entry {config.entry_id} in {season} GW{gameweek}. Stored "
            f"gameweeks: {stored_gameweeks(season, config.entry_id) or 'none'}. Fetch with: "
            f"./run.sh -m src.fpl.squad --refresh"
        )
    captured_at, path = latest
    with open(path, encoding='utf-8') as handle:
        body = json.load(handle)

    picks = _read_picks(body, path)
    bank, squad_value = _read_money(body)
    return FplSquad(
        entry_id=config.entry_id,
        entry_name=config.entry_name,
        manager=config.manager,
        season=season,
        gameweek=gameweek,
        captured_at=captured_at.isoformat(timespec='seconds'),
        active_chip=body.get('active_chip'),
        picks=picks,
        bank=bank,
        squad_value=squad_value,
    )


def _read_money(body: dict) -> tuple[float | None, float | None]:
    """Bank and squad value in millions, or `(None, None)` when the payload omits them.

    FPL stores both as tenths of a million. Missing `entry_history` is not raised on: it is absent
    from some historical payloads, and a squad still filters a board without it. What must not
    happen is a *zero* standing in for an unknown bank, because zero is itself a meaningful budget
    and the tightest one - a transfer screen would silently rule out every upgrade.
    """
    history = body.get('entry_history')
    if not isinstance(history, dict):
        return None, None
    def _millions(key: str) -> float | None:
        raw = history.get(key)
        return round(int(raw) / 10, 1) if isinstance(raw, int) else None
    return _millions('bank'), _millions('value')


def _read_picks(body: dict, path: str) -> tuple[SquadPick, ...]:
    """Convert a picks payload, checking it is a legal squad.

    Raises:
    - ValueError: on a missing field, the wrong number of players, duplicate or non-consecutive
      slots, or a shape that is not 2/5/5/3. A squad filter built on fourteen players would look
      exactly like a correct one.
    """
    rows = body.get('picks')
    if not isinstance(rows, list):
        raise ValueError(f"{path} has no 'picks' list; this is not an entry-picks payload.")
    picks = []
    for row in rows:
        try:
            picks.append(SquadPick(
                element=int(row['element']),
                position=PlayerType(int(row['element_type'])).name,
                slot=int(row['position']),
                is_captain=bool(row['is_captain']),
                is_vice_captain=bool(row['is_vice_captain']),
                multiplier=int(row['multiplier']),
            ))
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"{path} has an unreadable pick {row!r}: {exc}") from exc

    if len(picks) != SQUAD_SIZE:
        raise ValueError(
            f"{path} holds {len(picks)} players, not {SQUAD_SIZE}. A short squad would filter the "
            f"board as if you did not own the missing player."
        )
    slots = sorted(pick.slot for pick in picks)
    if slots != list(range(1, SQUAD_SIZE + 1)):
        raise ValueError(f"{path} has slots {slots}, expected 1-{SQUAD_SIZE} exactly once each.")
    if len({pick.element for pick in picks}) != SQUAD_SIZE:
        raise ValueError(f"{path} names the same player twice.")
    shape = {position: 0 for position in SQUAD_SHAPE}
    for pick in picks:
        shape[pick.position] += 1
    if shape != SQUAD_SHAPE:
        raise ValueError(f"{path} is a {shape} squad, expected {SQUAD_SHAPE}.")
    return tuple(sorted(picks, key=lambda pick: pick.slot))


def load_config_or_none() -> FplEntryConfig | None:
    """The connected entry, or None when there is not one.

    A missing config is a normal state - it is what a fresh checkout looks like - and callers that
    only want to *offer* the squad filter should not have to catch. A *broken* config still raises:
    treating a typo as "no team connected" would hide it.
    """
    try:
        return load_config()
    except FileNotFoundError:
        return None
