#!/usr/bin/env bash
# Refresh every input and generate a fresh projection run for both games.
#
#   ./refresh.sh                     # fetch, then project with the default method
#   ./refresh.sh v0-no-preseason     # project with a named method instead
#   SKIP_FETCH=1 ./refresh.sh        # reproject from what is already on disk
#   SKIP_FOTMOB=1 ./refresh.sh       # skip the browser-driven FotMob capture
#   SKIP_NEWS=1 ./refresh.sh         # skip the Scout news fetch and LLM extraction
#
# In-season it also refreshes draft-league ownership, which the waivers page needs, when a
# league is connected (./run.sh -m src.fpl.league --entry <id>).
#
# Our own classic-FPL squad needs no step here: the FPL fetch below already pulls every published
# gameweek's picks when a team is connected (./run.sh -m src.fpl.squad --entry <id>).
#
# The news steps call `claude -p` once per unprocessed article, so they cost real money and a few
# minutes. Extraction is cached per article, so a re-run only pays for articles that are new.
#
# The projection horizon follows the calendar: `src.fpl.project` starts at the next gameweek, so
# this script needs no seasonal edit. Pass --gw-from yourself only to reproduce a past run.
#
# Then: ./run.sh -m src.web.serve
#
# Fails on the first error rather than carrying on with a stale input, because a run that
# silently mixes fresh FPL data with last week's lineups is worse than no run.
set -euo pipefail

cd "$(dirname "$0")"
RUN=(./run.sh)
# No default here on purpose: the method registry owns the default, so this script cannot drift
# from it. Pass a name to override.
METHOD="${1:-}"

# Ownership, when a draft league is connected. Skipped rather than failed when it is not: the
# projection does not need it, only the waivers page does.
if [[ -f "data/$(./run.sh -c 'from src.fpl.loader.utils import Season; print(Season.CURRENT)')/draft_league.json" ]]; then
  echo "==> draft league ownership"
  "${RUN[@]}" -m src.fpl.league --refresh
else
  echo "==> no draft league connected, skipping ownership (./run.sh -m src.fpl.league --entry <id>)"
fi

if [[ -z "${SKIP_FETCH:-}" ]]; then
  echo "==> FPL snapshots"
  "${RUN[@]}" -m src.fpl.fetch
else
  echo "==> skipping FPL fetch (SKIP_FETCH set)"
fi

if [[ -z "${SKIP_FOTMOB:-}" ]]; then
  echo "==> FotMob lineups (needs playwright chromium)"
  "${RUN[@]}" -m src.fotmob.load
else
  echo "==> skipping FotMob capture (SKIP_FOTMOB set)"
fi

if [[ -z "${SKIP_NEWS:-}" ]]; then
  NEXT_GW="$("${RUN[@]}" -c 'from src.fpl.loader.utils import resolve_next_gameweek; print(resolve_next_gameweek())' 2>/dev/null | tail -1)"
  echo "==> Scout news for GW${NEXT_GW} (fetch, extract via claude -p, validate)"
  "${RUN[@]}" -m src.fpl.loader.news.pl fpl_scout --last-gw "${NEXT_GW}"
  "${RUN[@]}" -m src.fpl.loader.news.llm fpl_scout --last-gw "${NEXT_GW}"
  "${RUN[@]}" -m src.fpl.loader.news.validate fpl_scout --last-gw "${NEXT_GW}"
else
  echo "==> skipping news (SKIP_NEWS set)"
fi

if [[ -n "${METHOD}" ]]; then
  echo "==> projecting with method '${METHOD}'"
  "${RUN[@]}" -m src.fpl.project --method "${METHOD}"
else
  echo "==> projecting with the default method"
  "${RUN[@]}" -m src.fpl.project
fi

echo
echo "Done. Serve it with:  ./run.sh -m src.web.serve"
