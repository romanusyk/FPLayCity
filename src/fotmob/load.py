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
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, Optional
from urllib.parse import parse_qs, quote, urlparse

if TYPE_CHECKING:  # Only the fetching path needs a browser; readers must not pay for one.
    from playwright.async_api import APIRequestContext, BrowserContext

from src.fpl.loader.utils import Season, ensure_dir_exists
from src.fotmob.models.fotmob import (
    FotmobTeam,
    FotmobPlayer,
    MatchDetails,
    MatchKind,
    Substitution,
    classify_match_kind,
)
from src.fotmob.models.fotmob_metadata import TEAM_NAME_TO_ID, teams_for_season


FOTMOB_BASE_URL = "https://www.fotmob.com"


class TeamFetchError(RuntimeError):
    def __init__(self, team_id: int, message: str):
        super().__init__(f"team_id={team_id}: {message}")
        self.team_id = team_id


class MatchFetchError(RuntimeError):
    def __init__(self, team_id: int, match_id: int, message: str):
        super().__init__(f"team_id={team_id} match_id={match_id}: {message}")
        self.team_id = team_id
        self.match_id = match_id


class StaleFixtureError(MatchFetchError):
    """A fixture-list entry whose slug no longer resolves to that match.

    FotMob match slugs are not season-scoped, so last season's
    `/matches/burnley-vs-wolverhampton-wanderers/...` now serves *next* season's meeting. The
    entry is stale rather than broken, so callers skip and count it instead of aborting.
    """


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


def _parse_match_identity(match_json: dict, general: dict) -> tuple[int, datetime]:
    """Extract (match_id, kickoff time) from a match payload."""
    header_status = (match_json.get("header") or {}).get("status") or {}
    utc_time_str = header_status.get("utcTime") or general.get("matchTimeUTCDate")
    if not utc_time_str:
        raise ValueError("Match JSON missing kickoff time")
    match_id_raw = general.get("matchId")
    if match_id_raw is None:
        raise ValueError("Match JSON missing matchId")
    return int(match_id_raw), datetime.fromisoformat(utc_time_str.replace("Z", "+00:00"))


def _build_lineup_less_match(
    match_json: dict,
    general: dict,
    team_id: int,
    league_name: str,
    kind: MatchKind,
) -> MatchDetails:
    """Build `MatchDetails` for a friendly FotMob published without any lineup.

    The fixture still counts towards a club's pre-season schedule, so we keep it with empty
    squad lists and `lineup_available=False`. Downstream consumers must not read `starters`
    or `benched` from these without checking the flag.
    """
    match_id, event_time = _parse_match_identity(match_json, general)
    return MatchDetails(
        match_id=match_id,
        event_time=event_time,
        opponent_team=_opponent_from_general(general, team_id),
        starters=[],
        benched=[],
        unavailable=[],
        subs_log=[],
        league_name=league_name,
        kind=kind,
        lineup_available=False,
    )


def _opponent_from_general(general: dict, team_id: int) -> FotmobTeam:
    """Resolve the opponent from the `general` block, used when no lineup was published."""
    home = general.get("homeTeam") or {}
    away = general.get("awayTeam") or {}
    home_id, away_id = _as_int(home.get("id")), _as_int(away.get("id"))
    if team_id == home_id:
        other = away
    elif team_id == away_id:
        other = home
    else:
        raise ValueError(f"Team id {team_id} is neither side of match ({home_id} vs {away_id})")
    return FotmobTeam(id=_as_int(other.get("id")), name=other.get("name", "Unknown"))


