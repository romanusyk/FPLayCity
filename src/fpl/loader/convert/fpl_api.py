from __future__ import annotations

from datetime import datetime

from src.fpl.const import GameMode
from src.fpl.models.immutable import (
    Fixture,
    Gameweek,
    Player,
    PlayerFixture,
    PlayerSeason,
    PlayerType,
    PriorSeasonSource,
    Team,
    TeamFixture,
    PlayerPresence,
)


def event_json_to_gameweek(row: dict) -> Gameweek:
    """Convert a bootstrap event row into a Gameweek dataclass."""
    deadline_time = row.get("deadline_time")
    if deadline_time is None:
        raise ValueError(f"Missing deadline_time for gameweek {row.get('id')}")
    deadline_dt = datetime.fromisoformat(deadline_time.replace('Z', '+00:00'))
    return Gameweek(
        gameweek=row["id"],
        deadline_time=deadline_dt,
    )


def gameweek_to_json(gameweek: Gameweek) -> dict:
    """Convert a Gameweek dataclass back into a minimal JSON dict."""
    return {
        "id": gameweek.gameweek,
        "deadline_time": gameweek.deadline_time.isoformat(),
    }


def team_json_to_team(row: dict) -> Team:
    """Convert a bootstrap team row into a Team dataclass."""
    return Team(
        team_id=row["id"],
        short_name=row["short_name"],
        name=row["name"],
        strength_overall_home=row["strength_overall_home"],
        strength_overall_away=row["strength_overall_away"],
        strength_attack_home=row["strength_attack_home"],
        strength_attack_away=row["strength_attack_away"],
        strength_defence_home=row["strength_defence_home"],
        strength_defence_away=row["strength_defence_away"],
    )


def team_to_json(team: Team) -> dict:
    """Convert a Team dataclass into the bootstrap JSON representation."""
    return {
        "id": team.team_id,
        "name": team.name,
        "strength_overall_home": team.strength_overall_home,
        "strength_overall_away": team.strength_overall_away,
        "strength_attack_home": team.strength_attack_home,
        "strength_attack_away": team.strength_attack_away,
        "strength_defence_home": team.strength_defence_home,
        "strength_defence_away": team.strength_defence_away,
    }


def fixture_json_to_fixture(row: dict) -> Fixture:
    """Convert a fixtures endpoint row into Fixture/TeamFixture dataclasses."""
    home = TeamFixture(
        fixture_id=row["id"],
        team_id=row["team_h"],
        difficulty=row["team_h_difficulty"],
        gameweek=row["event"],
        score=row["team_h_score"],
    )
    away = TeamFixture(
        fixture_id=row["id"],
        team_id=row["team_a"],
        difficulty=row["team_a_difficulty"],
        gameweek=row["event"],
        score=row["team_a_score"],
    )
    return Fixture(
        fixture_id=row["id"],
        finished=row["finished"],
        gameweek=row["event"],
        home=home,
        away=away,
    )


def fixture_to_json(fixture: Fixture) -> dict:
    """Convert a Fixture dataclass (with nested TeamFixtures) back to JSON."""
    return {
        "id": fixture.fixture_id,
        "finished": fixture.finished,
        "event": fixture.gameweek,
        "team_h": fixture.home.team_id,
        "team_h_difficulty": fixture.home.difficulty,
        "team_h_score": fixture.home.score,
        "team_a": fixture.away.team_id,
        "team_a_difficulty": fixture.away.difficulty,
        "team_a_score": fixture.away.score,
    }


def element_json_to_player(row: dict) -> Player:
    """Convert a bootstrap element row into a Player dataclass."""
    return Player(
        player_id=row["id"],
        code=row["code"],
        first_name=row["first_name"],
        second_name=row["second_name"],
        web_name=row["web_name"],
        player_type=PlayerType(row["element_type"]),
        team_id=row["team"],
        now_cost=row["now_cost"] / 10.0,
        status=row["status"],
        chance_of_playing_next_round=row["chance_of_playing_next_round"],
        chance_of_playing_this_round=row["chance_of_playing_this_round"],
        news=row["news"],
        minutes=row["minutes"],
        selected_by_percent=float(row["selected_by_percent"]),
        penalties_order=row["penalties_order"],
        corners_order=row["corners_and_indirect_freekicks_order"],
        direct_freekicks_order=row["direct_freekicks_order"],
    )


