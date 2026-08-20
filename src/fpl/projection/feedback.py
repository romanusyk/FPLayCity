"""Your disagreements with a projection, stored as labelled data.

Why this is not just a comments box
-----------------------------------
Feedback has two jobs and the second is the interesting one.

1. I read it and change the models.
2. When the gameweek resolves, it becomes a scoreboard. Every entry names a run, a player and
   what you thought the model got wrong - and often your own `p_start`. Once the actual result
   lands, your override and the model's number can be scored against it. If your judgement
   beats the model at spotting rotation, that is a measurable finding and a thing to encode.
   If the model beats you, that is worth knowing too.

That is why an entry records the run id and the model's value at the time, not just the note.
An opinion without the number it disagreed with cannot be scored later.

Storage
-------
`data/<season>/feedback/gw<NN>/<timestamp>_<player_id>.json`, one file per entry, append-only.
Entries are never edited: two contradictory opinions a week apart are data about how your view
changed, not a conflict to resolve.
"""
from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime

from src.fpl.loader.utils import Season, ensure_dir_exists


logger = logging.getLogger(__name__)


REASONS = (
    'will_not_start',
    'nailed_starter',
    'role_changed',
    'injury_doubt',
    'fixture_wrong',
    'rate_wrong',
    'just_wrong',
    'looks_right',
)
"""Closed vocabulary. Free text is also stored, but a code is what makes entries countable.

`looks_right` is deliberately in the list. Agreement is as much a label as disagreement, and a
feedback set containing only complaints tells you nothing about the cases the model got right.
"""

SAFE_ID = re.compile(r'^[A-Za-z0-9._-]+$')


@dataclass
class FeedbackEntry:
    """One judgement about one player in one run."""

    season: str
    game: str
    run_id: str
    gameweek: int
    player_id: int
    web_name: str
    reason: str
    note: str = ''
    your_p_start: float | None = None
    your_points: float | None = None
    model_p_start: float | None = None
    model_points: float | None = None
    created_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec='seconds'))

    def validate(self) -> None:
        """Check the entry before it is written.

        Raises:
        - ValueError: on an unknown reason code, an out-of-range probability, or an unsafe run
          id. Storing a malformed entry would leave a hole in the eventual scoreboard.
        """
        if self.reason not in REASONS:
            raise ValueError(f"Unknown reason '{self.reason}'. Known: {', '.join(REASONS)}")
        if self.your_p_start is not None and not 0.0 <= self.your_p_start <= 1.0:
            raise ValueError(f"your_p_start must be in [0, 1], got {self.your_p_start}")
        if not SAFE_ID.match(self.run_id):
            raise ValueError(f"Unsafe run id '{self.run_id}'")
        if self.gameweek < 1:
            raise ValueError(f"gameweek must be >= 1, got {self.gameweek}")

    def as_dict(self) -> dict:
        return asdict(self)


def feedback_dir(season: str, gameweek: int) -> str:
    return os.path.join('data', season, 'feedback', f'gw{gameweek:02d}')


def save_feedback(entry: FeedbackEntry) -> str:
    """Validate and write one entry.

    Returns:
    - The path written.
    """
    entry.validate()
    stamp = entry.created_at.replace(':', '-')
    path = os.path.join(
        feedback_dir(entry.season, entry.gameweek), f'{stamp}_{entry.player_id}.json'
    )
    ensure_dir_exists(path)
    with open(path, 'w', encoding='utf-8') as handle:
        json.dump(entry.as_dict(), handle, indent=2)
    logger.info("Saved feedback: %s on %s (%s)", entry.reason, entry.web_name, entry.run_id)
    return path


def load_feedback(season: str | None = None, gameweek: int | None = None) -> list[FeedbackEntry]:
    """Read stored entries, newest first.

    Raises:
    - ValueError: if a stored file cannot be read as an entry. A corrupt file must surface
      rather than shrink the sample silently.
    """
    season = season or Season.CURRENT
    base = os.path.join('data', season, 'feedback')
    if not os.path.isdir(base):
        return []

    directories = (
        [feedback_dir(season, gameweek)] if gameweek is not None
        else [os.path.join(base, name) for name in sorted(os.listdir(base))]
    )
    entries: list[FeedbackEntry] = []
    for directory in directories:
        if not os.path.isdir(directory):
            continue
        for filename in sorted(os.listdir(directory)):
            if not filename.endswith('.json'):
                continue
            path = os.path.join(directory, filename)
            try:
                with open(path, encoding='utf-8') as handle:
                    entries.append(FeedbackEntry(**json.load(handle)))
            except (json.JSONDecodeError, TypeError) as exc:
                raise ValueError(f"{path} is not a readable feedback entry: {exc}") from exc
    entries.sort(key=lambda entry: entry.created_at, reverse=True)
    return entries


def runs_with_feedback(season: str | None = None) -> set[str]:
    """Run ids some stored entry refers to. These are never pruned."""
    return {entry.run_id for entry in load_feedback(season)}