def _build_match_details(match_json: dict, team_id: int) -> MatchDetails:
    """Convert a saved FotMob match payload into one team's `MatchDetails`.

    Friendlies are frequently published without any lineup. That is expected, so it produces a
    `MatchDetails` with `lineup_available=False` rather than an exception - the match still
    tells us the fixture happened. A *competitive* match without a lineup is a genuine data
    problem and raises.

    Raises:
    - ValueError: on a competitive match with no lineup, a missing kickoff time or match id,
      or when `team_id` is not one of the two sides.
    """
    general = match_json.get("general") or {}
    league_name = general.get("leagueName")
    if not league_name:
        raise ValueError("Match JSON missing league name")
    kind = classify_match_kind(league_name)

    lineup = ((match_json.get("content") or {}).get("lineup") or {})
    home_section = lineup.get("homeTeam")
    away_section = lineup.get("awayTeam")
    lineup_available = bool(home_section and away_section)

    if not lineup_available:
        if kind is not MatchKind.FRIENDLY:
            raise ValueError(
                f"Competitive match {general.get('matchId')} ({league_name}) has no lineup section. "
                f"Re-fetch the match; we do not drop competitive fixtures."
            )
        return _build_lineup_less_match(match_json, general, team_id, league_name, kind)

    home_id = _as_int(home_section.get("id"))
    away_id = _as_int(away_section.get("id"))

    if team_id == home_id:
        team_section, opponent_section, team_is_home = home_section, away_section, True
    elif team_id == away_id:
        team_section, opponent_section, team_is_home = away_section, home_section, False
    else:
        raise ValueError(
            f"Team id {team_id} not found in match lineup "
            f"({home_section.get('id')} vs {away_section.get('id')})"
        )

    match_id, event_time = _parse_match_identity(match_json, general)

    opponent_team = FotmobTeam(
        id=_as_int(opponent_section.get("id")),
        name=opponent_section.get("name", "Unknown"),
    )

    starters = _collect_players(team_section.get("starters", []), "starters")
    benched = _collect_players(team_section.get("subs", []), "bench")
    unavailable = _collect_players(team_section.get("unavailable", []), "unavailable")
    subs_log = _collect_substitutions(match_json, team_is_home)

    return MatchDetails(
        match_id=match_id,
        event_time=event_time,
        opponent_team=opponent_team,
        starters=starters,
        benched=benched,
        unavailable=unavailable,
        subs_log=subs_log,
        league_name=league_name,
        kind=kind,
        lineup_available=True,
    )


