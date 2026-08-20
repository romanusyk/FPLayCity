# Overview

Projects FPL points for every player over a range of gameweeks and writes the result as an
immutable run artifact. This is the pre-season and horizon path; `src/fpl/forecast/` remains the
in-season form pipeline that needs played gameweeks to work from.

Two things distinguish it from a single scoring model. First, every projection is a **sum of
named components** — appearance, goals, clean sheets, defensive contribution and the rest — each
traceable to a component model, so a number can be audited rather than trusted. Second, a run is
a **file, not a computation**: the web app reads artifacts and never projects, which makes
comparing two methods a diff.

# Key Concepts

- **Appearance dominates.** 56% of all points awarded in 2025/26 were appearance points, and
  everything else is conditional on minutes. `minutes.py` is therefore the first model, not an
  afterthought. See `docs/prediction_roadmap.md`.
- **Step functions, not averages.** Defensive contribution, saves and goals conceded are floor
  functions on a count. `E[floor(saves / 3)]` is not `E[saves] / 3` — `poisson.py` exists because
  the shortcut costs real points.
- **Shrinkage everywhere, with the weight measured.** Hit rates, per-90 rates and club ratings are
  all pulled toward a prior. Each module documents where its shrinkage constant came from.
- **Absence is not zero, and it is not average either.** A player with no prior Premier League
  season gets position averages *scaled by his club's attack rating*, plus a `no_prior_season`
  flag and `sample_minutes=0`. The plain league average made a promoted club's untested striker
  the fifth-best pick on the board.
- **Trust scales with evidence.** A club with two stored pre-season matches does not get its
  start shares believed as firmly as a club with six, and one competitive fixture is worth full
  trust on its own.
- **What pre-season tells you depends on who the player is.** For a nailed starter it is rest;
  for a squad player it is the manager choosing. So the blend weight is a curve over last
  season's start share — low at both ends, ~1.0 in the middle — not one number for everyone.
  Bruno Fernandes, 35 starts and one pre-season start, went from `p_start` 0.47 to 0.71 and
  from 46th on the board to 7th. Movers are exempt: for them pre-season is the only observation
  of the squad they are now in.
- **Value is measured against who would start instead of you.** Replacement level uses starting
  slots (1/4/4/2), not roster slots — your second keeper scores you nothing, so pricing a
  starting keeper against a backup inflates every keeper on the board.
- **Methods are named.** A projection is `v3-role-trust` or `v0-raw-dc`, not "the model". Controls
  exist so the value of a modelling decision can be measured rather than asserted.
- **A transfer resets a player's standing.** Last season's start share describes last season's
  squad. Nailed starters who moved to a stronger club realised 0.40 of a GW1-5 start share
  against 0.74 for those who stayed, so the prior-season term is discounted for movers.
- **Pre-season is not only friendlies.** The Community Shield and Super Cup are the only
  fixtures before GW1 where a big club picks a real XI, and they are weighted five times a
  friendly.

# Components

Evidence and shared maths:

- `score_player_match`, `MatchScore` in `src/fpl/projection/scoring.py` — the FPL rules, verified
  against all 23,165 stored player-matches.
- `build_player_histories`, `PlayerHistory`, `MatchRow` in `src/fpl/projection/history.py` —
  per-match rows joined across seasons on element `code`.
- `poisson.tail`, `poisson.expected_floor_div` in `src/fpl/projection/poisson.py`.

Component models:

- `MinutesModel`, `MinutesEstimate`, `preseason_weight_for_prior`, `transfer_role_multiplier` in
  `src/fpl/projection/minutes.py` — `p_start` and expected minutes, blending last season with
  pre-season at a weight that depends on both the club's evidence and how nailed the player was,
  and discounting last season for a player who has moved.
- `DefensiveContributionModel`, `DefensiveEstimate` in `src/fpl/projection/defensive.py` —
  empirical-Bayes threshold hit rate.
- `RateModel`, `PlayerRates` in `src/fpl/projection/rates.py` — shrunk xG, xA, saves, bonus, cards.
- `TeamStrength`, `TeamRating` in `src/fpl/projection/strength.py` — attack and defence ratings,
  Poisson clean sheets and concession points.
- `build_preseason_roles`, `PreseasonRole` in `src/fpl/projection/preseason.py` — involvement in
  every match before the gameweek-1 deadline, weighted by whether it was competitive.

