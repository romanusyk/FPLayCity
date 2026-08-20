from __future__ import annotations

import logging
import re
import unicodedata
from bisect import bisect_right

from src.fotmob.models.fotmob import MatchDetails
from src.fotmob.models.fotmob_metadata import (
    FPL_SHORT_NAMES,
    TEAM_NAME_TO_ID,
    teams_for_season,
)
from src.fpl.loader.utils import Season
from src.fpl.models.immutable import Query, Player, Gameweek
from src.fpl.models.rotation import RotationAnalyzer, GwMapper
from src.fotmob.rotation.rotation_config import (
    RotationConfig,
    PlayerMappingOverride,
    PLAYER_MAPPING_OVERRIDES,
)
from src.fotmob.rotation.rotation_view import PlayerSquadRole, RivalStartHint


def build_gameweek_mapper(gameweeks: list[Gameweek]) -> GwMapper:
    """
    Return a mapper that assigns each event timestamp to the next gameweek deadline.

    Raises:
        ValueError: When no deadlines are available, ensuring we never silently mislabel matches.
    """
    deadlines = sorted(gw.deadline_time for gw in gameweeks)
    if not deadlines:
        raise ValueError("Gameweek deadlines are missing")

    def mapper(event_time):
        return bisect_right(deadlines, event_time)

    return mapper


MIN_MATCH_SCORE = 5.0
"""Minimum `_match_score` for a candidate to be considered at all.

Sharing only a first name scores 4 (one common token, +3 for the first token matching), which
is not a match - it is three academy players called Josh tying with each other. Any real match
agrees on a surname, which scores at least 6. Raising the floor above 4 turns those spurious
ties into an honest "no candidate" instead of an ambiguity error.
"""

MIN_GLOBAL_MATCH_SCORE = 9.0
"""Minimum `_match_score` for the *global roster* fallback.

The team-scoped pass can trust a surname: two players called King rarely share a dressing
room. Across all 587 elements they do, and "George King" of Coventry's academy scores 6
against both Tom King and Josh King. The global pass therefore demands agreement on more than
the surname alone - a first name too, or a full prefix match - which real cross-club cases
(a player who moved after the fixture was played) comfortably clear.
"""


def fpl_team_id_to_fotmob_name(season: str | None = None) -> dict[int, str]:
    """Map this season's FPL team ids onto our FotMob club names.

    Derived, never hardcoded. FPL renumbers teams alphabetically every season - 16 of the 20
    ids changed meaning between 2025/26 and 2026/27, and id 3 went from Burnley to
    Bournemouth - so a literal table is wrong the moment a club is promoted or relegated. The
    only stable join is `short_name`, which is what `FPL_SHORT_NAMES` records.

    Raises:
    - ValueError: if a club in the live `Teams` collection has no FotMob counterpart, or if
      our season roster names a club the FPL bootstrap does not. Either way the lineups for
      that club would silently go missing.
    """
    season = season or Season.CURRENT
    fotmob_by_short_name = {
        FPL_SHORT_NAMES[name]: name for name in teams_for_season(season).values()
    }
    mapping: dict[int, str] = {}
    unknown: list[str] = []
    for team in Query.all_teams():
        fotmob_name = fotmob_by_short_name.pop(team.short_name, None)
        if fotmob_name is None:
            unknown.append(team.short_name)
            continue
        mapping[team.team_id] = fotmob_name
    if unknown or fotmob_by_short_name:
        raise ValueError(
            f"FotMob roster for {season} does not line up with the loaded FPL teams.\n"
            f"  FPL clubs with no FotMob entry: {sorted(unknown)}\n"
            f"  FotMob clubs not in the FPL bootstrap: {sorted(fotmob_by_short_name)}\n"
            f"Update SEASON_TEAMS/FPL_SHORT_NAMES in src/fotmob/models/fotmob_metadata.py."
        )
    return mapping


