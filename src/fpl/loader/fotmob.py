"""
Utilities for capturing and reading FotMob match details.

Two major responsibilities:
- `FotMobClient`: drives a Playwright browser to navigate club pages, capture the
  underlying `/api/data/teams` and `/api/data/matchDetails` responses, and persist
  them under `data/<season>/lineups/<team>/<match_id>.json`.
- `load_saved_match_details`: reads those saved JSON files and converts them into
  convenient Pydantic models (`MatchDetails`, `Substitution`, etc.) for downstream
  consumers.
"""
import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import parse_qs, quote, urlparse

from pydantic import BaseModel
from playwright.async_api import APIRequestContext, BrowserContext, async_playwright
from src.fpl.loader.utils import ensure_dir_exists


FOTMOB_BASE_URL = "https://www.fotmob.com"


TEAMS = {
    9825: "Arsenal",
    8456: "Manchester City",
    8455: "Chelsea",
    8472: "Southampton",
    8586: "Spurs",
    10252: "Aston Villa",
    10260: "Manchester United",
    8650: "Liverpool",
    8678: "Bournemouth",
    9826: "Crystal Palace",
    10204: "Brighton",
    9937: "Brentford",
    8668: "Everton",
    10261: "Newcastle",
    9879: "Fulham",
    8463: "Leeds",
    8191: "Burnley",
    8654: "Westham",
    10203: "Nottingham",
    8602: "Wolves",
}


class FotmobTeam(BaseModel):
    id: int
    name: str


class FotmobPlayer(BaseModel):
    id: int
    name: str


class Substitution(BaseModel):
    time: int
    player_out_injured: bool
    player_out: FotmobPlayer
    player_in: FotmobPlayer


class MatchDetails(BaseModel):
    match_id: int
    event_time: datetime
    opponent_team: FotmobTeam
    starters: list[FotmobPlayer]
    benched: list[FotmobPlayer]
    unavailable: list[FotmobPlayer]
    subs_log: list[Substitution]


def _normalize_team_key(name: str) -> str:
    return "".join(ch for ch in name.lower() if ch.isalnum())


class TeamFetchError(RuntimeError):
    def __init__(self, team_id: int, message: str):
        super().__init__(f"team_id={team_id}: {message}")
        self.team_id = team_id


class MatchFetchError(RuntimeError):
    def __init__(self, team_id: int, match_id: int, message: str):
        super().__init__(f"team_id={team_id} match_id={match_id}: {message}")
        self.team_id = team_id
        self.match_id = match_id


def _as_int(value: Any) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip():
        return int(value)
    raise ValueError("Missing numeric identifier")


def _build_player(entry: dict, context: str) -> FotmobPlayer:
    try:
        pid = _as_int(entry.get("id"))
    except Exception as exc:
        raise ValueError(f"Missing player id in {context}") from exc
    name = entry.get("name") or entry.get("shortName") or entry.get("fullName")
    if not name:
        raise ValueError(f"Missing player name in {context}")
    return FotmobPlayer(id=pid, name=name)


def _collect_players(entries: list[dict], context: str) -> list[FotmobPlayer]:
    return [_build_player(entry, context) for entry in (entries or [])]


def _collect_substitutions(match_json: dict, team_is_home: bool) -> list[Substitution]:
    events_root = (((match_json.get("content") or {}).get("matchFacts") or {}).get("events") or {})
    raw_events = events_root.get("events") or []
    subs: list[Substitution] = []
    for event in raw_events:
        if event.get("type") != "Substitution":
            continue
        if bool(event.get("isHome")) != team_is_home:
            continue
        swap = event.get("swap") or []
        if len(swap) != 2:
            raise ValueError(f"Unexpected substitution payload (swap len={len(swap)})")
        player_in = _build_player(swap[0], "substitution swap-in")
        player_out = _build_player(swap[1], "substitution swap-out")
        time_value = event.get("time")
        try:
            time_int = int(time_value)
        except Exception:
            time_int = 0
        injured = bool(event.get("injuredPlayerOut"))
        subs.append(
            Substitution(
                time=time_int,
                player_out_injured=injured,
                player_out=player_out,
                player_in=player_in,
            )
        )
    return subs


