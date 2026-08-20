"""Who has already been taken, during a live draft.

This is the one piece of mutable state in the app. Runs are immutable and feedback is
append-only; draft state is a scratchpad you edit as the picks come in, and it is deliberately
kept out of the run artifact so that marking a player taken never changes a projection.

What it buys you is a moving replacement level. Before the draft, replacement forward is
whoever will be the thirteenth-best forward left. Four picks later the pool is shallower and
every remaining forward is worth more. Recomputing VORP against the live pool is the whole
point of tracking this at all - a static board is wrong from the second pick onward.

Storage: `data/<season>/draft_state.json`, one file, overwritten in place. Losing it costs you
a draft's worth of clicks, not any derived data.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field

from src.fpl.loader.utils import Season, ensure_dir_exists


logger = logging.getLogger(__name__)


MINE = 'mine'
OTHER = 'other'
OWNERS = (MINE, OTHER)


def state_path(season: str) -> str:
    return os.path.join('data', season, 'draft_state.json')


@dataclass
class DraftState:
    """Players already off the board, split by whether they are yours.

    Keys are player ids as ints in memory; JSON turns them into strings on the way out and
    `load` turns them back, because a silently string-keyed dict would fail every id lookup.
    """

    season: str
    managers: int = 4
    taken: dict[int, str] = field(default_factory=dict)

    @property
    def my_players(self) -> set[int]:
        return {player_id for player_id, owner in self.taken.items() if owner == MINE}

    @property
    def all_taken(self) -> set[int]:
        return set(self.taken)

    def set_owner(self, player_id: int, owner: str | None) -> None:
        """Mark a player as taken by you, taken by someone else, or available again.

        Raises:
        - ValueError: on an unknown owner, so a typo cannot silently create a third category
          that the VORP recomputation would ignore.
        """
        if owner is None:
            self.taken.pop(player_id, None)
            return
        if owner not in OWNERS:
            raise ValueError(f"Unknown owner '{owner}'. Expected one of {OWNERS} or null.")
        self.taken[player_id] = owner

    def as_dict(self) -> dict:
        return {
            'season': self.season,
            'managers': self.managers,
            'taken': {str(player_id): owner for player_id, owner in self.taken.items()},
        }


def load(season: str | None = None) -> DraftState:
    """Read the stored state, or an empty one if there is none.

    Raises:
    - ValueError: if the file exists but cannot be parsed. Silently starting from empty would
      wipe a draft in progress.
    """
    season = season or Season.CURRENT
    path = state_path(season)
    if not os.path.exists(path):
        return DraftState(season=season)
    try:
        with open(path, encoding='utf-8') as handle:
            body = json.load(handle)
        return DraftState(
            season=body['season'],
            managers=body.get('managers', 4),
            taken={int(player_id): owner for player_id, owner in body['taken'].items()},
        )
    except (json.JSONDecodeError, KeyError, ValueError) as exc:
        raise ValueError(
            f"{path} exists but is not a readable draft state: {exc}. Fix or delete it - "
            f"starting from empty would silently discard a draft in progress."
        ) from exc


def save(state: DraftState) -> str:
    """Write the state, replacing whatever was there."""
    path = state_path(state.season)
    ensure_dir_exists(path)
    with open(path, 'w', encoding='utf-8') as handle:
        json.dump(state.as_dict(), handle, indent=2)
    logger.info("Draft state saved: %d taken (%d mine)", len(state.taken), len(state.my_players))
    return path
