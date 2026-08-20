"""Prior-season baseline: keep last season's numbers when a new season starts empty.

Why this exists
---------------
On the first day of a new season every per-gameweek collection is empty, so any model that
only reads the current season has nothing to work with. The FPL API hands us a way out: until
the new season kicks off, `bootstrap-static` still carries each element's **previous**-season
totals, already attached to the player's **new** club. Captured before GW1, that is a complete
baseline for the season ahead.

The trap
--------
Bootstrap is only trustworthy for players who stayed at the same club. For anyone who moved it
either zeroes `minutes`/`total_points` outright or truncates them, while leaving a stale
`defensive_contribution` behind. Read naively, Jaidon Anthony's 2,717-minute season at Burnley
reads as "never played". `element-summary/{id}/history_past` keeps the real totals, so it is
authoritative and bootstrap is used only to cross-check - a disagreement for a player who did
*not* change club is an error and stops the load.

Components
----------
- `build_prior_season_baseline`: populate the `PlayerSeasons` collection from raw payloads.
- `persist_prior_season_baseline`: write a compact derived snapshot for later reuse.
- `load_prior_season_baseline`: repopulate `PlayerSeasons` from that snapshot, no HTTP needed.
- `BaselineReport`: what was recovered, skipped, and why.
"""
from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass, field

from src.fpl.loader.convert import (
    json_to_player_season,
    player_season_to_json,
    prior_season_to_player_season,
)
from src.fpl.loader.store import JsonSnapshotStore, SnapshotSpec
from src.fpl.loader.utils import Season
from src.fpl.models.immutable import PlayerSeasons, PriorSeasonSource


logger = logging.getLogger(__name__)


def baseline_store(season: str, prior_season: str) -> JsonSnapshotStore:
    """Snapshot store holding `prior_season` totals derived while loading `season`."""
    return JsonSnapshotStore(SnapshotSpec(base_path=f"data/{season}/prior_season/{prior_season}"))


@dataclass
class BaselineReport:
    """Outcome of a baseline build, for logging and eyeballing after a load."""

    season: str
    prior_season: str
    total_elements: int
    by_source: Counter = field(default_factory=Counter)
    without_prior_season: list[str] = field(default_factory=list)
    transferred: list[str] = field(default_factory=list)

    @property
    def loaded(self) -> int:
        return sum(self.by_source.values())

    def log(self) -> None:
        """Emit a human-readable summary at INFO level."""
        logger.info(
            "Prior-season baseline %s -> %s: %d/%d elements carry %s totals",
            self.prior_season, self.season, self.loaded, self.total_elements, self.prior_season,
        )
        for source in PriorSeasonSource:
            count = self.by_source.get(source, 0)
            if count:
                logger.info("  %-24s %d", source.value, count)
        repaired = (self.by_source.get(PriorSeasonSource.RECOVERED_FROM_HISTORY, 0)
                    + self.by_source.get(PriorSeasonSource.PARTIAL_IN_BOOTSTRAP, 0))
        if repaired:
            logger.info("  ^ %d player(s) would carry wrong or zero totals from bootstrap alone", repaired)
        logger.info("  changed club since %s: %d", self.prior_season, len(self.transferred))
        logger.info("  no %s Premier League record: %d", self.prior_season, len(self.without_prior_season))


def _prior_club_by_code(prior_season: str) -> dict[int, str]:
    """Map element `code` to club `short_name` using the prior season's bootstrap snapshot.

    Two identifiers matter here and neither is the obvious one:
    - `code` is the only *player* id stable across seasons; element `id` is reassigned yearly.
    - `short_name` is the only *club* id stable across seasons; FPL renumbers teams
      alphabetically, so 16 of 20 team ids changed meaning between 2025/26 and 2026/27.

    Returns an empty mapping when no snapshot for `prior_season` is stored locally, which only
    happens before that season has ever been loaded. In that case `PlayerSeason.prior_team`
    stays None and `PlayerSeason.is_new_club` reports False for everyone.
    """
    store = JsonSnapshotStore(SnapshotSpec(base_path=f"data/{prior_season}/bootstrap"))
    if store.find_latest() is None:
        logger.warning(
            "No bootstrap snapshot for %s under data/%s/bootstrap - transfers into the new season "
            "cannot be detected and prior_team will be None.", prior_season, prior_season,
        )
        return {}
    body = store.load_latest()
    short_names = {team["id"]: team["short_name"] for team in body["teams"]}
    return {element["code"]: short_names[element["team"]] for element in body["elements"]}


