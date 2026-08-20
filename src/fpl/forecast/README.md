## Overview
Forecast provides simple, composable models to predict fixture- and player‑level outcomes using recent form, season aggregates, and fixture difficulty. Outputs are `Aggregate` values that upstream code can combine, compare, and evaluate with loss functions.

**Scope note.** This is the *in-season* pipeline: every model here reads `Season` aggregates and needs played gameweeks to work from. The pre-season and multi-gameweek horizon path is a separate package, `src/fpl/projection/`, which projects from last season's totals plus pre-season friendlies and writes immutable run artifacts. They share nothing but the immutable models, deliberately — see `src/fpl/projection/README.md`.

Two known problems with the models below, measured in `docs/prediction_roadmap.md`: `PlayerPointsSimpleModel` covers 45.5% of the scoring surface (it has no appearance, bonus, saves or negatives term), and `Player.dc_points` treats defensive contribution as linear when it is a step function. `src/fpl/projection/scoring.py` has the corrected, verified scoring rules; this package has not yet been migrated onto them.

## Key Concepts
- **Aggregate outputs**: Predictions return `Aggregate` (point estimate `p` plus implicit sample size `count`) from `src/fpl/aggregate.py`.
- **Season context**: All models depend on `Season` in `src/fpl/models/season.py` for team/player stats, including form windows and FDR splits.
- **Form windows**: Team models commonly use last‑N form (e.g., own last 3). Player models use `last_n_weeks` (default 5).
- **FDR scaling**: Many models scale predictions by fixture difficulty (home/away specific).
- **Composition**: Higher‑level predictions combine simple sub‑models using weighted averages (`wa`) or sample‑weighted averages (`swa`).

## Components
- **Fixture (team) models** in `src/fpl/forecast/models.py`:
  - `FixtureModel`: base with `predict` and `predict_for_team`.
  - Clean sheets:
    - `CleanSheetModel` (base)
    - `SeasonAvgCleanSheetModel` — season average CS
    - `Last5CleanSheetModel` — last 5 matches form
    - `AllAndFormCleanSheetModel` — `swa` of season avg and form
    - `AvgFDRCleanSheetModel` — FDR‑bucket average
    - `AvgSeasonAndFDRCleanSheetModel` — `swa` of season avg and FDR
    - `UltimateCleanSheetModel` — 0.6×FDR + 0.4×side/total weighted
  - Expected goals:
    - `XGModel` (base)
    - `SimpleXGModel` — team xG form scaled by FDR (team or league backfill)
  - Expected assists:
    - `XAModel` (base)
    - `SimpleXAModel` — team xA form scaled by FDR (team or league backfill)
  - Defensive contribution:
    - `DCModel` (base)
    - `SimpleDCModel` — provides per‑fixture scale; `predict_for_team` unimplemented
  - Points:
    - `PtsModel` (base)
    - `SimplePtsModel` — provides per‑fixture scale; `predict_for_team` unimplemented

- **Player models** in `src/fpl/forecast/models.py`:
  - `PlayerFixtureModel`: base with `predict` and `_predict`
  - `PlayerCSSimpleModel` — team CS × player minutes share
  - `PlayerXGSimpleModel` — player xG form × team xG scale
  - `PlayerXGUltimateModel` — team xG × player xG share
  - `PlayerXASimpleModel` — player xA form × team xA scale
  - `PlayerXAUltimateModel` — team xA × player xA share
  - `PlayerDCSimpleModel` — player DC form × team DC scale
  - `PlayerPointsSimpleModel` — linear combination of CS/xG/xA/DC using scoring from `Query.player(...)`
  - `PlayerPointsFormNaiveModel` — recent points average
  - `PlayerPointsFormModel` — recent points scaled by team points scale

- **Loss functions** in `src/fpl/forecast/loss.py`:
  - `Loss` — base interface
  - `MAELoss` — mean absolute error
  - `LogLoss` — log loss for probabilities
  - `AvgDiffLoss` — 1 − (avg positive − avg negative)

## Data/Control Flow
1. Build a `Season` with team/player aggregates.
2. Choose fixture‑level models (e.g., `SimpleXGModel`, `SeasonAvgCleanSheetModel`) for team context.
3. Optionally compose with `wa`/`swa` to blend sources (form, FDR, side vs total).
4. For players, instantiate `Player*` models with team models to inject team scale/context.
5. Call `predict` on a `Fixture` (team models) or `PlayerFixture` (player models). Use `Aggregate.p` for the point estimate; combine multiple `Aggregate`s via helpers when needed.
6. Evaluate predictions using a `Loss` implementation.

## Public API
- Team models:
  - `FixtureModel.predict(fixture: Fixture) -> tuple[Aggregate, Aggregate]` (home, away)
  - `FixtureModel.predict_for_team(team_id: int, fixture: Fixture) -> Aggregate`
- Player models:
  - `PlayerFixtureModel.predict(fixture: PlayerFixture) -> Aggregate`
- Loss:
  - `Loss.score(labels: list[float], predictions: list[float]) -> float`

Inputs must be consistent with `Fixture`, `PlayerFixture`, and `Season` from `src/fpl/models/immutable.py` and `src/fpl/models/season.py`. Predictions are real‑valued rates/probabilities, depending on the model.

## Key Paths
- `src/fpl/forecast/models.py`
- `src/fpl/forecast/loss.py`
- `src/fpl/aggregate.py` — `Aggregate`, `swa`, `wa`
- `src/fpl/models/immutable.py` — `Fixture`, `PlayerFixture`, `Query`
- `src/fpl/models/season.py` — season aggregates for teams/players

## Related Docs
- Horizon projection, run artifacts and VORP — see `src/fpl/projection/README.md` (the pre-season sibling of this package, with the verified scoring function).
- Where the points actually are, and what to build next — see `docs/prediction_roadmap.md`.
- Compute pipeline and aggregate composition — see `src/fpl/compute/README.md` (how aggregates are combined and normalized).
- Fotmob loader overview — see `src/fotmob/README.md` (input data sources and normalization).
- Rotation and squad-role basics — see `src/fotmob/rotation/README.md` (how appearance evidence is weighted and turned into squad roles).

