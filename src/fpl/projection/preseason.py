"""Pre-season friendlies, rolled up per FPL player.

Why bother with friendlies
--------------------------
In August they are the only fresh evidence there is. Everything else - last season's totals,
last season's per-match rows - describes a squad that has since changed manager, signed four
players and sold two. A friendly tells you who the manager is picking *now*.

They also genuinely predict. Backtesting 2025/26 (90 friendlies in July-August 2025 against
actual GW1-5 starts):

| Pre-season start share | players | mean GW1-5 starts |
|---|---|---|
| 0.00-0.25 | 12 | 0.17 |
| 0.25-0.50 | 50 | 1.32 |
| 0.50-0.75 | 135 | 2.38 |
| 0.75-1.00 | 103 | 3.60 |

Monotonic across the range. See `docs/prediction_roadmap.md` for the caveats on that
measurement.

Not every pre-season match is a friendly
----------------------------------------
The Community Shield and the UEFA Super Cup are played before gameweek 1 and are the *only*
matches in the window where the big clubs pick a real XI. Arsenal against Manchester City on
2026-08-16 settled three questions that four friendlies each had got wrong: Raya started (Kepa
had started all four friendlies and was on the bench), Haaland started (he had started none of
City's three friendlies), and Elliot Anderson started in City's midfield. Marc Guéhi, by
contrast, was benched behind Dias, Gvardiol and Khusanov.

So this module reads *every* match before the gameweek-1 deadline and weights them by kind:
`RotationConfig.match_kind_weights` counts a friendly at 0.35 and a competitive fixture at 1.0.
An earlier version filtered to friendlies only and threw the Community Shield away, which is
exactly backwards - it discarded the best evidence in the window.

Availability is the exception worth naming: a player on a friendly's `unavailable` list is as
injured as one missing a league game, so `unavailable` is counted at full strength and is often
ahead of the FPL news field. It flagged Coventry's Jack Rudoni first.

What this module does *not* cover
---------------------------------
Once the season is under way, "pre-season start share" stops being the freshest signal and
current-season starts take over. Wiring that in is still open work - see
`docs/prediction_roadmap.md`.
"""
from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass
from datetime import datetime

from src.fotmob.load import load_saved_match_details
from src.fotmob.rotation.fotmob_adapter import FotmobAdapter
from src.fotmob.rotation.rotation_config import RotationConfig
from src.fpl.loader.utils import Season
from src.fpl.models.immutable import Query


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PreseasonRole:
    """One player's involvement in the matches before gameweek 1.

    Counts are raw; `start_share` is weighted. `team_weight` is the denominator that matters -
    the club's total evidence weight, so starting the Community Shield counts for roughly three
    friendlies, and a player absent from every squad sheet has zero starts against a non-zero
    `team_weight`.
    """

    player_id: int
    team_matches: int
    team_weight: float
    matches: int
    starts: int
    competitive_starts: int
    weighted_starts: float
    benched: int
    unavailable: int

    @property
    def start_share(self) -> float:
        """Weighted share of the club's pre-season matches this player started.

        Capped at 1.0. A player who moved clubs mid-window keeps the starts he made for his
        old side - that is still evidence a manager picks him - but the denominator is his new
        club's fixture count, which can be smaller.
        """
        if not self.team_weight:
            return 0.0
        return min(1.0, self.weighted_starts / self.team_weight)

    @property
    def involvement_share(self) -> float:
        """Raw share of the club's pre-season matches this player was in the squad for."""
        if not self.team_matches:
            return 0.0
        return (self.starts + self.benched) / self.team_matches

    @property
    def has_evidence(self) -> bool:
        return self.team_weight > 0

    def as_dict(self) -> dict:
        return {
            'team_matches': self.team_matches,
            'team_weight': round(self.team_weight, 2),
            'matches': self.matches,
            'starts': self.starts,
            'competitive_starts': self.competitive_starts,
            'weighted_starts': round(self.weighted_starts, 2),
            'benched': self.benched,
            'unavailable': self.unavailable,
            'start_share': round(self.start_share, 3),
        }