def build_prior_season_baseline(
    element_rows: list[dict],
    player_summaries: dict[str, dict],
    team_rows: list[dict],
    season: str | None = None,
) -> BaselineReport:
    """Populate `PlayerSeasons` with the season preceding `season`.

    Parameters:
    - element_rows: `elements` from the current season's bootstrap payload.
    - player_summaries: element id (as str) -> element-summary payload, as returned by
      `fetch_player_summaries`. Every element must be present.
    - team_rows: `teams` from the current season's bootstrap payload, used to resolve each
      element's club `short_name`.
    - season: Season being loaded. Defaults to `Season.CURRENT`.

    Returns:
    - A `BaselineReport` describing what was loaded.

    Raises:
    - KeyError: if an element has no summary payload. We refuse to build a partial baseline.
    - ValueError: propagated from `prior_season_to_player_season` when bootstrap and
      `history_past` disagree.
    """
    season = season or Season.CURRENT
    prior_season = Season.previous(season)
    fpl_season_name = Season.as_fpl_history_name(prior_season)
    prior_club_by_code = _prior_club_by_code(prior_season)
    short_names = {team['id']: team['short_name'] for team in team_rows}

    report = BaselineReport(season=season, prior_season=prior_season, total_elements=len(element_rows))

    for element_row in element_rows:
        element_id = str(element_row["id"])
        if element_id not in player_summaries:
            raise KeyError(
                f"No element-summary payload for player {element_id} ({element_row['web_name']}). "
                f"Refusing to build a partial {prior_season} baseline."
            )
        summary = player_summaries[element_id]
        if "history_past" not in summary:
            raise KeyError(
                f"element-summary payload for player {element_id} ({element_row['web_name']}) has no "
                f"'history_past' key. The FPL response shape has changed."
            )

        player_season = prior_season_to_player_season(
            element_row=element_row,
            history_past_rows=summary["history_past"],
            season=prior_season,
            fpl_season_name=fpl_season_name,
            team=short_names[element_row["team"]],
            prior_team=prior_club_by_code.get(element_row["code"]),
        )
        if player_season is None:
            report.without_prior_season.append(element_row["web_name"])
            continue

        PlayerSeasons.add(player_season)
        report.by_source[player_season.source] += 1
        if player_season.is_new_club:
            report.transferred.append(element_row["web_name"])

    return report


def persist_prior_season_baseline(season: str | None = None) -> str:
    """Write the in-memory `PlayerSeasons` rows for the prior season to a snapshot.

    The derived snapshot is small and self-contained, so later runs can restore the baseline
    without refetching ~600 element summaries.

    Returns:
    - Path of the snapshot written.

    Raises:
    - ValueError: if the baseline has not been built yet.
    """
    season = season or Season.CURRENT
    prior_season = Season.previous(season)
    rows = PlayerSeasons.get_list(season=prior_season)
    if not rows:
        raise ValueError(
            f"No {prior_season} rows in PlayerSeasons - call build_prior_season_baseline() first."
        )
    body = {
        "season": prior_season,
        "captured_during": season,
        "players": [player_season_to_json(row) for row in rows],
    }
    path = baseline_store(season, prior_season).write(body)
    logger.info("Wrote %d %s player-season rows to %s", len(rows), prior_season, path)
    return path


def load_prior_season_baseline(season: str | None = None) -> int:
    """Repopulate `PlayerSeasons` from the persisted snapshot instead of the API.

    Returns:
    - Number of rows loaded.

    Raises:
    - FileNotFoundError: if no baseline snapshot has been written for `season`.
    """
    season = season or Season.CURRENT
    prior_season = Season.previous(season)
    body = baseline_store(season, prior_season).load_latest()
    for row in body["players"]:
        PlayerSeasons.add(json_to_player_season(row))
    logger.info("Restored %d %s player-season rows from snapshot", len(body["players"]), prior_season)
    return len(body["players"])
