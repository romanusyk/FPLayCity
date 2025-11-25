from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Callable

from src.fotmob.models.fotmob import MatchDetails, FotmobPlayer
from src.fotmob.rotation.rotation_config import RotationConfig
from src.fotmob.rotation.rotation_view import (
    PlayerAppearance,
    PlayerAppearanceStatus,
    PlayerSquadRole,
    RivalStartHint,
    RivalSubDetail,
)

GwMapper = Callable[[datetime], int]
"""Callable mapping a match datetime to its effective FPL gameweek index."""


class RotationAnalyzer:
    """Precomputes squad roles and substitution rivalries from FotMob match logs."""
    def __init__(
        self,
        matches_by_team: dict[int, list[MatchDetails]],
        rotation_config: RotationConfig,
        gw_mapper: GwMapper,
    ):
        """
        Index match data by player so later queries can derive squad roles and rival hints.

        Args:
            matches_by_team: FotMob matches grouped by team id (already filtered to relevant teams).
            rotation_config: Thresholds controlling which leagues count and how rivals are ranked.
            gw_mapper: Callable that converts match kickoff times into effective FPL gameweek numbers.
        """
        self._matches_by_team = matches_by_team
        self._config = rotation_config
        self._gw_mapper = gw_mapper
        self._player_appearances: dict[int, list[tuple[int, PlayerAppearance]]] = defaultdict(list)
        self._rival_events: dict[int, dict[int, list[tuple[int, MatchDetails]]]] = defaultdict(
            lambda: defaultdict(list)
        )
        self._player_names: dict[int, str] = {}
        self._build_indexes()

    def _build_indexes(self):
        """Populate appearance timelines and rival substitution logs for every player."""
        included_leagues = set(self._config.included_leagues)
        for team_matches in self._matches_by_team.values():
            for match in team_matches:
                if included_leagues and match.league_name not in included_leagues:
                    continue
                gw_eff = self._gw_mapper(match.event_time)
                self._register_appearances(match, gw_eff)
                self._register_substitutions(match, gw_eff)

    def _register_appearances(self, match: MatchDetails, gw_eff: int):
        """Record starters, bench players, and unavailable players for a single match."""
        for player in match.starters:
            self._add_appearance(player, PlayerAppearanceStatus.STARTED, match, gw_eff)
        for player in match.benched:
            self._add_appearance(player, PlayerAppearanceStatus.BENCHED, match, gw_eff)
        for player in match.unavailable:
            self._add_appearance(player, PlayerAppearanceStatus.UNAVAILABLE, match, gw_eff)

    def _add_appearance(
        self,
        player: FotmobPlayer,
        status: PlayerAppearanceStatus,
        match: MatchDetails,
        gw_eff: int,
    ):
        """Append one appearance entry to the chronological history for a FotMob player."""
        appearance = PlayerAppearance(
            fotmob_player_id=player.id,
            status=status,
            match=match,
        )
        self._remember_player_name(player)
        self._player_appearances[player.id].append((gw_eff, appearance))

    def _remember_player_name(self, player: FotmobPlayer):
        """Cache a FotMob player's name for later RivalSubDetail display."""
        if player.id not in self._player_names:
            self._player_names[player.id] = player.name

    def _register_substitutions(self, match: MatchDetails, gw_eff: int):
        """Capture substitution rivalries for both directions of every substitution event."""
        for substitution in match.subs_log:
            self._remember_player_name(substitution.player_in)
            self._remember_player_name(substitution.player_out)
            self._add_rival_pair(substitution.player_out.id, substitution.player_in.id, match, gw_eff)
            self._add_rival_pair(substitution.player_in.id, substitution.player_out.id, match, gw_eff)

    def _add_rival_pair(self, primary_id: int, rival_id: int, match: MatchDetails, gw_eff: int):
        """Link a primary player with the rival that swapped places during a substitution."""
        self._rival_events[primary_id][rival_id].append((gw_eff, match))

    def get_player_squad_role(self, fotmob_player_id: int, max_gameweek: int | None) -> PlayerSquadRole:
        """Return cumulative appearance stats for a player up to an optional gameweek."""
        appearances = [
            appearance
            for gw_eff, appearance in self._player_appearances.get(fotmob_player_id, [])
            if max_gameweek is None or gw_eff <= max_gameweek
        ]
        return PlayerSquadRole(
            fotmob_player_id=fotmob_player_id,
            appearances=appearances,
            first_team_threshold=self._config.first_team_start_ratio,
        )

    def get_rival_start_hint(self, fotmob_player_id: int, max_gameweek: int | None) -> RivalStartHint:
        """Summarize which rivals frequently replace the player within the configured window."""
        rival_details: list[RivalSubDetail] = []
        for rival_id, events in self._rival_events.get(fotmob_player_id, {}).items():
            matches = [
                match
                for gw_eff, match in events
                if max_gameweek is None or gw_eff <= max_gameweek
            ]
            if len(matches) < self._config.min_subs_for_rival:
                continue
            if rival_id not in self._player_names:
                raise ValueError(
                    f"No FotMob name recorded for rival player {rival_id}; "
                    "the substitution index is incomplete."
                )
            rival_details.append(
                RivalSubDetail(
                    fotmob_player_id=rival_id,
                    fotmob_name=self._player_names[rival_id],
                    sub_count=len(matches),
                    matches=matches,
                )
            )
        rival_details.sort(key=lambda detail: -detail.sub_count)
        return RivalStartHint(
            player_fotmob_id=fotmob_player_id,
            rivals_sorted=rival_details,
        )

