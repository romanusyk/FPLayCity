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
- **Evidence a year old stops being the best evidence.** Two weights ramp toward the current
  season as it is played: the minutes blend reaches the recent window in full by five gameweeks
  (`current_season_ramp` in `src/fpl/projection/minutes.py`), and club ratings weigh this season
  equally with last at five matches (`TeamStrength` in `src/fpl/projection/strength.py`). Neither
  constant is fitted - one gameweek cannot fit a ramp - so both are stated judgements with
  single-lever controls. Both are inert before GW1, so every pre-season comparison still holds.
  Measured at one gameweek played: the minutes ramp moves the board a mean 11 places, the ratings
  18. Manchester United's 0-2 at Hull took their attack rating from 1.250 to 1.077 and their
  defence from 1.000 to 1.061, which under `v3-role-trust` it could not do at all.
- **Once the season starts, played gameweeks join that window.** A GW2-11 run reads every match
  before the GW2 deadline, so GW1's real line-ups count at five times a friendly - the same
  mechanism, better evidence. Measured on 2026-08-26 against `v3-role-preseason-only`, which cuts
  the window at the GW1 deadline: mean 43 places of movement, with GW1 starters who were pre-season
  unknowns rising (Dedić 508 -> 209) and GW1 non-starters falling (Mingueza 148 -> 367). The
  direction is right; the *size* is unfitted, because the role curve was fitted on friendlies and
  one gameweek is not a fit. `role_evidence_before_gameweek` in
  `src/fpl/projection/methods.py` is the switch, and the pair of runs is the comparison.

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

### The horizon defaults forward

A run projects ten gameweeks starting at the **next** one, resolved by `resolve_next_gameweek()`.
`--gw-from` and `--gw-to` override either end, and `--gw-to` alone is a full horizon from whatever
start applies, clamped to `MAX_GAMEWEEK`.

This is not cosmetic. The method registry carries `gameweek_from=1`, which was correct in
pre-season and silently wrong from GW2 on, because three separate things key off the start of the
horizon rather than the calendar:

- the points are **summed over the range**, so a GW1-10 run generated in October scores nine
  gameweeks that have already been played;
- `played_gameweeks` is `gameweek_from - 1`, so the `current_season_ramp` that `v4-current-form`
  exists for never engages;
- the role-evidence window falls back to `gameweek_from`, so real line-ups are read as if no match
  had been played.

Before GW1 the next gameweek is 1, so the default reproduces the registry's GW1-10 and every stored
pre-season comparison keeps its meaning. `_with_horizon` in `src/fpl/project.py` is the single
place this is decided, and `tests/test_project_horizon.py` pins it.

## Methods

| Method | What it changes |
|---|---|
| `v4-current-form` | **The default from GW2 2026/27.** Both current-season levers: the minutes blend ramps toward the matches just played, and club ratings blend this season with last. |
| `v4-form-minutes` | Only the minutes ramp. One parameter from `v3-role-trust`. |
| `v4-form-strength` | Only the club ratings. One parameter from `v3-role-trust`. |
| `v3-role-trust` | The do-nothing baseline for both: pre-season weights all season, club ratings a year old. |
| `v3-role-trust-flat` | Its control: one flat pre-season weight for everyone. |
| `v3-role-preseason-only` | Its other control, once the season is under way: role evidence cut off at the GW1 deadline, so played gameweeks are ignored. |
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