def _build_match_details(match_json: dict, team_name: str) -> MatchDetails:
    lineup = ((match_json.get("content") or {}).get("lineup") or {})
    home_section = lineup.get("homeTeam")
    away_section = lineup.get("awayTeam")
    if not home_section or not away_section:
        raise ValueError("Match JSON missing lineup information")

    norm_target = _normalize_team_key(team_name)
    home_norm = _normalize_team_key(home_section.get("name", ""))
    away_norm = _normalize_team_key(away_section.get("name", ""))

    if norm_target == home_norm:
        team_section, opponent_section, team_is_home = home_section, away_section, True
    elif norm_target == away_norm:
        team_section, opponent_section, team_is_home = away_section, home_section, False
    else:
        raise ValueError(f"Team '{team_name}' not found in match lineup ({home_section.get('name')} vs {away_section.get('name')})")

    general = match_json.get("general") or {}
    header_status = (match_json.get("header") or {}).get("status") or {}
    utc_time_str = header_status.get("utcTime") or general.get("matchTimeUTCDate")
    if not utc_time_str:
        raise ValueError("Match JSON missing kickoff time")
    event_time = datetime.fromisoformat(utc_time_str.replace("Z", "+00:00"))

    match_id_raw = general.get("matchId")
    if match_id_raw is None:
        raise ValueError("Match JSON missing matchId")
    match_id = int(match_id_raw)

    opponent_team = FotmobTeam(
        id=_as_int(opponent_section.get("id")),
        name=opponent_section.get("name", "Unknown"),
    )

    starters = _collect_players(team_section.get("starters", []), f"{team_name} starters")
    benched = _collect_players(team_section.get("subs", []), f"{team_name} bench")
    unavailable = _collect_players(team_section.get("unavailable", []), f"{team_name} unavailable")
    subs_log = _collect_substitutions(match_json, team_is_home)

    return MatchDetails(
        match_id=match_id,
        event_time=event_time,
        opponent_team=opponent_team,
        starters=starters,
        benched=benched,
        unavailable=unavailable,
        subs_log=subs_log,
    )


