#!/usr/bin/env bash
# Refresh every input and generate a fresh projection run for both games.
#
#   ./refresh.sh                     # fetch, then project with the default method
#   ./refresh.sh v0-no-preseason     # project with a named method instead
#   SKIP_FETCH=1 ./refresh.sh        # reproject from what is already on disk
#   SKIP_FOTMOB=1 ./refresh.sh       # skip the browser-driven FotMob capture
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

if [[ -n "${METHOD}" ]]; then
  echo "==> projecting with method '${METHOD}'"
  "${RUN[@]}" -m src.fpl.project --method "${METHOD}"
else
  echo "==> projecting with the default method"
  "${RUN[@]}" -m src.fpl.project
fi

echo
echo "Done. Serve it with:  ./run.sh -m src.web.serve"