def _extract_next_page_props(html: str) -> dict:
    """Extract Next.js `pageProps` JSON from a FotMob page HTML."""
    m = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', html, re.S)
    if not m:
        raise ValueError("Missing __NEXT_DATA__ script tag")
    data = json.loads(m.group(1))
    props = data.get("props") or {}
    page_props = props.get("pageProps")
    if not isinstance(page_props, dict):
        raise ValueError("Missing pageProps in __NEXT_DATA__")
    return page_props


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
        self._context: Optional['BrowserContext'] = None
        self._api_context: Optional['APIRequestContext'] = None
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
        """Start the Playwright browser and create request context.

        Playwright is imported here rather than at module scope so that reading stored
        lineups with `load_saved_match_details` does not require a browser to be installed.
        """
        from playwright.async_api import async_playwright

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
        page = await asyncio.wait_for(self._context.new_page(), timeout=15)
        try:
            try:
                async with page.expect_response(
                    lambda resp: self._is_teams_api_response(resp.url, team_id),
                    timeout=45000,
                ) as response_info:
                    await asyncio.wait_for(
                        page.goto(
                            f"{FOTMOB_BASE_URL}/teams/{team_id}/overview",
                            wait_until="domcontentloaded",
                            timeout=30000,
                        ),
                        timeout=35,
                    )
                    response = await response_info.value
                captured = await response.json()
            except Exception as exc:
                raise TeamFetchError(team_id, f"Failed to fetch team data: {type(exc).__name__}: {exc}") from exc

            logging.info(f"Successfully fetched team data for team_id={team_id}")
            return captured
        finally:
            await page.close()

    async def collect_team_matches(
        self,
        team_id: int,
        team_name: str,
        season: str | None = None,
        matches_limit: Optional[int] = None,
    ) -> list[int]:
        """Load team fixtures and save every new finished match, friendlies included.

        Pre-season friendlies appear in the same `allFixtures` feed as competitive matches, so
        no special casing is needed here - `classify_match_kind` separates them at parse time.

        Returns:
        - Match ids newly written to `data/<season>/lineups/<team_name>/`.
        """
        season = season or Season.CURRENT
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

        # Step 3: Identify finished, past-dated, in-season, not-yet-saved fixtures.
        #
        # FotMob's team feed spans seasons, and its match slugs are not season-scoped: the
        # pageUrl for last season's "brentford-vs-liverpool" resolves to *this* season's
        # fixture. Without the season window we silently save the wrong match.
        now = datetime.now(timezone.utc)
        season_start, season_end = Season.window(season)
        candidates: list[tuple[datetime, int, str]] = []
        out_of_window = 0
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
            if not (finished and dt <= now) or int(match_id) in existing_ids:
                continue
            if not (season_start <= dt < season_end):
                out_of_window += 1
                continue
            candidates.append((dt, int(match_id), page_url))
        if out_of_window:
            logging.info(
                "[team] %s: ignored %d finished match(es) outside the %s window (%s..%s)",
                team_name, out_of_window, season, season_start.date(), season_end.date(),
            )

        # Oldest first
        candidates.sort(key=lambda t: t[0])
        total_candidates = len(candidates)
        if matches_limit is not None and matches_limit >= 0:
            candidates = candidates[:matches_limit]
        logging.info(f"[team] {team_name}: {len(existing_ids)} known, {total_candidates} new finished; "
                     f"loading up to {len(candidates)}")

        # Step 4: Iterate matches and save match details.
        #
        # NOTE: FotMob's JSON API endpoints can be protected (403 / hanging bodies) in automation.
        # The match page HTML includes a full `__NEXT_DATA__` payload with `pageProps` that mirrors
        # the matchDetails structure (`general`, `header`, `content`, ...). We parse that instead.
        saved_ids: list[int] = []
        stale_slugs: list[int] = []
        try:
            for idx, (dt, match_id, page_url) in enumerate(candidates, start=1):
                logging.info(f"[progress] {team_name}: loading match {idx}/{len(candidates)} "
                             f"id={match_id} date={dt.isoformat()}")
                target_url = f"{FOTMOB_BASE_URL}{page_url}".split("#")[0]
                logging.info(f"[match] Navigating to {target_url}")
                try:
                    resp = await asyncio.wait_for(
                        self._context.request.get(target_url, headers={"accept": "text/html", **self._default_headers}),
                        timeout=30,
                    )
                    if not resp.ok:
                        raise RuntimeError(f"HTTP {resp.status}")
                    html = await asyncio.wait_for(resp.text(), timeout=30)
                    captured = _extract_next_page_props(html)
                    if not isinstance(captured.get("general"), dict) or not captured.get("general", {}).get("matchId"):
                        raise ValueError("Unexpected pageProps shape (missing general.matchId)")
                    if not (((captured.get("header") or {}).get("status") or {}).get("finished")):
                        raise StaleFixtureError(
                            team_id,
                            match_id,
                            f"slug {page_url} served an unfinished match "
                            f"({captured['general'].get('matchName')})",
                        )
                except StaleFixtureError as exc:
                    # Not data loss: the fixture belongs to another season and its slug no
                    # longer points at it. Counted and reported, never silently dropped.
                    stale_slugs.append(match_id)
                    logging.warning("[match] %s: skipping stale fixture - %s", team_name, exc)
                    continue
                except Exception as exc:
                    raise MatchFetchError(
                        team_id,
                        match_id,
                        f"Failed to parse match details from page HTML: {type(exc).__name__}: {exc}",
                    ) from exc

                # File under the id the payload reports, not the id we asked for: a reused
                # slug can land us on a different match, and the filename must never lie.
                actual_match_id = int(captured["general"]["matchId"])
                if actual_match_id != match_id:
                    logging.warning(
                        "[match] %s: requested id=%s but the page served id=%s (%s). Saving under %s.",
                        team_name, match_id, actual_match_id,
                        captured["general"].get("matchName"), actual_match_id,
                    )
                filepath = os.path.join(base_dir, f"{actual_match_id}.json")
                with open(filepath, "w") as f:
                    json.dump(captured, f, indent=2)
                logging.info(f"[match] Saved match id={actual_match_id} -> {filepath}")
                saved_ids.append(actual_match_id)
        except MatchFetchError as exc:
            logging.error("[team] %s: %s", team_name, exc)

        if stale_slugs:
            logging.warning(
                "[team] %s: %d fixture(s) skipped as stale: %s", team_name, len(stale_slugs), stale_slugs
            )
        return saved_ids