def build_preseason_roles(
    season: str | None = None,
    rotation_config: RotationConfig | None = None,
    before_gameweek: int = 1,
) -> dict[int, PreseasonRole]:
    """Roll up every match before a gameweek deadline into per-FPL-player evidence.

    Requires the `Players`, `Teams` and `Gameweeks` collections to be populated - call
    `src.fpl.loader.load.load_from_snapshots` first.

    Parameters:
    - season: season whose `data/<season>/lineups/` directory to read. Defaults to
      `Season.CURRENT`.
    - rotation_config: supplies the per-`MatchKind` evidence weights. A friendly counts 0.35, a
      competitive fixture 1.0.
    - before_gameweek: only matches kicking off before this gameweek's deadline are read.
      Defaults to 1, i.e. the pre-season window.

    Returns:
    - FPL player id -> `PreseasonRole`, for every player at a club with stored matches in the
      window. Players at such a club who never appeared are present with zero starts, which is
      the informative case; players whose club has no stored matches are absent entirely, so a
      caller can tell "did not feature" apart from "we have no data on this club".

    Raises:
    - ValueError: on an ambiguous FotMob-to-FPL name match, or a club roster mismatch.
    - KeyError: if the deadline for `before_gameweek` is unknown.
    """
    season = season or Season.CURRENT
    rotation_config = rotation_config or RotationConfig()
    deadline = Query.gameweek(before_gameweek).deadline_time

    match_details = _matches_before(season, deadline)
    total = sum(len(matches) for matches in match_details.values())
    if not total:
        logger.warning(
            "No matches stored under data/%s/lineups before the GW%d deadline. Pre-season "
            "evidence will be empty; fetch it with: uv run -m src.fotmob.load --season %s",
            season, before_gameweek, season,
        )
        return {}

    kinds = Counter(match.kind for matches in match_details.values() for match in matches)
    logger.info(
        "Rolling up %d pre-GW%d matches across %d clubs (%s)",
        total, before_gameweek, len(match_details),
        ", ".join(f"{kind.value}={count}" for kind, count in sorted(
            kinds.items(), key=lambda pair: pair[0].value)),
    )

    # Academy players appear in every pre-season lineup and are not FPL elements. Skipping them
    # is correct; the adapter counts and logs them rather than dropping them quietly.
    adapter = FotmobAdapter(
        match_details,
        rotation_config,
        gw_mapper=lambda _event_time: 0,
        season=season,
        allow_unmatched=True,
    )

    team_evidence = _team_evidence(match_details, rotation_config)
    roles: dict[int, PreseasonRole] = {}
    for player in Query.all_players():
        if player.team.short_name not in team_evidence:
            raise KeyError(
                f"No pre-season evidence entry for {player.team.short_name} while rolling up "
                f"{season}. The club roster and the lineups directory disagree."
            )
        matches, weight = team_evidence[player.team.short_name]
        if not matches:
            continue
        roles[player.player_id] = _role_for(adapter, player.player_id, matches, weight)

    started = sum(1 for role in roles.values() if role.starts)
    competitive = sum(1 for role in roles.values() if role.competitive_starts)
    logger.info(
        "Pre-season roles: %d players, %d started something, %d started a competitive fixture",
        len(roles), started, competitive,
    )
    return roles


def _matches_before(season: str, deadline: datetime) -> dict[str, list]:
    """Every stored match for the season that kicked off before `deadline`, by club name."""
    all_matches = load_saved_match_details(season=season)
    return {
        team_name: [match for match in matches if match.event_time < deadline]
        for team_name, matches in all_matches.items()
    }


def _team_evidence(match_details: dict, rotation_config: RotationConfig) -> dict[str, tuple[int, float]]:
    """Club `short_name` -> (match count, total evidence weight).

    Every club in the season appears, with `(0, 0.0)` when nothing is stored for it, so the
    caller can tell an empty club apart from an unknown one.
    """
    from src.fotmob.models.fotmob_metadata import FPL_SHORT_NAMES

    measured = {
        FPL_SHORT_NAMES[team_name]: (
            len(matches),
            sum(rotation_config.weight_for(match.kind) for match in matches),
        )
        for team_name, matches in match_details.items()
    }
    return {
        team.short_name: measured.get(team.short_name, (0, 0.0))
        for team in Query.all_teams()
    }


def _role_for(
    adapter: FotmobAdapter,
    player_id: int,
    club_matches: int,
    club_weight: float,
) -> PreseasonRole:
    """Build one player's role, treating an unmapped player as "never featured"."""
    empty = PreseasonRole(
        player_id=player_id,
        team_matches=club_matches,
        team_weight=club_weight,
        matches=0, starts=0, competitive_starts=0, weighted_starts=0.0,
        benched=0, unavailable=0,
    )
    try:
        squad_role = adapter.get_player_squad_role(player_id, max_gameweek=None)
    except KeyError:
        # No FotMob counterpart, i.e. the player did not appear in any stored squad sheet.
        return empty
    return PreseasonRole(
        player_id=player_id,
        team_matches=club_matches,
        team_weight=club_weight,
        matches=squad_role.starts + squad_role.benched,
        starts=squad_role.starts,
        competitive_starts=squad_role.competitive_starts,
        weighted_starts=squad_role.weighted_starts,
        benched=squad_role.benched,
        unavailable=squad_role.unavailable,
    )
