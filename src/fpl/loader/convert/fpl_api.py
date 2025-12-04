from __future__ import annotations

from datetime import datetime

from src.fpl.models.immutable import (
    Fixture,
    Gameweek,
    Player,
    PlayerFixture,
    PlayerType,
    Team,
    TeamFixture,
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
        score=row["team_h_score"],
    )
    away = TeamFixture(
        fixture_id=row["id"],
        team_id=row["team_a"],
        difficulty=row["team_a_difficulty"],
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
    )


def player_to_json(player: Player) -> dict:
    """Convert a Player dataclass into the bootstrap JSON representation."""
    return {
        "id": player.player_id,
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
    }


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