class FotmobAdapter:
    """Bridges FotMob match data with FPL entities and exposes rotation insights."""
    def __init__(
        self,
        match_details_by_team_name: dict[str, list[MatchDetails]],
        rotation_config: RotationConfig,
        gw_mapper: GwMapper,
        overrides: list[PlayerMappingOverride] | None = None,
        season: str | None = None,
        allow_unmatched: bool = False,
    ):
        """
        Index matches and derive FotMob↔FPL mappings for downstream rotation analysis.

        Parameters:
        - season: season whose club roster the FPL team ids belong to. Defaults to
          `Season.CURRENT`. Passing the wrong one silently maps clubs to each other's lineups.
        - allow_unmatched: when True, a FotMob player with no FPL counterpart at all is
          recorded in `unmatched_players` and logged instead of raising. Set this only for
          pre-season friendlies, where academy players who are not FPL elements appear in
          every lineup. An *ambiguous* match still raises either way - that is a data problem,
          not an absence.

        Raises:
            ValueError: When required teams, players, or deadlines are missing so data would be incomplete.
        """
        self._config = rotation_config
        self._season = season or Season.CURRENT
        self._allow_unmatched = allow_unmatched
        self.unmatched_players: list[tuple[int, str]] = []
        self._allowed_leagues = set(rotation_config.included_leagues or [])
        self._matches_by_team = self._convert_team_keys(match_details_by_team_name)
        self._team_mapping = self._build_team_mapping()
        self._rotation_analyzer = RotationAnalyzer(self._matches_by_team, rotation_config, gw_mapper)
        self._overrides = overrides or PLAYER_MAPPING_OVERRIDES
        self._fotmob_to_fpl: dict[int, int] = {}
        self._fpl_to_fotmob: dict[int, int] = {}
        self._global_name_index = {
            player.player_id: self._player_token_variants(player)
            for player in Query.all_players()
        }
        self._build_player_mappings()

    def _convert_team_keys(self, by_name: dict[str, list[MatchDetails]]) -> dict[int, list[MatchDetails]]:
        """
        Replace FotMob team-name keys with ids, erroring on unknown names and filling gaps.

        Raises:
            ValueError: When FotMob metadata is incomplete so teams cannot be mapped reliably.
        """
        result: dict[int, list[MatchDetails]] = {}
        for team_name, matches in by_name.items():
            if team_name not in TEAM_NAME_TO_ID:
                raise ValueError(f"Unknown FotMob team '{team_name}'")
            result[TEAM_NAME_TO_ID[team_name]] = matches
        for fotmob_team_id in teams_for_season(self._season):
            result.setdefault(fotmob_team_id, [])
        return result

    def _build_team_mapping(self) -> dict[int, int]:
        """
        Map this season's FPL team ids to FotMob team ids.

        Raises:
            ValueError: When a FotMob id is missing so downstream lookups cannot be trusted.
        """
        return {
            fpl_team_id: TEAM_NAME_TO_ID[fotmob_name]
            for fpl_team_id, fotmob_name in fpl_team_id_to_fotmob_name(self._season).items()
        }

    def _build_player_mappings(self):
        """Populate FotMob↔FPL dictionaries, respecting overrides and ambiguity checks."""
        override_by_fotmob_id = self._index_overrides()

        for fpl_team_id, fotmob_team_id in self._team_mapping.items():
            fpl_players = Query.players_by_team(fpl_team_id)
            if not fpl_players:
                raise ValueError(f"No FPL players found for team {fpl_team_id}")
            name_index = {
                player.player_id: self._global_name_index[player.player_id]
                for player in fpl_players
            }
            fotmob_players = self._collect_fotmob_players(fotmob_team_id)
            for fotmob_player_id, fotmob_name in fotmob_players.items():
                fpl_player_id = self._resolve_fpl_player_id_for_fotmob(
                    fpl_team_id,
                    fotmob_team_id,
                    fotmob_player_id,
                    fotmob_name,
                    name_index,
                    override_by_fotmob_id,
                )
                if fpl_player_id is None:
                    continue
                self._fotmob_to_fpl[fotmob_player_id] = fpl_player_id
                self._fpl_to_fotmob[fpl_player_id] = fotmob_player_id

        if self.unmatched_players:
            logging.info(
                "%d FotMob player(s) had no FPL counterpart and were skipped (allow_unmatched=True). "
                "First 10: %s",
                len(self.unmatched_players),
                ", ".join(name for _, name in self.unmatched_players[:10]),
            )

    def _index_overrides(self) -> dict[int, PlayerMappingOverride]:
        """Return overrides keyed by FotMob player id, dropping the ones that have aged out.

        An override names a player by `fpl_player_code`, which is stable across seasons. When
        that code is absent from the loaded squad the player has left the league, so the
        override no longer applies. That is a correct skip, and it is logged and counted -
        silently dropping it would hide a typo in the code just as effectively as a transfer.

        Raises:
        - ValueError: on two overrides for the same FotMob player, which cannot both be right.
        """
        indexed: dict[int, PlayerMappingOverride] = {}
        retired: list[str] = []
        for override in self._overrides:
            existing = indexed.get(override.fotmob_player_id)
            if existing is not None and existing != override:
                raise ValueError(
                    f"Two different overrides for FotMob player {override.fotmob_player_id}: "
                    f"{existing!r} and {override!r}. Remove one in "
                    f"src/fotmob/rotation/rotation_config.py."
                )
            if not override.ignore and Query.player_by_code(override.fpl_player_code) is None:
                retired.append(f"{override.note or override.fotmob_player_id}")
                continue
            indexed[override.fotmob_player_id] = override
        if retired:
            logging.info(
                "%d player mapping override(s) skipped - not in the %s squad: %s",
                len(retired), self._season, ", ".join(retired),
            )
        return indexed

    def _resolve_fpl_player_id_for_fotmob(
        self,
        fpl_team_id: int,
        fotmob_team_id: int,
        fotmob_player_id: int,
        fotmob_name: str,
        name_index: dict[int, list[list[str]]],
        override_by_fotmob_id: dict[int, PlayerMappingOverride],
    ) -> int | None:
        """Resolve a single FotMob player into an FPL id, honoring overrides and conflict checks."""
        override = override_by_fotmob_id.get(fotmob_player_id)
        if override:
            if override.ignore:
                return None
            if override.fpl_player_code is None:
                raise ValueError(
                    f"Override for FotMob player '{fotmob_name}' ({fotmob_player_id}) "
                    "must specify fpl_player_code when ignore=False."
                )
            # _index_overrides has already dropped codes that are not in this season's squad.
            fpl_player_id = Query.player_by_code(override.fpl_player_code).player_id
        else:
            fpl_player_id = self._match_fotmob_player(
                fpl_team_id,
                fotmob_team_id,
                fotmob_player_id,
                fotmob_name,
                name_index,
                self._global_name_index,
            )

        if fpl_player_id is None:
            return None

        existing = self._fotmob_to_fpl.get(fotmob_player_id)
        if existing and existing != fpl_player_id:
            raise ValueError(
                f"Conflicting mappings for FotMob player {fotmob_name} ({fotmob_player_id}): "
                f"{existing} vs {fpl_player_id}"
            )
        return fpl_player_id

    def _collect_fotmob_players(self, fotmob_team_id: int) -> dict[int, str]:
        """Aggregate every FotMob player that appeared for a team within allowed leagues."""
        players: dict[int, str] = {}
        for match in self._matches_by_team.get(fotmob_team_id, []):
            if self._allowed_leagues and match.league_name not in self._allowed_leagues:
                continue
            for player in match.starters + match.benched + match.unavailable:
                if player.id == 0:
                    continue
                players[player.id] = player.name
            for substitution in match.subs_log:
                players[substitution.player_in.id] = substitution.player_in.name
                players[substitution.player_out.id] = substitution.player_out.name
        return players

    def _match_fotmob_player(
        self,
        fpl_team_id: int,
        fotmob_team_id: int,
        fotmob_player_id: int,
        fotmob_name: str,
        name_index: dict[int, list[list[str]]],
        fallback_index: dict[int, list[list[str]]],
    ) -> int | None:
        """Match one FotMob name to an FPL player using team-first then global search.

        Returns None only when `allow_unmatched` is set and no candidate exists at all.
        """
        tokens = self._tokenize(fotmob_name)
        if not tokens:
            raise ValueError(f"Cannot derive tokens for FotMob player '{fotmob_name}' ({fotmob_player_id})")
        fotmob_team_name = next(name for name, fid in TEAM_NAME_TO_ID.items() if fid == fotmob_team_id)
        fpl_team_name = Query.team(fpl_team_id).name

        player_id = self._resolve_best_match(
            tokens,
            name_index,
            fotmob_name,
            fotmob_player_id,
            context=f"{fotmob_team_name}/{fpl_team_name}",
        )
        if player_id is not None:
            return player_id

        player_id = self._resolve_best_match(
            tokens,
            fallback_index,
            fotmob_name,
            fotmob_player_id,
            context="global roster",
            min_score=MIN_GLOBAL_MATCH_SCORE,
        )
        if player_id is not None:
            logging.info(
                "Mapped FotMob player '%s' (%s) from team %s to FPL player '%s' (%s) in team %s via global roster",
                fotmob_name,
                fotmob_player_id,
                fotmob_team_name,
                Query.player(player_id).full_name or Query.player(player_id).web_name,
                player_id,
                Query.player(player_id).team.name,
            )
            return player_id

        if self._allow_unmatched:
            self.unmatched_players.append((fotmob_player_id, f"{fotmob_name} ({fotmob_team_name})"))
            return None

        raise ValueError(
            f"No candidate FPL player for FotMob player '{fotmob_name}' ({fotmob_player_id}) "
            f"in team {fotmob_team_name}/{fpl_team_name} or global roster. "
            f"Pass allow_unmatched=True if this is pre-season friendly data, where academy "
            f"players who are not FPL elements are expected."
        )

    def _resolve_best_match(
        self,
        fotmob_tokens: list[str],
        index: dict[int, list[list[str]]],
        fotmob_name: str,
        fotmob_player_id: int,
        context: str,
        min_score: float = MIN_MATCH_SCORE,
    ) -> int | None:
        """Return the unique best-scoring FPL candidate, or None when nothing scores well enough."""
        scored_matches: list[tuple[float, int]] = []
        for player_id, variants in index.items():
            score = max((self._match_score(fotmob_tokens, variant) for variant in variants), default=0.0)
            if score >= min_score:
                scored_matches.append((score, player_id))

        if not scored_matches:
            return None

        scored_matches.sort(key=lambda pair: (-pair[0], pair[1]))
        top_score, top_player_id = scored_matches[0]
        if len(scored_matches) > 1 and scored_matches[1][0] == top_score:
            raise ValueError(
                f"Ambiguous mapping for FotMob player '{fotmob_name}' ({fotmob_player_id}) "
                f"in {context}. Top candidates: "
                f"{[Query.player(pid).full_name or Query.player(pid).web_name for _, pid in scored_matches[:3]]}"
            )
        return top_player_id

    @staticmethod
    def _tokenize(name: str) -> list[str]:
        """Normalize a name into lowercase ASCII tokens for fuzzy matching."""
        normalized = unicodedata.normalize("NFKD", name)
        ascii_str = "".join(ch for ch in normalized if not unicodedata.combining(ch))
        ascii_str = ascii_str.encode("ascii", "ignore").decode("ascii")
        return [token for token in re.split(r"[^a-z0-9]+", ascii_str.lower()) if token]

    def _player_token_variants(self, player: Player) -> list[list[str]]:
        """Return tokenized variants for a player (full name + web name) for matching."""
        variants: list[list[str]] = []
        full_tokens = self._tokenize(player.full_name)
        if full_tokens:
            variants.append(full_tokens)
        web_tokens = self._tokenize(player.web_name)
        if web_tokens and web_tokens not in variants:
            variants.append(web_tokens)
        if not variants:
            variants.append([])
        return variants

    @staticmethod
    def _match_score(fotmob_tokens: list[str], fpl_tokens: list[str]) -> float:
        """Score similarity between two token lists; higher scores indicate stronger matches."""
        if not fpl_tokens:
            return 0.0
        fotmob_set = set(fotmob_tokens)
        fpl_set = set(fpl_tokens)
        common = fotmob_set & fpl_set
        if not common:
            return 0.0
        score = len(common)
        if fotmob_tokens[-1] == fpl_tokens[-1]:
            score += 5
        if fotmob_tokens[0] == fpl_tokens[0]:
            score += 3
        elif fotmob_tokens[0][0] == fpl_tokens[0][0]:
            score += 1
        has_prefix_match = len(fpl_tokens) >= len(fotmob_tokens) and fpl_tokens[: len(fotmob_tokens)] == fotmob_tokens
        if has_prefix_match:
            score += 4
        has_suffix_match = len(fpl_tokens) >= len(fotmob_tokens) and fpl_tokens[-len(fotmob_tokens) :] == fotmob_tokens
        if has_suffix_match:
            score += 2
        return score

    def get_fotmob_player_id(self, fpl_player_id: int) -> int:
        """Return the FotMob player id for a given FPL player, raising when unmapped."""
        if fpl_player_id not in self._fpl_to_fotmob:
            raise KeyError(f"No FotMob player mapping for FPL player {fpl_player_id}")
        return self._fpl_to_fotmob[fpl_player_id]

    def get_fpl_player_id_from_fotmob(self, fotmob_player_id: int) -> int:
        """Return the FPL id for a FotMob player, raising when the roster was not indexed."""
        if fotmob_player_id not in self._fotmob_to_fpl:
            raise KeyError(f"No FPL player mapping for FotMob player {fotmob_player_id}")
        return self._fotmob_to_fpl[fotmob_player_id]

    def get_player_squad_role(self, fpl_player_id: int, max_gameweek: int | None) -> PlayerSquadRole:
        """Expose the rotation analyzer’s per-player role view using an FPL identifier."""
        fotmob_player_id = self.get_fotmob_player_id(fpl_player_id)
        return self._rotation_analyzer.get_player_squad_role(fotmob_player_id, max_gameweek)

    def get_rival_start_hint(self, fpl_player_id: int, max_gameweek: int | None) -> RivalStartHint:
        """Return rival substitution insights for a given FPL player id."""
        fotmob_player_id = self.get_fotmob_player_id(fpl_player_id)
        return self._rotation_analyzer.get_rival_start_hint(fotmob_player_id, self._fotmob_to_fpl, max_gameweek)