class FotMobClient:
    """Client for making FotMob API requests with browser context."""

    def __init__(self, headless: bool = True):
        """Initialize the client.

        Args:
            headless: Whether to run browser in headless mode (default: True)
        """
        self.headless = headless
        self._playwright = None
        self._browser = None
        self._context: Optional[BrowserContext] = None
        self._api_context: Optional[APIRequestContext] = None
        self._default_headers = {
            "accept": "*/*",
            "accept-language": "en-GB,en;q=0.9",
            "referer": f"{FOTMOB_BASE_URL}/",
            "sec-ch-ua": '"Chromium";v="142", "Google Chrome";v="142", "Not_A Brand";v="99"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"macOS"',
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-origin",
        }

    async def __aenter__(self):
        """Async context manager entry."""
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        await self.close()

    async def start(self):
        """Start the Playwright browser and create request context."""
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(headless=self.headless)

        # Create a browser context - this will maintain cookies and headers
        self._context = await self._browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/142.0.0.0 Safari/537.36"
            ),
            locale="en-GB",
            timezone_id="Europe/London",
        )

        # Ensure UK geo/country context via cookie similar to the cURL example
        # This helps the site choose GBR for ccode3 and any regional gating
        try:
            location_cookie_value = quote(json.dumps({
                "countryCode": "GB",
                "regionId": "30",
                "ip": "127.0.0.1",
                "ccode3": "GBR",
                "ccode3NoRegion": "GBR",
                "timezone": "Europe/London",
            }))
            await self._context.add_cookies([{
                "name": "u:location",
                "value": location_cookie_value,
                "domain": "www.fotmob.com",
                "path": "/",
                # Allow regular use; no need to set httpOnly/secure explicitly here
            }])
        except Exception as e:
            logging.warning(f"Failed to set GBR location cookie (continuing anyway): {e}")

        # Use the browser context's request API directly (not used for fetching, but kept for completeness)
        self._api_context = self._context.request

    async def close(self):
        """Close the browser and cleanup."""
        # Note: self._api_context is self._context.request, so it will be closed with the context
        if self._context:
            await self._context.close()
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()

    @staticmethod
    def _is_teams_api_response(url: str, team_id: int) -> bool:
        """Return True if given URL matches /api/data/teams with the specified team id."""
        try:
            parsed = urlparse(url)
            if not parsed.path.endswith("/api/data/teams"):
                return False
            qs = parse_qs(parsed.query)
            ids = qs.get("id") or []
            return str(team_id) in ids
        except Exception:
            logging.debug("Failed to inspect teams API url=%s", url, exc_info=True)
            return False

    @staticmethod
    def _is_match_details_response(url: str, match_id: int) -> bool:
        """Return True if given URL matches /api/data/matchDetails with the specified match id."""
        try:
            parsed = urlparse(url)
            if not parsed.path.endswith("/api/data/matchDetails"):
                return False
            qs = parse_qs(parsed.query)
            ids = qs.get("matchId") or []
            return str(match_id) in ids
        except Exception:
            logging.debug("Failed to inspect matchDetails url=%s", url, exc_info=True)
            return False

    @staticmethod
    def _parse_utc_time(dt_str: str) -> Optional[datetime]:
        try:
            return datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
        except Exception:
            logging.debug("Failed to parse utc timestamp %s", dt_str, exc_info=True)
            return None

    async def get_team_data(self, team_id: int, ccode3: str = "GBR") -> Dict[str, Any]:
        """Fetch team data from FotMob API.

        Args:
            team_id: FotMob team ID (e.g., 8650 for Liverpool)
            ccode3: Country code (default: "GBR" for United Kingdom)

        Returns:
            Team data as dictionary

        Example:
            >>> async with FotMobClient() as client:
            ...     data = await client.get_team_data(8650, "GBR")
        """
        if not self._context:
            raise RuntimeError("Client not started. Use async context manager or call start() first.")

        logging.info(f"Fetching team data: team_id={team_id}, ccode3={ccode3}")
        
        # Use a page and capture the natural API response done by the app
        page = await self._context.new_page()
        
        try:
            # Navigate to team overview - the site should trigger the API call we need
            await page.goto(
                f"{FOTMOB_BASE_URL}/teams/{team_id}/overview",
                wait_until="domcontentloaded",
                timeout=30000,
            )
            try:
                response = await page.wait_for_event(
                    "response",
                    predicate=lambda resp: self._is_teams_api_response(resp.url, team_id),
                    timeout=45000,
                )
                captured = await response.json()
            except Exception as exc:
                raise TeamFetchError(team_id, "Timed out waiting for team details API response") from exc
            
            logging.info(f"Successfully fetched team data for team_id={team_id}")
            return captured
        finally:
            await page.close()

    async def collect_team_matches(
        self,
        team_id: int,
        team_name: str,
        season: str = "2025-2026",
        matches_limit: Optional[int] = None,
    ) -> list[int]:
        """Load team fixtures and save new finished matches' details.

        Returns list of match IDs saved.
        """
        # Step 1: Load team data via page and capture API JSON
        try:
            team_data = await self.get_team_data(team_id, ccode3="GBR")
        except TeamFetchError as exc:
            logging.error("[team] %s: %s", team_name, exc)
            return []
        fixtures_root = (((team_data.get("fixtures") or {}).get("allFixtures") or {}))
        fixtures_list: list[dict] = fixtures_root.get("fixtures") or []
        # Verbose: identify last finished match from API if present
        last_match = fixtures_root.get("lastMatch") or {}
        lm_id = last_match.get("id")
        lm_status = (last_match.get("status") or {})
        lm_time = lm_status.get("utcTime")
        lm_result = lm_status.get("scoreStr")
        logging.info(f"[team] {team_name}: last finished match id={lm_id} time={lm_time} score={lm_result}")

        # Step 2: Determine already saved matches for this team
        base_dir = os.path.join("data", season, "lineups", team_name)
        ensure_dir_exists(os.path.join(base_dir, "_"))
        existing_ids: set[int] = set()
        for fname in os.listdir(base_dir):
            if fname.endswith(".json"):
                try:
                    existing_ids.add(int(fname.replace(".json", "")))
                except Exception:
                    continue

        # Step 3: Identify finished, past-dated, not-yet-saved fixtures
        now = datetime.now(timezone.utc)
        candidates: list[tuple[datetime, int, str]] = []
        for fx in fixtures_list:
            match_id = fx.get("id")
            page_url = fx.get("pageUrl")
            status = fx.get("status") or {}
            finished = bool(status.get("finished"))
            utc_time = status.get("utcTime")
            if not match_id or not page_url or not utc_time:
                continue
            dt = self._parse_utc_time(utc_time)
            if not dt:
                continue
            if finished and dt <= now and int(match_id) not in existing_ids:
                candidates.append((dt, int(match_id), page_url))

        # Oldest first
        candidates.sort(key=lambda t: t[0])
        total_candidates = len(candidates)
        if matches_limit is not None and matches_limit >= 0:
            candidates = candidates[:matches_limit]
        logging.info(f"[team] {team_name}: {len(existing_ids)} known, {total_candidates} new finished; "
                     f"loading up to {len(candidates)}")

        # Step 4: With the same page/context, iterate and capture matchDetails for each candidate
        saved_ids: list[int] = []
        page = await self._context.new_page()
        try:
            for idx, (dt, match_id, page_url) in enumerate(candidates, start=1):
                logging.info(f"[progress] {team_name}: loading match {idx}/{len(candidates)} "
                             f"id={match_id} date={dt.isoformat()}")
                target_url = f"{FOTMOB_BASE_URL}{page_url}"
                logging.info(f"[match] Navigating to {target_url}")
                await page.goto(target_url, wait_until="domcontentloaded", timeout=30000)
                try:
                    resp = await page.wait_for_event(
                        "response",
                        predicate=lambda r: self._is_match_details_response(r.url, match_id),
                        timeout=45000,
                    )
                    captured = await resp.json()
                except Exception as exc:
                    raise MatchFetchError(team_id, match_id, "Timed out waiting for matchDetails response") from exc

                filepath = os.path.join(base_dir, f"{match_id}.json")
                try:
                    with open(filepath, "w") as f:
                        json.dump(captured, f, indent=2)
                    logging.info(f"[match] Saved match id={match_id} -> {filepath}")
                    saved_ids.append(match_id)
                except Exception as exc:
                    logging.warning(f"[match] Failed to save match id={match_id}: {exc}")
            return saved_ids
        except MatchFetchError as exc:
            logging.error("[team] %s: %s", team_name, exc)
            return saved_ids
        finally:
            await page.close()

