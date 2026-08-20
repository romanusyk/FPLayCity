#!/usr/bin/env bash
# Run a module in this project's environment, whichever one exists.
#
#   ./run.sh -m src.fpl.project --method v1-baseline
#   ./run.sh -m src.web.serve
#   ./run.sh -m pytest -q
#
# `uv run` is the documented way and stays the default when uv is installed, because it is the
# only one that honours uv.lock and .python-version. A plain `.venv/` is accepted as a fallback so
# the project works on a machine without uv. Anything else stops with instructions rather than a
# bare "command not found".
set -euo pipefail

cd "$(dirname "$0")"

if command -v uv >/dev/null 2>&1; then
  exec uv run python "$@"
fi

if [[ -x .venv/bin/python ]]; then
  exec .venv/bin/python "$@"
fi

cat >&2 <<'MESSAGE'
No Python environment found for this project.

Either install uv, which is what the docs assume and the only option that honours uv.lock
and .python-version:

    brew install uv
    uv sync

or create a plain virtualenv, which works but does not respect the lockfile:

    python3 -m venv .venv
    .venv/bin/pip install -e .
    .venv/bin/playwright install chromium   # only needed for src.fotmob.load

Then re-run this command.
MESSAGE
exit 1