def player_to_json(player: Player) -> dict:
    """Convert a Player dataclass into the bootstrap JSON representation."""
    return {
        "id": player.player_id,
        "code": player.code,
        "first_name": player.first_name,
        "second_name": player.second_name,
        "web_name": player.web_name,
        "element_type": player.player_type.value,
        "team": player.team_id,
        "now_cost": int(player.now_cost * 10),
        "status": player.status,
        "chance_of_playing_next_round": player.chance_of_playing_next_round,
        "chance_of_playing_this_round": player.chance_of_playing_this_round,
        "news": player.news,
        "minutes": player.minutes,
        "selected_by_percent": str(player.selected_by_percent),
        "penalties_order": player.penalties_order,
        "corners_and_indirect_freekicks_order": player.corners_order,
        "direct_freekicks_order": player.direct_freekicks_order,
    }


PRIOR_SEASON_TOTALS: tuple[str, ...] = (
    "minutes",
    "starts",
    "total_points",
    "goals_scored",
    "assists",
    "clean_sheets",
    "goals_conceded",
    "own_goals",
    "penalties_saved",
    "penalties_missed",
    "yellow_cards",
    "red_cards",
    "saves",
    "bonus",
    "bps",
    "defensive_contribution",
)
"""Integer totals carried identically by bootstrap elements and `history_past` rows."""

PRIOR_SEASON_EXPECTED: tuple[str, ...] = (
    "expected_goals",
    "expected_assists",
    "expected_goal_involvements",
    "expected_goals_conceded",
)
"""Expected-stat fields, delivered as decimal strings by both payloads."""


def prior_season_to_player_season(
    element_row: dict,
    history_past_rows: list[dict],
    season: str,
    fpl_season_name: str,
    team: str,
    prior_team: str | None,
) -> PlayerSeason | None:
    """Build a `PlayerSeason` for the season preceding the one currently loaded.

    Before a season kicks off, the FPL bootstrap carries each element's *previous*-season
    totals. It mirrors `history_past` exactly for players who stayed at the same club, but is
    unreliable for anyone who moved: usually zeroed, occasionally truncated, and in both cases
    leaving a stale `defensive_contribution` behind. `history_past` is authoritative here, and
    bootstrap is used only to cross-check.

    Parameters:
    - element_row: A bootstrap `elements` entry for the season being loaded.
    - history_past_rows: The `history_past` list from that element's summary payload.
    - season: Season directory name the totals belong to (e.g. `2025-2026`).
    - fpl_season_name: The same season in FPL's `history_past` form (e.g. `2025/26`).
    - team: The player's current club `short_name`.
    - prior_team: The player's club `short_name` in `season`, or None if we hold no bootstrap
      snapshot for that season to look it up in. Clubs are compared by short name because FPL
      renumbers team ids every season.

    Returns:
    - A `PlayerSeason`, or None when the player has no Premier League record for that season
      (a signing from abroad, or a promoted-club player). Absence is real information and is
      never substituted with zeros.

    Raises:
    - ValueError: if the two sources disagree for a player who did *not* change club. That
      breaks the observed invariant and must surface rather than silently corrupt the
      baseline. Divergence for a player who moved is expected and is recorded in `source`.
    """
    season_rows = [row for row in history_past_rows if row["season_name"] == fpl_season_name]
    if len(season_rows) > 1:
        raise ValueError(
            f"Player {element_row['id']} ({element_row['web_name']}) has {len(season_rows)} "
            f"'{fpl_season_name}' rows in history_past. Expected at most one aggregated row."
        )
    history_row = season_rows[0] if season_rows else None
    bootstrap_minutes = element_row["minutes"]
    # None when we hold no prior-season bootstrap, so club changes cannot be established.
    changed_club = None if prior_team is None else prior_team != team

    if history_row is None:
        if bootstrap_minutes > 0:
            raise ValueError(
                f"Player {element_row['id']} ({element_row['web_name']}) has {bootstrap_minutes} bootstrap "
                f"minutes but no '{fpl_season_name}' row in history_past. The prior-season baseline cannot "
                f"be reconciled - inspect the element-summary payload before trusting this load."
            )
        return None

    divergent = {
        field: (element_row[field], history_row[field])
        for field in PRIOR_SEASON_TOTALS
        if element_row[field] != history_row[field]
    }

    if not divergent:
        source = PriorSeasonSource.BOOTSTRAP if bootstrap_minutes > 0 else PriorSeasonSource.HISTORY_PAST
    elif changed_club is False:
        raise ValueError(
            f"Player {element_row['id']} ({element_row['web_name']}) did not change club, yet bootstrap "
            f"totals disagree with history_past for {fpl_season_name}: {divergent}. Bootstrap is expected "
            f"to mirror the previous season exactly for players who stayed put."
        )
    elif bootstrap_minutes == 0:
        source = PriorSeasonSource.RECOVERED_FROM_HISTORY
    else:
        source = PriorSeasonSource.PARTIAL_IN_BOOTSTRAP

    totals = {field: history_row[field] for field in PRIOR_SEASON_TOTALS}
    expected = {field: float(history_row[field]) for field in PRIOR_SEASON_EXPECTED}

    return PlayerSeason(
        player_id=element_row["id"],
        season=season,
        source=source,
        team_id=element_row["team"],
        team=team,
        prior_team=prior_team,
        **totals,
        **expected,
    )


