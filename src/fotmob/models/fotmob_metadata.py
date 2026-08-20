"""FotMob club identifiers, scoped per season.

FotMob team ids are stable for the lifetime of a club; what changes each season is *which*
clubs are in the Premier League. Modelling those separately means promotion and relegation is
a one-line edit to `SEASON_TEAMS`, and historic seasons keep resolving after a club goes down.

Key concepts:
- `FOTMOB_TEAM_IDS`: every club we have ever tracked, keyed by our own directory-safe name.
  These names are the directory names under `data/<season>/lineups/` and must not be renamed.
- `SEASON_TEAMS`: the 20 clubs in each season we hold data for.
- `validate_against_fpl`: cross-checks our roster against the FPL bootstrap so a missed
  promotion surfaces immediately rather than as silently absent lineups.
"""
from __future__ import annotations

from src.fpl.loader.utils import Season


FOTMOB_TEAM_IDS: dict[str, int] = {
    "Arsenal": 9825,
    "Aston Villa": 10252,
    "Bournemouth": 8678,
    "Brentford": 9937,
    "Brighton": 10204,
    "Burnley": 8191,
    "Chelsea": 8455,
    "Coventry": 8669,
    "Crystal Palace": 9826,
    "Everton": 8668,
    "Fulham": 9879,
    "Hull": 8667,
    "Ipswich": 9902,
    "Leeds": 8463,
    "Liverpool": 8650,
    "Manchester City": 8456,
    "Manchester United": 10260,
    "Newcastle": 10261,
    "Nottingham": 10203,
    "Spurs": 8586,
    "Sunderland": 8472,
    "Westham": 8654,
    "Wolves": 8602,
}
"""Our club name -> FotMob team id. Names double as `data/<season>/lineups/` directory names."""

FPL_SHORT_NAMES: dict[str, str] = {
    "Arsenal": "ARS",
    "Aston Villa": "AVL",
    "Bournemouth": "BOU",
    "Brentford": "BRE",
    "Brighton": "BHA",
    "Burnley": "BUR",
    "Chelsea": "CHE",
    "Coventry": "COV",
    "Crystal Palace": "CRY",
    "Everton": "EVE",
    "Fulham": "FUL",
    "Hull": "HUL",
    "Ipswich": "IPS",
    "Leeds": "LEE",
    "Liverpool": "LIV",
    "Manchester City": "MCI",
    "Manchester United": "MUN",
    "Newcastle": "NEW",
    "Nottingham": "NFO",
    "Spurs": "TOT",
    "Sunderland": "SUN",
    "Westham": "WHU",
    "Wolves": "WOL",
}
"""Our club name -> FPL `short_name`, used to reconcile the two providers."""

SEASON_TEAMS: dict[str, tuple[str, ...]] = {
    Season.s2526: (
        "Arsenal", "Aston Villa", "Bournemouth", "Brentford", "Brighton", "Burnley",
        "Chelsea", "Crystal Palace", "Everton", "Fulham", "Leeds", "Liverpool",
        "Manchester City", "Manchester United", "Newcastle", "Nottingham", "Spurs",
        "Sunderland", "Westham", "Wolves",
    ),
    Season.s2627: (
        "Arsenal", "Aston Villa", "Bournemouth", "Brentford", "Brighton", "Chelsea",
        "Coventry", "Crystal Palace", "Everton", "Fulham", "Hull", "Ipswich", "Leeds",
        "Liverpool", "Manchester City", "Manchester United", "Newcastle", "Nottingham",
        "Spurs", "Sunderland",
    ),
}
"""Premier League membership per season. 2026/27: Burnley, West Ham and Wolves down;
Coventry, Hull and Ipswich up."""


def teams_for_season(season: str | None = None) -> dict[int, str]:
    """Return `{fotmob_team_id: our_name}` for a season's Premier League clubs.

    Raises:
    - KeyError: if the season has no declared roster, or names a club with no FotMob id.
    """
    season = season or Season.CURRENT
    if season not in SEASON_TEAMS:
        raise KeyError(
            f"No FotMob team roster declared for season '{season}'. "
            f"Add it to SEASON_TEAMS in src/fotmob/models/fotmob_metadata.py."
        )
    roster = SEASON_TEAMS[season]
    missing = [name for name in roster if name not in FOTMOB_TEAM_IDS]
    if missing:
        raise KeyError(f"Season '{season}' names clubs with no FotMob id: {missing}")
    return {FOTMOB_TEAM_IDS[name]: name for name in roster}


def team_name_to_id_for_season(season: str | None = None) -> dict[str, int]:
    """Return `{our_name: fotmob_team_id}` for a season's Premier League clubs."""
    return {name: team_id for team_id, name in teams_for_season(season).items()}


def validate_against_fpl(fpl_teams: list[dict], season: str | None = None) -> None:
    """Assert our club roster matches the FPL bootstrap's for `season`.

    Parameters:
    - fpl_teams: the `teams` list from a bootstrap-static payload.
    - season: season to validate. Defaults to `Season.CURRENT`.

    Raises:
    - ValueError: on any mismatch. A promoted club we forgot to add would otherwise show up
      as silently missing lineups rather than an error.
    """
    season = season or Season.CURRENT
    ours = {FPL_SHORT_NAMES[name] for name in SEASON_TEAMS[season]}
    theirs = {team["short_name"] for team in fpl_teams}
    if ours != theirs:
        raise ValueError(
            f"FotMob roster for {season} disagrees with the FPL bootstrap.\n"
            f"  missing from SEASON_TEAMS: {sorted(theirs - ours)}\n"
            f"  stale in SEASON_TEAMS:     {sorted(ours - theirs)}\n"
            f"Update SEASON_TEAMS and FOTMOB_TEAM_IDS in src/fotmob/models/fotmob_metadata.py."
        )


TEAMS: dict[int, str] = teams_for_season(Season.CURRENT)
"""Current-season `{fotmob_team_id: name}`. Prefer `teams_for_season()` for explicit seasons."""

TEAM_NAME_TO_ID: dict[str, int] = {name: team_id for name, team_id in FOTMOB_TEAM_IDS.items()}
"""Name -> id across *all* seasons, so historic `lineups/` directories still resolve."""