def load_saved_match_details(
    season: str | None = None,
    team_filter: Optional[list[str]] = None,
    limit_per_team: Optional[int] = None,
    kinds: Optional[set[MatchKind]] = None,
) -> dict[str, list[MatchDetails]]:
    """Load saved matchDetails JSON files and convert them into `MatchDetails` models.

    Parameters:
    - season: Season directory to read. Defaults to `Season.CURRENT`.
    - team_filter: Restrict to these club directory names. Defaults to every directory present.
    - limit_per_team: Read at most this many match files per club.
    - kinds: Keep only these `MatchKind`s. Defaults to all.

    Returns:
    - Mapping club name -> `MatchDetails` list, sorted by kickoff time.

    Raises:
    - ValueError: on an unknown club directory, or an empty/corrupt match file. Both mean the
      dataset is incomplete, which must surface rather than silently shrink the sample.
    """
    season = season or Season.CURRENT
    base_dir = Path("data") / season / "lineups"
    result: dict[str, list[MatchDetails]] = {}
    if not base_dir.exists():
        return result

    selected_teams = team_filter if team_filter is not None else [d.name for d in base_dir.iterdir() if d.is_dir()]
    for team_name in selected_teams:
        if team_name not in TEAM_NAME_TO_ID:
            raise ValueError(
                f"Unknown team directory '{team_name}' - no matching FotMob team id. "
                f"Add it to FOTMOB_TEAM_IDS in src/fotmob/models/fotmob_metadata.py."
            )
        team_id = TEAM_NAME_TO_ID[team_name]
        team_path = base_dir / team_name
        if not team_path.is_dir():
            continue
        match_files = sorted(team_path.glob("*.json"), key=lambda p: int(p.stem))
        if limit_per_team is not None and limit_per_team >= 0:
            match_files = match_files[:limit_per_team]
        match_list: list[MatchDetails] = []
        for match_file in match_files:
            raw = match_file.read_text()
            if not raw.strip():
                raise ValueError(
                    f"Empty match file {match_file}. A previous capture wrote a truncated snapshot; "
                    f"delete it and re-run the fetcher for this club."
                )
            try:
                match_json = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Corrupt match file {match_file}: {exc}") from exc
            details = _build_match_details(match_json, team_id)
            if kinds is not None and details.kind not in kinds:
                continue
            match_list.append(details)
        match_list.sort(key=lambda d: d.event_time)
        result[team_name] = match_list
    return result


def main():
    """CLI entry point for capturing FotMob match details.

    Examples:
        uv run -m src.fotmob.load                        # every club, current season
        uv run -m src.fotmob.load --team 'Coventry'      # one club by name
        uv run -m src.fotmob.load --season 2025-2026     # backfill an earlier season
    """
    import argparse

    parser = argparse.ArgumentParser(description="Capture FotMob match details for a season's clubs")
    parser.add_argument("--team-id", type=int, help="Specific FotMob team id to process")
    parser.add_argument("--team", type=str, help="Specific club name as used in data/<season>/lineups/")
    parser.add_argument("--matches-limit", type=int, default=None, help="Load earliest N new matches only")
    parser.add_argument("--no-headless", action="store_true", help="Run browser in visible mode")
    parser.add_argument(
        "--season", type=str, default=Season.CURRENT,
        help=f"Season directory name (default: {Season.CURRENT})",
    )

    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)

    season_teams = teams_for_season(args.season)
    if args.team_id and args.team:
        raise SystemExit("Pass either --team-id or --team, not both.")
    if args.team:
        if args.team not in TEAM_NAME_TO_ID:
            raise SystemExit(
                f"Unknown club '{args.team}'. Known clubs: {', '.join(sorted(TEAM_NAME_TO_ID))}"
            )
        team_ids = [TEAM_NAME_TO_ID[args.team]]
    elif args.team_id:
        team_ids = [int(args.team_id)]
    else:
        team_ids = list(season_teams.keys())

    async def _run():
        async with FotMobClient(headless=not args.no_headless) as client:
            total_saved = 0
            for team_id in team_ids:
                if team_id not in season_teams:
                    raise SystemExit(
                        f"FotMob team id {team_id} is not in the {args.season} Premier League roster. "
                        f"Check SEASON_TEAMS in src/fotmob/models/fotmob_metadata.py."
                    )
                team_name = season_teams[team_id]
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
