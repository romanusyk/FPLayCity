# Overview

A localhost review app for projections. It is not a dashboard — it is one half of an evaluation
loop: a method changes, a run is regenerated, you see what moved and why, you record where it is
wrong, and when the gameweek resolves we find out who was right.

Three requirements drive every decision below, and they come from
`docs/webapp_plan.md`:

- **Comparison is primary.** One list of numbers cannot tell you whether a change helped.
- **Every number must be explainable.** No bare projections — each figure shows its components,
  its inputs and its sample size.
- **Disagreement must be capturable and scoreable**, or feedback stays in chat and evaporates.

# Key Concepts

- **The app never projects.** `./run.sh -m src.fpl.project` writes an immutable run file; the app
  reads run files. That is what makes comparison a diff, pages instant and runs reproducible. The
  one exception is live-draft VORP, which is arithmetic over an existing run.
- **Two games, two runs.** Draft and FPL are projected separately, so their methods can diverge.
  The draft board ranks by value over replacement and hides price; the FPL board ranks by points
  and shows price, ownership and points per million.
- **Live draft mode** recomputes replacement level against the undrafted pool. Click the rank
  column to cycle a player between available, mine and taken. Only VORP, rank and tier move;
  projected points come from the run and never change. `mine` and `other` are numerically the
  same — both mean the player is gone — and differ only in the highlight and the counter.
- **Two different questions, two metrics.** VORP is *is this player valuable* and is deliberately
  stable — taking a star removes one from the pool and one from the picks still to come, so the
  last man standing does not move. `Δnext` and the cost-of-waiting cards are *what does this pick
  cost*, and they move on every pick. `src/web/opportunity.py`, and its docstring explains why
  both are needed.
- **Every label explains itself.** `HELP` in `src/web/static/index.html` holds one plain-English
  explanation per column, component and input — what it means and how it is worked out. Anything
  with an entry is drawn with a dotted underline and answers on hover; `#/glossary` prints the
  same text grouped, so it can be read straight through. A new column must be added to `HELP` in
  the same change, for the same reason a number must show its sample size: an unexplained column
  is an unexplained number.
- **Feedback is labelled data.** Each entry stores the run id and the model's numbers next to
  yours, which is what makes it scoreable once results land.
- **Localhost only.** Single user, no authentication, bound to 127.0.0.1. The app writes files.

# Components

- `create_app` in `src/web/serve.py` — FastAPI app plus the CLI entry point.
- `router` in `src/web/api.py` — every route; `_live_replacement_levels` is the one piece of
  live computation.
- `AppContext` in `src/web/context.py` — collections, per-match history and feedback, loaded once
  at startup.
- `DraftState`, `load`, `save` in `src/web/draft_state.py` — the only mutable state.
- `next_best_drop`, `wait_costs`, `picks_between_turns` in `src/web/opportunity.py` — the cost of
  a single pick, as opposed to a player's season value. Pure arithmetic over run rows.
- `score_run` in `src/web/calibration.py` — scores a run against resolved gameweeks, per
  component, against a stated naive baseline.
- `src/web/static/index.html` — the whole front end. Vanilla JS, no build step, light and dark.

Run artifacts and feedback persistence live with the projector, in
`src/fpl/projection/artifacts.py` and `src/fpl/projection/feedback.py`, because the CLI writes
them and the app only reads. This differs from the file layout sketched in `docs/webapp_plan.md`
section 6, which put both under `src/web/`.

# Screens

| Route | What it answers |
|---|---|
| `#/draft` | Who to pick next, by VORP, with tiers, replacement levels and live picks |
| `#/fpl` | Who to buy, by points, price, points per million and ownership |
| player panel | Why this number — component waterfall, inputs, sample sizes, match history |
| `#/compare` | What a method change moved, and which parameters differ |
| `#/calibration` | Whether it is actually better, per component, against a baseline |
| `#/feedback` | Everything you have disagreed with, and what the model said at the time |
| `#/glossary` | Every column and metric in plain words, grouped — the hover hints in one page |

# Public API

`GET /api/docs` serves the generated OpenAPI page.

| Route | Notes |
|---|---|
| `GET /api/config` | Season, games, gameweek, method registry, clubs, feedback reasons |
| `GET /api/runs?game=` | Stored runs, newest first |
| `GET /api/board?game=&run_id=&live=&picks_until_next_turn=` | Slim sortable rows; `live` recomputes draft VORP and adds `drop_next` plus the `waiting` block |
| `GET /api/player?player_id=&game=&run_id=` | Full row plus per-match history and feedback |
| `GET /api/compare?game=&a=&b=` | Rank deltas, risers, fallers, parameter diff |
| `GET`/`POST /api/feedback` | Read and record disagreements |
| `GET /api/draft/state`, `POST /api/draft/pick`, `POST /api/draft/reset` | Live draft |
| `GET /api/calibration?game=&run_id=` | Component scores once gameweeks resolve |

`run_id` omitted means the newest run for that game. An unknown or malformed run id is a 404 with
the same message the CLI would print — never an empty table.

# Data/Control Flow

```
./run.sh -m src.fpl.project        ->  data/<season>/runs/<game>/<run_id>.json   (immutable)
./run.sh -m src.web.serve
        |
        +-- startup: load_from_snapshots() + build_player_histories() + load_feedback()
        |
        +-- GET /api/board   -> read run file, slim it, sort, tier
        |                       (draft + live: recompute replacement level vs draft_state.json)
        +-- GET /api/player  -> read run file + in-memory history
        +-- POST /api/feedback -> data/<season>/feedback/gw<NN>/<timestamp>_<player_id>.json
        +-- POST /api/draft/pick -> data/<season>/draft_state.json
```

# Running it

```bash
./refresh.sh                    # fetch -> FotMob -> project both games
./run.sh -m src.web.serve         # http://127.0.0.1:8000
./run.sh -m src.web.serve --port 8123 --season 2025-2026
```

The server does not watch for changes. Generating a new run makes it appear in the run selector
on the next page load; editing Python needs a restart.

# Key Paths

- App: `src/web/serve.py`, `src/web/api.py`, `src/web/context.py`
- Front end: `src/web/static/index.html`
- Draft state: `data/<season>/draft_state.json`
- Feedback: `data/<season>/feedback/gw<NN>/`
- Runs: `data/<season>/runs/<game>/`

# Related Docs

- How a run is produced, and what each component model does — `src/fpl/projection/README.md`
- Why the app exists in this shape, plus the measurements behind it — `docs/webapp_plan.md`
- What to model next — `docs/prediction_roadmap.md`
- Tests, including the HTTP ones — `tests/README.md`
