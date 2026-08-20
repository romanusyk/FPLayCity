"""Per-match history for a player, joined across seasons.

Why this module exists
----------------------
Season totals tell you a player averaged 10.2 defensive actions. They cannot tell you whether
that was ten quiet matches or five blanks and five hauls, and for a step-function award like
defensive contribution the difference is worth points. The distribution lives in the
per-gameweek rows of `data/<season>/elements/*.json`, and this module is the only place that
reads them.

The cross-season join
---------------------
Element `id` is reassigned every season, so last season's `elements/379_*.json` is a different
player this season. Element `code` is stable for the lifetime of a player, so the join goes
`current id -> code -> prior id`. Anything that compares raw element ids across seasons is a
bug; see `CLAUDE.md`.

What is *not* here
------------------
Coverage is whatever the stored snapshots contain. The 2025/26 snapshot was taken in March
2026, so it holds gameweeks 1-30, not 1-38. `PlayerHistory.coverage` reports that per season
rather than letting a caller assume a full season, and every model that consumes history
records its sample size in the run artifact.

Components
----------
- `MatchRow`: one player-match, position-aware and already scored.
- `PlayerHistory`: every stored match for one current-season player, newest last.
- `build_player_histories`: read the snapshots and do the join.

There is deliberately no derived snapshot here. Building the full join reads ~1,400 files in
under half a second, so caching it would add a staleness failure mode and buy nothing.
"""
from __future__ import annotations

import glob
import json
import logging
import os
from collections import Counter
from dataclasses import dataclass, field

from src.fpl.loader.store import JsonSnapshotStore, SnapshotSpec
from src.fpl.loader.utils import Season
from src.fpl.models.immutable import PlayerType
from src.fpl.projection.scoring import clears_defensive_threshold, score_player_match


logger = logging.getLogger(__name__)


HISTORY_FIELDS = (
    'minutes', 'goals_scored', 'assists', 'clean_sheets', 'goals_conceded', 'saves',
    'defensive_contribution', 'bonus', 'bps', 'yellow_cards', 'red_cards',
    'penalties_saved', 'penalties_missed', 'own_goals', 'starts', 'total_points',
)
"""Integer fields lifted verbatim from an FPL history row."""

HISTORY_FLOAT_FIELDS = (
    'expected_goals', 'expected_assists', 'expected_goal_involvements', 'expected_goals_conceded',
)


@dataclass(frozen=True)
class MatchRow:
    """One player-match, as stored by the FPL API plus what we derive from it."""

    season: str
    gameweek: int
    fixture_id: int
    opponent: str
    """Opponent club `short_name`. Resolved at build time because `opponent_team` in the raw
    payload is an element-season team id, and those are reassigned every year."""
    was_home: bool
    kickoff_time: str

    minutes: int
    starts: int
    total_points: int
    goals_scored: int
    assists: int
    clean_sheets: int
    goals_conceded: int
    saves: int
    defensive_contribution: int
    bonus: int
    bps: int
    yellow_cards: int
    red_cards: int
    penalties_saved: int
    penalties_missed: int
    own_goals: int
    expected_goals: float
    expected_assists: float
    expected_goal_involvements: float
    expected_goals_conceded: float

    @property
    def started(self) -> bool:
        return bool(self.starts)

    @property
    def played(self) -> bool:
        return self.minutes > 0

    def cleared_defensive_threshold(self, position: PlayerType) -> bool:
        """True when this match earned the +2 defensive-contribution award."""
        return clears_defensive_threshold(position, self.defensive_contribution)

    def score(self, position: PlayerType):
        """Re-score this match from its raw fields. Used to audit the scoring function."""
        return score_player_match(
            position=position,
            minutes=self.minutes,
            goals_scored=self.goals_scored,
            assists=self.assists,
            clean_sheet=bool(self.clean_sheets),
            goals_conceded=self.goals_conceded,
            saves=self.saves,
            defensive_contribution=self.defensive_contribution,
            bonus=self.bonus,
            yellow_cards=self.yellow_cards,
            red_cards=self.red_cards,
            penalties_saved=self.penalties_saved,
            penalties_missed=self.penalties_missed,
            own_goals=self.own_goals,
        )


@dataclass
class PlayerHistory:
    """Every stored per-match row for one player, keyed by their *current* element id."""

    player_id: int
    code: int
    matches: list[MatchRow] = field(default_factory=list)

    @property
    def coverage(self) -> dict[str, tuple[int, int]]:
        """Season -> (first gameweek, last gameweek) actually stored.

        Read this before trusting a count. A season present here with `(1, 30)` is a
        part-season, and treating it as 38 gameweeks would understate every per-season rate.
        """
        by_season: dict[str, list[int]] = {}
        for match in self.matches:
            by_season.setdefault(match.season, []).append(match.gameweek)
        return {season: (min(gws), max(gws)) for season, gws in by_season.items()}

    def in_season(self, season: str) -> list[MatchRow]:
        return [match for match in self.matches if match.season == season]

    def starts(self, season: str | None = None) -> list[MatchRow]:
        """Matches the player started, optionally restricted to one season."""
        return [
            match for match in self.matches
            if match.started and (season is None or match.season == season)
        ]

    def appearances(self, season: str | None = None) -> list[MatchRow]:
        return [
            match for match in self.matches
            if match.played and (season is None or match.season == season)
        ]


def _snapshot_paths(season: str) -> list[str]:
    """Return every stored per-element snapshot for a season, one per element id.

    `data/<season>/elements/` holds `<element_id>_<timestamp>.json` files plus an aggregate
    `elements_<timestamp>.json` one directory up, which is not read here.
    """
    return sorted(glob.glob(os.path.join('data', season, 'elements', '*_*.json')))