def player_season_to_json(player_season: PlayerSeason) -> dict:
    """Convert a PlayerSeason dataclass into a persistable JSON dict."""
    payload = {
        "player_id": player_season.player_id,
        "season": player_season.season,
        "source": player_season.source.value,
        "team_id": player_season.team_id,
        "team": player_season.team,
        "prior_team": player_season.prior_team,
    }
    payload.update({field: getattr(player_season, field) for field in PRIOR_SEASON_TOTALS})
    payload.update({field: getattr(player_season, field) for field in PRIOR_SEASON_EXPECTED})
    return payload


def json_to_player_season(row: dict) -> PlayerSeason:
    """Rebuild a PlayerSeason from its persisted JSON dict."""
    return PlayerSeason(
        player_id=row["player_id"],
        season=row["season"],
        source=PriorSeasonSource(row["source"]),
        team_id=row["team_id"],
        team=row["team"],
        prior_team=row["prior_team"],
        **{field: row[field] for field in PRIOR_SEASON_TOTALS},
        **{field: row[field] for field in PRIOR_SEASON_EXPECTED},
    )


def history_entry_to_player_fixture(row: dict) -> PlayerFixture:
    """Convert a player history entry into a PlayerFixture dataclass."""
    return PlayerFixture(
        player_id=row["element"],
        fixture_id=row["fixture"],
        gameweek=row["round"],
        was_home=row["was_home"],
        total_points=row["total_points"],
        minutes=row["minutes"],
        goals_scored=row["goals_scored"],
        assists=row["assists"],
        clean_sheets=row["clean_sheets"],
        defensive_contribution=row.get("defensive_contribution", 0),
        expected_goals=float(row["expected_goals"]),
        expected_assists=float(row["expected_assists"]),
        expected_goal_involvements=float(row["expected_goal_involvements"]),
        expected_goals_conceded=float(row["expected_goals_conceded"]),
        value=row["value"],
        starts=row["starts"],
    )


def future_fixture_to_player_fixture(player_id: int, row: dict) -> PlayerFixture:
    """Convert a future fixture entry into a (minimal) PlayerFixture dataclass."""
    return PlayerFixture(
        player_id=player_id,
        fixture_id=row["id"],
        gameweek=row["event"],
        was_home=row["is_home"],
    )


def player_fixture_to_history_json(player_fixture: PlayerFixture) -> dict:
    """Convert a historical PlayerFixture dataclass back to JSON."""
    return {
        "element": player_fixture.player_id,
        "fixture": player_fixture.fixture_id,
        "round": player_fixture.gameweek,
        "was_home": player_fixture.was_home,
        "total_points": player_fixture.total_points,
        "minutes": player_fixture.minutes,
        "goals_scored": player_fixture.goals_scored,
        "assists": player_fixture.assists,
        "clean_sheets": player_fixture.clean_sheets,
        "defensive_contribution": player_fixture.defensive_contribution,
        "expected_goals": player_fixture.expected_goals,
        "expected_assists": player_fixture.expected_assists,
        "expected_goal_involvements": player_fixture.expected_goal_involvements,
        "expected_goals_conceded": player_fixture.expected_goals_conceded,
        "value": player_fixture.value,
        "starts": player_fixture.starts,
    }


def player_fixture_to_future_json(player_fixture: PlayerFixture) -> dict:
    """Convert a future-looking PlayerFixture dataclass back to JSON."""
    return {
        "id": player_fixture.fixture_id,
        "event": player_fixture.gameweek,
        "is_home": player_fixture.was_home,
    }


def fpl_presence_json_to_player_presence(row: dict, gameweek: int, manager_id: int, is_mine: bool) -> PlayerPresence:
    """Convert a FPL presence row into a PlayerPresence dataclass."""
    return PlayerPresence(
        game_mode=GameMode.fpl,
        manager_id=manager_id,
        is_mine=is_mine,
        player_id=row["element"],
        gameweek=gameweek,
        position=row["position"],
        is_captain=row["is_captain"],
        is_vice_captain=row["is_vice_captain"],
        multiplier=row["multiplier"],
    )
