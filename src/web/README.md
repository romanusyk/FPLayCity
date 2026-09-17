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
  The draft board ranks by value over replacement and hides price; the FPL board ranks by points at
  the horizon you are deciding on, and shows price, ownership and points per million at that same
  horizon.
- **Live draft mode** recomputes replacement level against the undrafted pool. Click the rank
  column to cycle a player between available, mine and taken. Only VORP, rank and tier move;
  projected points come from the run and never change. `mine` and `other` are numerically the
  same — both mean the player is gone — and differ only in the highlight and the counter.
- **A waiver is not a pick.** `#/draft` prices a player against the pool; `#/waivers` prices him
  against *your fifteen*. Those are different numbers, and the second is usually much smaller: the
  game only allows same-position swaps, and a signing who would sit behind your other five gains
  nothing. `src/web/waivers.py` re-picks the best legal eleven for every gameweek in the horizon,
  before and after the swap, and the difference is the whole metric.
- **A transfer is a waiver with a wallet.** `#/transfers` asks the classic-FPL version of the
  waivers question and uses the identical metric, so the two screens cannot drift. What the classic
  game adds narrows the answer three times over: the incoming player must fit `bank + the selling
  price of the man you sell`, no more than three players may come from one club, and every transfer
  past your free allowance costs 4 points. That last one usually decides it - most improvements the
  screen finds are worth less than a hit - which is why `net` sits beside `gain`.
  `src/web/transfers.py`.
- **Two things on `#/transfers` are approximations, and both are stated on the page.** FPL
  publishes an entry's picks only after the deadline, so the squad shown is last gameweek's; and
  the public API never publishes what you paid for a player, so selling prices are approximated by
  current prices, making every budget an upper bound. Neither is a bug to fix - both are properties
  of the API - so they are printed where the numbers are read rather than filed in a doc.
- **Three horizons, one run.** A run stores per-fixture points, so 1, 3 and 5 gameweeks are three
  sums of the same numbers. You decide on one of them and see what that decision costs at the
  others - a claim that wins the next gameweek and loses the next five should not look identical
  to one that wins both. The FPL board offers the same three, over the same weeks, from the same
  parser (`parse_horizons`, `span_gameweeks`, `resolve_sort_horizon` in `src/web/waivers.py`): one
  definition, so "3 GW" cannot come to mean two windows on two screens. The horizons are session
  state, not per-page, so changing them on one screen changes them on the other.
- **The FPL board knows which fifteen are yours.** `data/fpl_entry.json` names our classic-FPL
  entry and `data/<season>/fpl_managers/<entry>/picks/` holds what it submitted, so every row is
  tagged `mine` or not: the `show` filter cuts the board to your squad (who to drop) or to
  everyone else (your transfer market), and the squad card values your best legal XI over the
  deciding horizon. Two things it says out loud rather than assuming. It is **last gameweek's**
  squad, because FPL publishes picks only after a deadline and the upcoming week's is not
  readable at all. And the XI value does not double a captain or model auto-subs, so it is the
  squad's worth rather than a predicted score. With no team connected the board renders exactly as
  before, minus the filter, and says which command connects one.
- **Rank always means the deciding horizon.** On the FPL board the `#` column, `per GW` and `pts/£`
  all follow it, and clicking another horizon column re-decides rather than sorting one column by a
  horizon the rest of the row does not use. What does *not* follow it is `Proj` and every component
  column: a run stores points per fixture but components only as totals, so those stay run-range
  and the page says so above the table rather than pretending to slice them.
- **Two modes, because "what should I do" and "who is out there" are different questions.** The
  claims mode ranks swaps by what they add to your eleven; the all-players mode is the plain board,
  ranked by points, scoped to *available to me* (yours plus free agents), *mine*, *all* or *free
  agents*. Rivals' players are shown greyed in the *all* scope - only a trade reaches them. The
  claims list is capped at `limit` rows; the player list never is.
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
- `waiver_board`, `SquadValuation`, `parse_horizons` in `src/web/waivers.py` - what a free agent
  would add to your own squad, over up to three horizons at once. Its horizon helpers
  (`parse_horizons`, `span_gameweeks`, `resolve_sort_horizon`, `horizon_totals`,
  `default_horizons_for`) are shared with `/api/board`, so both screens validate and slice horizons
  the same way. Reads run rows plus the league
  ownership map; projects nothing.