def _latest_by_element(paths: list[str]) -> dict[int, str]:
    """Keep only the newest snapshot per element id.

    `JsonSnapshotStore` deletes superseded snapshots, so in practice there is one file per
    element. Being explicit here means a stray older file cannot silently double-count.
    """
    latest: dict[int, str] = {}
    for path in paths:
        basename = os.path.basename(path)
        element_id = int(basename.split('_', 1)[0])
        if element_id not in latest or path > latest[element_id]:
            latest[element_id] = path
    return latest


def _bootstrap(season: str) -> dict:
    """Load a season's bootstrap snapshot.

    Raises:
    - FileNotFoundError: if the season has no stored bootstrap. Without it the cross-season
      join is impossible and a silently empty history would look like "the player never played".
    """
    store = JsonSnapshotStore(SnapshotSpec(base_path=f"data/{season}/bootstrap"))
    if store.find_latest() is None:
        raise FileNotFoundError(
            f"No bootstrap snapshot under data/{season}/bootstrap. Per-match history for "
            f"{season} cannot be joined to the current season without element codes. "
            f"Fetch it with: uv run -m src.fpl.fetch --season {season}"
        )
    return store.load_latest()


def _codes_by_element_id(body: dict) -> dict[int, int]:
    """Map element id to the season-stable element `code`."""
    return {element['id']: element['code'] for element in body['elements']}


def _short_names_by_team_id(body: dict) -> dict[int, str]:
    """Map that season's team ids to club `short_name`s."""
    return {team['id']: team['short_name'] for team in body['teams']}


def _row_from_history(season: str, row: dict, short_names: dict[int, str]) -> MatchRow:
    """Build a `MatchRow` from one raw FPL history entry.

    Raises:
    - KeyError: if the payload is missing a field we score with. A changed API shape must stop
      the build rather than produce rows with zeros in them.
    """
    missing = [
        name for name in HISTORY_FIELDS + HISTORY_FLOAT_FIELDS + ('round', 'fixture', 'opponent_team')
        if name not in row
    ]
    if missing:
        raise KeyError(
            f"FPL history row for element {row.get('element')} in {season} is missing "
            f"{missing}. The API response shape has changed; update HISTORY_FIELDS in "
            f"src/fpl/projection/history.py."
        )
    if row['opponent_team'] not in short_names:
        raise KeyError(
            f"Element {row.get('element')} played {season} GW{row['round']} against team "
            f"{row['opponent_team']}, which is not in that season's bootstrap. The snapshots "
            f"are inconsistent."
        )
    return MatchRow(
        season=season,
        gameweek=row['round'],
        fixture_id=row['fixture'],
        opponent=short_names[row['opponent_team']],
        was_home=bool(row['was_home']),
        kickoff_time=row['kickoff_time'],
        **{name: int(row[name]) for name in HISTORY_FIELDS},
        **{name: float(row[name]) for name in HISTORY_FLOAT_FIELDS},
    )


def build_player_histories(
    season: str | None = None,
    seasons_back: int = 1,
) -> dict[int, PlayerHistory]:
    """Join stored per-match rows from `season` and its predecessors onto current element ids.

    Parameters:
    - season: the season being projected. Defaults to `Season.CURRENT`.
    - seasons_back: how many earlier seasons to pull in. 1 means "last season too".

    Returns:
    - Current element id -> `PlayerHistory`. Players with no stored matches in any season are
      present with an empty `matches` list, so a caller can tell "no evidence" apart from
      "not in the league".

    Raises:
    - FileNotFoundError: if a season being read has no bootstrap snapshot to join `code` on.
    """
    season = season or Season.CURRENT
    bootstraps = {season: _bootstrap(season)}
    current_codes = _codes_by_element_id(bootstraps[season])
    histories = {
        element_id: PlayerHistory(player_id=element_id, code=code)
        for element_id, code in current_codes.items()
    }
    by_code = {history.code: history for history in histories.values()}

    seasons = [season]
    cursor = season
    for _ in range(seasons_back):
        cursor = Season.previous(cursor)
        seasons.append(cursor)

    unmatched = Counter()
    for read_season in seasons:
        paths = _latest_by_element(_snapshot_paths(read_season))
        if not paths:
            logger.info("No per-element snapshots under data/%s/elements - skipping.", read_season)
            continue
        body = bootstraps.setdefault(read_season, _bootstrap(read_season))
        codes = _codes_by_element_id(body)
        short_names = _short_names_by_team_id(body)
        added = 0
        for element_id, path in paths.items():
            code = codes.get(element_id)
            if code is None:
                raise KeyError(
                    f"Element {element_id} has a snapshot at {path} but no entry in the "
                    f"{read_season} bootstrap. The snapshots are inconsistent; re-fetch "
                    f"{read_season} before projecting."
                )
            history = by_code.get(code)
            if history is None:
                # Left the Premier League, or never joined it. Not an error: their matches
                # simply have nobody to attach to in the season being projected.
                unmatched[read_season] += 1
                continue
            with open(path, encoding='utf-8') as handle:
                payload = json.load(handle)
            for row in payload.get('history', []):
                history.matches.append(_row_from_history(read_season, row, short_names))
                added += 1
        logger.info(
            "Loaded %d %s player-matches from %d elements (%d elements not in %s)",
            added, read_season, len(paths), unmatched[read_season], season,
        )

    for history in histories.values():
        history.matches.sort(key=lambda match: (match.season, match.gameweek))

    with_history = sum(1 for history in histories.values() if history.matches)
    logger.info(
        "Per-match history: %d/%d %s players carry at least one stored match",
        with_history, len(histories), season,
    )
    return histories