def load_saved_match_details(
    season: str = "2025-2026",
    team_filter: Optional[list[str]] = None,
    limit_per_team: Optional[int] = None,
) -> dict[str, list[MatchDetails]]:
    """Load saved matchDetails JSON files and convert them into MatchDetails models.

    Returns:
        Mapping team_name -> list of MatchDetails sorted by event_time.
    """
    base_dir = Path("data") / season / "lineups"
    result: dict[str, list[MatchDetails]] = {}
    if not base_dir.exists():
        return result

    selected_teams = team_filter if team_filter is not None else [d.name for d in base_dir.iterdir() if d.is_dir()]
    for team_name in selected_teams:
        team_path = base_dir / team_name
        if not team_path.is_dir():
            continue
        match_files = sorted(team_path.glob("*.json"), key=lambda p: int(p.stem))
        if limit_per_team is not None and limit_per_team >= 0:
            match_files = match_files[:limit_per_team]
        match_list: list[MatchDetails] = []
        for match_file in match_files:
            match_json = json.loads(match_file.read_text())
            details = _build_match_details(match_json, team_name)
            match_list.append(details)
        match_list.sort(key=lambda d: d.event_time)
        result[team_name] = match_list
    return result


def main():
    """CLI entry point for testing FotMob API calls."""
    import argparse

    parser = argparse.ArgumentParser(description="Load FotMob match details for configured teams")
    parser.add_argument("--team-id", type=int, help="Specific FotMob team ID to process (default: all teams in TEAMS)")
    parser.add_argument("--matches-limit", type=int, default=None, help="Load earliest N new matches only")
    parser.add_argument("--no-headless", action="store_true", help="Run browser in visible mode")
    parser.add_argument("--season", type=str, default="2025-2026", help="Season directory name (default: 2025-2026)")

    args = parser.parse_args()

    async def _run():
        async with FotMobClient(headless=not args.no_headless) as client:
            # Determine team IDs to process
            team_ids: list[int]
            if args.team_id:
                team_ids = [int(args.team_id)]
            else:
                team_ids = list(TEAMS.keys())

            total_saved = 0

            # Otherwise, iterate teams and save new matches up to limit
            for team_id in team_ids:
                team_name = TEAMS.get(team_id, f"team-{team_id}")
                print(f"[team] {team_name} ({team_id})")
                saved_ids = await client.collect_team_matches(
                    team_id=team_id,
                    team_name=team_name,
                    season=args.season,
                    matches_limit=args.matches_limit,
                )
                print(f"[team] saved {len(saved_ids)} matches: {saved_ids}")
                total_saved += len(saved_ids)

            print(f"Done. Total new matches saved: {total_saved}")

    asyncio.run(_run())


if __name__ == "__main__":
    main()