- `transfer_board`, `net_after_hits` in `src/web/transfers.py` - what an affordable, legal
  classic-FPL transfer would add to your own fifteen. Reuses `SquadValuation` from
  `src/web/waivers.py` rather than restating the metric; adds the budget, the three-per-club cap
  and the hit. One row per incoming player, paired with the sale that gains most at the deciding
  horizon. Raises rather than guessing when the stored picks carry no bank.
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
| `#/waivers` — claims | Which free agent to claim and who to drop, over three horizons at once |
| `#/waivers` — all players | Every player's points at those horizons, filtered by who owns him: the board you pick an eleven from |
| `#/fpl` | Who to buy, by points at three horizons at once, price, points per million and ownership — filterable to your own fifteen, or to everyone but them |
| player panel | Why this number — component waterfall, inputs, sample sizes, and a game log: every stored match with the scoreline, minutes, points, goals conceded and defensive actions |
| `#/transfers` | Which classic-FPL transfer to make and who to sell, priced against your own fifteen, after the budget, the three-per-club cap and the hit |
| `#/compare` | What a method change moved, and which parameters differ |
| `#/calibration` | Whether it is actually better, per component, against a baseline |
| `#/feedback` | Everything you have disagreed with, and what the model said at the time |
| `#/glossary` | Every column and metric in plain words, grouped — the hover hints in one page |

# Public API

`GET /api/docs` serves the generated OpenAPI page.

| Route | Notes |
|---|---|
| `GET /api/config` | Season, games, gameweek, method registry, clubs, feedback reasons, the connected draft league and the connected classic-FPL team (`fpl_entry`, with its squad or a note saying why there is not one yet) |
| `GET /api/runs?game=` | Stored runs, newest first |
| `GET /api/board?game=&run_id=&live=&picks_until_next_turn=&horizons=1,3,5&sort_horizon=` | Slim sortable rows, each carrying `horizon_points`, `horizon_blanks`, `horizon_per_gameweek` and `horizon_per_million` per requested horizon. An FPL board ranks on `sort_horizon` and carries a `squad` block plus a per-row `squad` tag (slot, captaincy, bench) for our own fifteen; a draft board still ranks on VORP, which is priced over the whole run, and has neither. `live` recomputes draft VORP and adds `drop_next` plus the `waiting` block. 400 for a horizon the run is too short for; omitting `horizons` falls back to the defaults that fit |
| `GET /api/player?player_id=&game=&run_id=` | Full row plus per-match history and feedback |
| `GET /api/compare?game=&a=&b=` | Rank deltas, risers, fallers, parameter diff |
| `GET`/`POST /api/feedback` | Read and record disagreements |
| `GET /api/draft/state`, `POST /api/draft/pick`, `POST /api/draft/reset` | Live draft |
| `GET /api/waivers?run_id=&horizons=1,3,5&sort_horizon=&include_locked=&limit=` | `candidates`: free agents ranked by what they add to your squad (capped at `limit`). `players`: every projected player with his points per horizon and `owner_kind` (`mine`/`rival`/`free`/`locked`), never capped. 409 with the command to run when no draft league is connected |
| `GET /api/transfers?run_id=&horizons=1,3,5&sort_horizon=&free_transfers=1&limit=` | `candidates`: affordable, legal classic-FPL transfers ranked by what they add to your fifteen, one row per incoming player, each carrying `gain`, `net` (after the hit), the `out` it is paired with and the `spare` money left. `squad`: your fifteen with points and starts per horizon. `budget`: bank, squad value and the selling-price caveat. 409 with the command to run when no classic team is connected |
| `GET /api/calibration?game=&run_id=` | Component scores once gameweeks resolve |

`run_id` omitted means the newest run of the **default method** for that game, falling back to the
newest run of any method. Generating a control would otherwise make the control the board every
page load opens on. An unknown or malformed run id is a 404 with
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
        |
        +-- GET /api/waivers -> read run file + data/<season>/draft_league/<id>/element_status
        |                       (who owns whom), then value each swap against your own fifteen
        |
        +-- GET /api/transfers -> read run file + data/fpl_entry.json
                                 + data/<season>/fpl_managers/<entry>/picks/<gw>
                                 (your fifteen and your bank), then price every legal swap
```

# Running it

```bash
./refresh.sh                    # fetch -> FotMob -> ownership -> project both games
./run.sh -m src.fpl.league --entry 12345   # once per season: connect the draft league
./run.sh -m src.fpl.league --refresh       # before each waiver deadline: who owns whom now
./run.sh -m src.web.serve         # http://127.0.0.1:8000
./run.sh -m src.web.serve --port 8123 --season 2025-2026
```

The server does not watch for changes. Generating a new run makes it appear in the run selector
on the next page load; editing Python needs a restart.

# Key Paths

- App: `src/web/serve.py`, `src/web/api.py`, `src/web/context.py`
- Front end: `src/web/static/index.html`
- Draft state: `data/<season>/draft_state.json`
- League ownership: `data/<season>/draft_league.json` and `data/<season>/draft_league/<id>/`
- Feedback: `data/<season>/feedback/gw<NN>/`
- Classic-FPL entry and picks: `data/fpl_entry.json`, `data/<season>/fpl_managers/<entry>/picks/`
- Runs: `data/<season>/runs/<game>/`

# Related Docs

- How a run is produced, and what each component model does — `src/fpl/projection/README.md`
- Why the app exists in this shape, plus the measurements behind it — `docs/webapp_plan.md`
- What to model next — `docs/prediction_roadmap.md`
- Where ownership comes from, and why its ids are season-scoped — `src/fpl/loader/README.md`
- Tests, including the HTTP ones — `tests/README.md`
