# Improving predictions: where the points actually are

Written 2026-08-16. Every number here was measured against this repo's own data, not assumed.
The reproduction snippets assume `uv run python`.

**Status, 2026-08-16.** Priorities 1–5 and the calibration harness are now implemented in
`src/fpl/projection/`, which is a new package alongside the in-season `src/fpl/forecast/`
pipeline rather than a rewrite of it. What each priority actually shipped as is noted inline
below; what is still missing is collected under "Still open" at the end. Start at
`src/fpl/projection/README.md`.

## Outline
1. [The measurement that reframes everything](#1-the-measurement-that-reframes-everything)
2. [What the current model actually covers](#2-what-the-current-model-actually-covers)
3. [Priority 1: a minutes model](#priority-1-a-minutes-model)
4. [Priority 2: complete the scoring function](#priority-2-complete-the-scoring-function)
5. [Priority 3: defensive contribution as a hit rate](#priority-3-defensive-contribution-as-a-hit-rate)
6. [Priority 4: consume the pre-season data we now collect](#priority-4-consume-the-pre-season-data-we-now-collect)
7. [Priority 5: draft is a different game](#priority-5-draft-is-a-different-game)
8. [Supporting work: a calibration harness](#supporting-work-a-calibration-harness)
9. [Still open](#still-open)

## 1. The measurement that reframes everything

Reconstructing all 9,102 player-matches of 2025/26 from `data/2025-2026/elements/*.json` and
decomposing each into its scoring sources (the reconstruction reconciles exactly against
`total_points` — 0 mismatches, so the decomposition is trustworthy):

| Source | Share of all points | GKP | DEF | MID | FWD |
|---|---|---|---|---|---|
| **Appearance** | **56.0%** | 59.5% | 57.5% | 55.1% | 52.8% |
| Clean sheets | 15.0% | 30.6% | 27.6% | 6.5% | 0.0% |
| Goals | 14.0% | 0.0% | 6.7% | 17.3% | 31.5% |
| Defensive contribution | 8.3% | 0.0% | 13.8% | 7.5% | 0.4% |
| Assists | 8.2% | 0.4% | 5.9% | 11.3% | 7.4% |
| Bonus | 7.0% | 6.3% | 5.7% | 7.0% | 11.3% |
| Saves | 1.3% | 16.9% | – | – | – |
| Negatives | −9.9% | −16.3% | −17.1% | −4.8% | −3.4% |

**Fifty-six percent of every FPL point is just showing up.** Goals — the thing every model
obsesses over — are 14%. For a defender, appearance plus clean sheets plus DC is 99% of the
upside and none of it requires an attacking event.

The practical consequence: prediction accuracy is dominated by *did he play, and for how long*,
and every other component is conditional on that. This matches the published work — both
[OpenFPL](https://arxiv.org/pdf/2508.09992) and the
[multi-stream analytics paper](https://arxiv.org/pdf/1912.07441) name minutes/availability
handling as a primary accuracy driver, and note goalkeepers are predicted least accurately
precisely because their output is almost entirely team-and-minutes driven.

## 2. What the current model actually covers

`PlayerPointsSimpleModel` in `src/fpl/forecast/models.py` sums exactly four terms:

```python
cs * player.clean_sheet_points + xg * player.goal_points
    + xa * player.assist_points + dc * player.dc_points
```

Against the table above, that is **45.5% of the scoring surface**. Missing entirely:
appearance (56%), bonus (7%), negatives (−9.9%), saves (1.3% overall but 16.9% of a keeper's
points). It also implicitly assumes every player plays a full match.

Separately, `Player.dc_points` in `src/fpl/models/immutable.py` is `0.1/10 = 0.01` per action
for defenders. A 200-action season therefore scores 2 points, when the real return is roughly
2 points *per match* the threshold is cleared — off by more than an order of magnitude, and
wrong in form as well as scale (see §5).

## Priority 1: a minutes model

**Why:** it is 56% of the points and gates the other 44%.

**What to build:** `p_start` and `E[minutes | start]` per player per gameweek, as a first-class
model with the same shape as the existing `PlayerFixtureModel`s.

Features that are already on disk and currently unused:

- `status` / `chance_of_playing_next_round` / `news` from bootstrap — the official injury and
  suspension feed, refreshed continuously. Higher precision than anything scraped.
- Rolling start streak and minutes trend from `data/<season>/elements/*.json`.
- `selected_by_percent` — millions of managers pricing role security in real time. It moved
  first on Spurs' Dubravka/Kinsky split before any predicted-XI page did.
- Pre-season start share (§4) for the cold-start weeks.
- `PlayerSeason.starts` from the new prior-season baseline, for GW1 when nothing else exists.

`src/fpl/models/red_flags.py` already has the beginnings of this (`MissedLastGame`) but nothing
consumes it. Turn the flag set into a calibrated probability rather than a boolean.

**Acceptance test:** Brier score of `p_start` against actual starts, backtested GW6→38 of
2025/26, beating the naive "started last week" baseline.

**Shipped** as `MinutesModel` in `src/fpl/projection/minutes.py`: `p_start` is a measured blend
of last season's start share and pre-season start share, multiplied by an availability factor
read from `status` and `chance_of_playing_next_round`. Expected minutes, P(60+ | start) and cameo
rates are per-player and shrunk toward measured position defaults.

The blend weight was fitted three times, each time because a named player looked wrong and the
data agreed:

| | correlation | MAE vs actual GW1–5 start share |
|---|---|---|
| last season alone | 0.611 | 0.218 |
| flat blend, `w = 0.60` | 0.758 | 0.191 |
| plus the club-evidence ramp | 0.759 | 0.190 |
| plus the pre-season role curve | 0.760 | **0.180** |

The role curve is the interesting one: the best weight is not monotonic in how nailed a player
was, it is a hump — pre-season barely matters for a first-choice player (rest) or a fringe one
(cheap friendly starts), and almost entirely decides the middle. It cuts error 13% on nailed
starters and 24% on nailed midfielders, and it is what moved Bruno Fernandes from 46th to 7th.

**Still unused:** `selected_by_percent` as a role-security signal, and the rolling start streak
within a season. Both are on disk; neither is in the model.

## Priority 2: complete the scoring function

**Why:** the model is blind to 54.5% of the points.

I have a reconstruction that reconciles exactly across all 9,102 player-matches of 2025/26 —
lift it directly into a single `score_player_match(...)` function so the projection and any
backtest share one definition of FPL scoring:

- appearance: 2 if minutes ≥ 60 else 1 (if minutes > 0)
- goals × {GKP 6, DEF 6, MID 5, FWD 4}; assists × 3
- clean sheet × {GKP 4, DEF 4, MID 1}, only when minutes ≥ 60
- saves // 3; penalties saved × 5
- defensive contribution: +2 when the threshold is met (§5)
- goals conceded // 2 × −1 for GKP/DEF; yellow −1, red −3, own goal −2, penalty missed −2
- bonus

Two of these deserve their own models rather than a constant:

- **Bonus (7%, and 11.3% of a forward's points).** BPS is deterministic given match events, so
  bonus is predictable from the same xG/xA/DC/CS terms already computed, plus minutes. Cheap win.
- **Goals conceded (−17% for GKP and DEF).** Currently unmodelled, and it is the second-largest
  term in a defender's score after appearance. It falls straight out of the expected-goals-
  conceded number the clean-sheet model already computes — a Poisson on the same λ gives both
  `P(CS)` and `E[goals conceded]` for free.

**Shipped** as `score_player_match` in `src/fpl/projection/scoring.py`, verified against all
23,165 stored 2025/26 player-matches with zero mismatches (`tests/test_scoring.py`). Goals
conceded and saves are Poisson expectations of the floor, not the floor of the expectation —
`E[floor(saves/3)]` at 3.0 saves is 0.66, and using 1.0 would have been a three-point error per
keeper over ten gameweeks. Bonus is a shrunk per-90 rate; a proper BPS model is still open.

## Priority 3: defensive contribution as a hit rate

**Why:** DC is 8.3% of all points and 13.8% of a defender's, and the current linear treatment
mis-ranks players. It is a step function: 9 actions score 0, 10 score 2.

Measured over 2025/26, players with 15+ starts, split by position so the differing thresholds
(10 for DEF, 12 for others) don't contaminate the comparison:

| DEF, mean CBIT | players | hit rate min | median | max | spread |
|---|---|---|---|---|---|
| 8–9 | 12 | 36% | 39% | 50% | 14pp |
| 9–10 | 11 | 35% | 44% | 50% | 15pp |
| 10–11 | 8 | 36% | 58% | 67% | **31pp** |

Concrete: **Alderete (mean 10.2, hit 36%) and Ballard (mean 10.6, hit 67%)** are teammates with
effectively the same average and **6.1 DC points apart over 10 starts**. In midfield, Ayari
(10.1, 22%) vs Casemiro (10.8, 48%) is 5.1 points apart. A mean-based model prices these pairs
identically.

**Update (2026-08-16):** raw hit rate is *not* the right estimator either. Splitting each
player's starts into first and second half gives reliabilities of 0.70 (DEF) / 0.74 (MID) for
hit rate against 0.77 / 0.82 for mean actions; splitting odd against even matches, which avoids
conflating a time trend with noise, gives 0.80 / 0.68 against 0.87 / 0.81. Either way the mean is
the more stable quantity, so the answer is empirical-Bayes: shrink observed hit rate toward the
rate implied by the player's per-90 actions and minutes, weighted by sample size. The
minutes-confound control is in `docs/webapp_plan.md` §1.

**Shipped** as `DefensiveContributionModel` in `src/fpl/projection/defensive.py`, with `k = 5`
starts derived from the odd/even reliabilities via Spearman-Brown. The Poisson prior is measured,
not assumed: it correlates 0.96 with observed hit rate and runs about two percentage points low.
`v0-raw-dc` in the method registry turns the shrinkage off so the decision can be compared rather
than argued.

**Still open:** the opponent adjustment. Some sides concede far more tackles, blocks and
interceptions than others, and the model currently treats every fixture as average for defensive
contribution even though it does not for goals or clean sheets.

## Priority 4: consume the pre-season data we now collect

We now fetch pre-season friendlies (105 matches for 2026/27), but nothing aggregates them.

**Pre-season genuinely predicts.** Backtesting 2025/26 — 90 friendlies in July–August 2025
against actual GW1–5 starts:

| Pre-season start share | players | mean GW1–5 starts | started 4 of 5+ |
|---|---|---|---|
| 0.00–0.25 | 12 | 0.17 | 0% |
| 0.25–0.50 | 50 | 1.32 | 10% |
| 0.50–0.75 | 135 | 2.38 | 38% |
| 0.75–1.00 | 103 | 3.60 | 60% |

Cleanly monotonic across the full range. A player starting three quarters of the friendlies is
~20× more likely to nail down a first-team place than one starting under a quarter.

Caveat on this specific measurement: it used crude name matching and resolved only 300 of 701
FotMob players (43%), and the matched subset likely skews to better-known players. The repo's
`FotmobAdapter` does proper tokenized matching with overrides, so real coverage should be
higher — but the effect size is large enough that the direction is not in doubt.

**What to build:**

1. `preseason_minutes(season, team) -> {fpl_player_id: (starts, appearances, minutes)}` — the
   rollup that does not exist yet. It needs the FotMob→FPL identity bridge, which
   `FotmobAdapter` already implements; wire it up rather than rewriting it.
2. Feed that start share into the §1 minutes model as the GW1–5 prior, decaying as real
   gameweeks accumulate.
3. Replace the guessed `RotationConfig.match_kind_weights[FRIENDLY] = 0.35` with a value fitted
   from this backtest. 0.35 was my prior; the data can now supply the number.

Also unused: the `unavailable` list on every `MatchDetails` (40 listings across 2026/27
pre-season). It flagged Coventry's Jack Rudoni before the FPL news field did. It is the one
part of friendly data that is not noisy and it deliberately carries full weight.

**Shipped** as `build_preseason_roles` in `src/fpl/projection/preseason.py`, feeding `p_start`.
Items 1 and 2 are done. Item 3 was answered in a way this section did not anticipate — twice:

- The pre-season blend weight is **not a single fitted number**. It is a curve over last season's
  start share (`PRESEASON_ROLE_KNOTS`), because what a quiet pre-season means depends entirely on
  whether the player was first choice or fighting for a place.
- A pre-season *fixture* weight was fitted, but for match kind rather than the blend:
  `preseason_friendly_weight = 0.20` values a friendly against 1.0 for a competitive pre-season
  fixture. `RotationConfig.match_kind_weights[FRIENDLY]` stays at the guessed 0.35 for the
  in-season rotation view, which is a different question and still unfitted.

Both rest on one summer's data; the caveats are in the "dataset" section of
`src/fpl/projection/minutes.py`. `unavailable` is counted and surfaced but still not a model input.

## Priority 5: draft is a different game

Everything in `src/fpl/forecast/` and `src/fpl/main.py` optimises a classic squad under a price
constraint.
Draft has no prices and no ownership — it has scarcity. Projected points is the wrong sort key.

For the 2026/27 draft, replacement level (the best player at each position who goes undrafted
in a 4-manager league) came out as:

| Position | Replacement pts over GW1–10 |
|---|---|
| GKP | 31.9 |
| DEF | 33.3 |
| MID | **37.5** |
| FWD | **25.7** |

A 40-point forward is worth far more than a 40-point midfielder, because skipping midfield
costs almost nothing. Only 25 forwards in the league have 8+ Premier League starts against 121
midfielders — FPL classifies most attackers as midfielders, so real No.9s are scarce.

**What to build:**

1. `value_over_replacement(projection, managers, slots)` as a first-class function. Trivial code,
   and it is the actual draft-order signal.
2. Waiver/free-agent valuation during the season: the same VORP calculation against the
   currently-unowned pool. `PlayerPresences` already tracks who owns whom.
3. Drop the price constraint from the draft path entirely — `src/fpl/dump/players.py` still keys on it.

**Shipped** as `src/fpl/projection/vorp.py` plus the draft board in `src/web/`. Item 1 is done,
including tier breaks. Item 2 is done in a manual form — the live draft board recomputes
replacement level against whatever pool you mark as taken — but it is not yet driven from
`PlayerPresences`, so in-season waiver valuation still needs wiring to who actually owns whom.
Item 3 is unchanged: `src/fpl/dump/players.py` still keys on price.

One correction to the intuition in this section: taking the four best forwards does *not* raise
replacement level. The pool and the picks remaining both shrink by four, so the same player is
last taken. What raises it is a reach below the line.

## Supporting work: a calibration harness

`src/fpl/forecast/loss.py` has MAE/LogLoss/AvgDiff, but `main.py` only compares total points of
selected squads. That measures the whole pipeline end-to-end and cannot tell you *which*
component is wrong.

**What to build:** a backtest that scores each component separately against actuals —
`p_start` (Brier), clean sheets (log loss), goals/assists (MAE against actual, plus calibration
curves), DC hit rate (Brier), bonus (MAE) — with a fixed naive baseline per component. Any model
change that does not beat its baseline should not ship. The `Season.play()` progressive replay
already prevents data leakage, so the machinery is half-built.

Without this, none of the above can be verified — including the changes proposed here.

**Partly shipped** as `score_run` in `src/web/calibration.py`, surfaced on the app's Calibration
screen. It scores `p_start` (Brier, against a league-average-start-rate baseline) and points
(MAE, against a position-average baseline) per gameweek, and reports "nothing has resolved yet"
rather than drawing an empty chart. Clean sheets, goals and assists, DC hit rate and bonus are
not yet scored separately, and there are no calibration curves.

## Still open

Collected from the sections above, roughly in order of expected value:

1. **Component-level calibration** — clean sheets (log loss), goals/assists (MAE plus curves),
   DC hit rate (Brier), bonus (MAE). Without these, "better" is only measurable in aggregate.
2. **A BPS model for bonus.** 7% of all points and 11.3% of a forward's, currently a shrunk
   per-90 rate. BPS is deterministic given match events, so this is a cheap win.
3. **Squad competition.** The one gap the pre-season role curve makes visible rather than
   closes. The curve keeps a nailed starter's prior on the grounds that his pre-season absence is
   usually rest — but sometimes a summer signing has genuinely taken the place, and nothing in the
   model knows a club bought a player at all. Today those cases are marked with the
   `preseason_role_drop` flag (26 players in 2026/27 GW1) for a human to check. Doing it properly
   means modelling the squad, not the player: minutes available per position per club, and who
   arrived. See "A nailed starter is a different question from a squad player" in
   `src/fpl/projection/minutes.py`.
4. **Opponent adjustment for defensive contribution.**
5. **`selected_by_percent` and start streaks in the minutes model.**
6. **Waiver valuation driven from `PlayerPresences`** rather than hand-marked picks.
7. **Set-piece duty as a model input** rather than a board flag — the real gap is newly appointed
   takers, whose prior-season xG does not include the duty.
8. **A fitted `RotationConfig.match_kind_weights[FRIENDLY]`**, replacing the 0.35 prior.
9. **`src/fpl/dump/players.py` still keys on price** on the draft path.
10. **The mover pre-season weight, fitted alongside the transfer discount.** Leave-one-club-out
    cross-validation picks 1.0 for movers in all 20 folds, against the 0.60 shipped, but the
    harness that says so does not apply `transfer_role_multiplier` — so it is compensating for a
    discount the model already applies, and at 1.0 the discount becomes dead code. Needs one
    harness that fits both terms together. See "A transfer is not portable" in
    `src/fpl/projection/minutes.py`.
11. **A second pre-season on disk.** Only 2025/26 has stored pre-season lineups, so every
    constant in the minutes blend is fitted on one transition and validated by split half.
    Keeping 2026/27's lineups makes the first out-of-sample check possible next August.

## Suggested order (original)

1. Calibration harness (nothing else is measurable without it)
2. Minutes model + complete scoring function (together: 56% + 54.5% of the surface)
3. DC hit rate (small change, immediate ranking effect)
4. Pre-season rollup feeding the minutes prior (matters most in GW1–10, i.e. every redraft)
5. Draft VORP and waiver valuation

## Related docs
- What was built from this roadmap, and how to run it — `src/fpl/projection/README.md`
- The review app that makes model changes reviewable — `src/web/README.md`
- Why that app exists, and the split-half work behind the shrinkage — `docs/webapp_plan.md`
- Data collections and lifecycle — `data/README.md`
- Prior-season baseline, and why bootstrap cannot be trusted for transfers —
  `src/fpl/loader/baseline.py`
- Friendly weighting and squad roles — `src/fotmob/rotation/README.md`
- Repo conventions and known traps — `CLAUDE.md`