Assembly and output:

- `ProjectionEngine`, `PlayerProjection` in `src/fpl/projection/engine.py`.
- `METHODS`, `ProjectionParams` in `src/fpl/projection/methods.py`.
- `replacement_levels`, `value_over_replacement`, `tier_breaks` in `src/fpl/projection/vorp.py`.
- `build_run`, `write_run`, `list_runs`, `prune_runs` in `src/fpl/projection/artifacts.py`.
- `FeedbackEntry`, `save_feedback` in `src/fpl/projection/feedback.py`.

# Data/Control Flow

```
load_from_snapshots(season)              # Teams, Players, Fixtures, prior-season baseline
        |
        v
ProjectionEngine(params, season)
   build_player_histories()              # data/<season>/elements + last season's, joined by code
   TeamStrength()                        # last season's fixtures -> attack/defence ratings
   RateModel(prior seasons)              # position averages, then per-player shrinkage
   build_preseason_roles()               # data/<season>/lineups, everything before the GW1 deadline
        |
        v
   for each player, for each fixture in the horizon:
       appearance + goals + assists + clean sheet + concessions
       + saves + defensive contribution + bonus + cards
        |
        v
replacement_levels() -> value_over_replacement()
        |
        v
build_run() -> write_run() -> data/<season>/runs/<game>/<run_id>.json
        |
        v
prune_runs(keep=N)                       # never deletes a run that feedback refers to
```

# Public API

```bash
./run.sh -m src.fpl.project                          # both games, default method
./run.sh -m src.fpl.project --game draft             # one game
./run.sh -m src.fpl.project --method v0-raw-dc       # a control, to compare against
./run.sh -m src.fpl.project --gw-from 5 --gw-to 14   # a different horizon
./run.sh -m src.fpl.project --keep 5                 # retain fewer runs
./run.sh -m src.fpl.project --list-methods
```

Reads only what is on disk. Refresh the inputs first with `./run.sh -m src.fpl.fetch` and
`./run.sh -m src.fotmob.load`, or run `./refresh.sh` to do the lot.

## Methods

| Method | What it changes |
|---|---|
| `v3-role-trust` | **The default.** Everything on, including the pre-season role curve. |
| `v3-role-trust-flat` | Its control: one flat pre-season weight for everyone. |
| `v2-transfer` | The role curve off (identical to `v3-role-trust-flat`, under the older name). |
| `v1-baseline` | The transfer discount off too. |
| `v0-raw-dc` | Raw defensive-contribution hit rate instead of the shrunk one. |
| `v0-no-preseason` | Last season only — no friendlies, no Community Shield. |
| `v2-transfer-no-preseason` | Transfer discount without the pre-season signal. |

Every method predating the role curve is pinned to `FLAT_ROLE`, so a run generated today under
an old name still means what that name meant when it was coined. Adding a knob to
`ProjectionParams` with a live default silently rewrites every older method otherwise, and every
stored comparison changes its subject.

# Adding a method

1. Add an entry to `METHODS` in `src/fpl/projection/methods.py`, ideally differing from
   `v1-baseline` in exactly one way so the comparison isolates it.
2. If it needs a new knob, add a field to `ProjectionParams` with a default that leaves existing
   methods unchanged. Run artifacts record the full parameter set, so old runs stay reproducible.
3. Generate it and diff: `./run.sh -m src.fpl.project --method <name>`, then the compare screen.

# Known omissions

Stated rather than hidden, each with a measured size:

- Own goals and missed penalties, together about 0.3% of points.
- Set-piece duty is shown as a flag, not modelled. A player's own xG and xA already include the
  set pieces he took last season; the real gap is newly appointed takers.
- Opponent-specific defensive contribution — some sides concede far more tackles than others.
- Bonus is a shrunk per-90 rate rather than a BPS model.

# Key Paths

- Engine and models: `src/fpl/projection/`
- CLI: `src/fpl/project.py`
- Artifacts on disk: `data/<season>/runs/<game>/`

# Related Docs

- Where the points actually are, and the prioritised backlog — `docs/prediction_roadmap.md`
- The review app that reads these artifacts — `src/web/README.md`
- Why the app is built this way, and the measurements behind it — `docs/webapp_plan.md`
- In-season form models — `src/fpl/forecast/README.md`
- Repo conventions and cross-season traps — `CLAUDE.md`
