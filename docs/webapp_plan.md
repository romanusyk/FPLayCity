# Plan: a review app for draft and FPL predictions

Status: **built**. Written 2026-08-16, implemented the same day.

This document is kept as the reasoning behind the app, not as a to-do list. For what exists now
and how to run it, read `src/web/README.md` and `src/fpl/projection/README.md`.

## What was built, and where it departs from this plan

All six phases in section 7 shipped, including the calibration harness — though with nothing to
score until a gameweek resolves, it reports that rather than drawing an empty chart.

Three things exist that this plan did not ask for, each added because using the app exposed a gap:

- **A glossary, and a hint on every column.** Section 4 assumed the columns were self-evident.
  They are not — the board carries sixteen of them, several of which are terms of art. Every
  column, component and input now explains itself on hover, and `#/glossary` prints the same text
  grouped. `HELP` in `src/web/static/index.html`.
- **Opportunity cost alongside VORP.** VORP answers "is this player valuable" and is deliberately
  stable — the plan did not notice that this makes it nearly useless for "who do I take *now*",
  which is the question you actually have on the clock. `Δnext` and the cost-of-waiting cards
  answer that one and move on every pick. `src/web/opportunity.py`.
- **A `role drop?` flag.** Where the minutes model deliberately overrides the pre-season signal,
  the board says so, rather than presenting a judgement call as a fact.

The four open questions in section 9 were answered: **two projections** (draft and FPL are
separate runs so their methods can diverge), **live draft mode** (the board recomputes
replacement level against the undrafted pool), **prune to the last N** (`--keep`, defaulting to
20, and never deleting a run that feedback refers to), and **localhost only**.

Three deliberate departures from the sketch below:

1. **Run artifacts and feedback live in `src/fpl/projection/`, not `src/web/`.** The CLI writes
   them and the app only reads, so the app importing the projector is the right direction of
   dependency; the reverse would have made `src/fpl/project.py` import from `src/web`.
2. **The engine is a new `src/fpl/projection/` package rather than an extension of
   `src/fpl/forecast/`.** `forecast/` is the in-season form pipeline and needs played gameweeks;
   the horizon projection has to work from a standing start in August. Mixing them would have
   muddled both.
3. **`src/fpl/views/` was not reused.** Its dataclasses carry pandas `DataFrame`s and are built
   around the in-season prediction pipeline, so they are not JSON-serialisable and do not
   describe what a run artifact needs. Section 6 assumed otherwise.

Two things measured during implementation are worth recording, because both went against the
guess in this document:

- **Sample-size weighting on the pre-season signal buys nothing.** `w * n / (n + k)` peaks at the
  same 0.750 correlation for three different `(k, w)` pairs, so the knob does not exist.
- **"In no friendly squad at all" is a real signal, not missing evidence.** Those 134 players
  averaged a 0.069 GW1–5 start share against a 0.218 prior-season share. Treating the absence as
  "no evidence" drops overall correlation from 0.748 to 0.653.

Original plan follows.

## Outline
1. [First: your objection, tested](#1-first-your-objection-tested)
2. [What this app is actually for](#2-what-this-app-is-actually-for)
3. [Core design decision: runs are artifacts](#3-core-design-decision-runs-are-artifacts)
4. [Screens](#4-screens)
5. [Feedback as labelled data](#5-feedback-as-labelled-data)
6. [Stack and layout](#6-stack-and-layout)
7. [Phases](#7-phases)
8. [Alternatives considered](#8-alternatives-considered)
9. [Open questions for you](#9-open-questions-for-you) — all four answered, see the status note above

## 1. First: your objection, tested

You asked whether a hit-rate difference is a real player property or an artifact of minutes and
substitutions. That is testable, so I tested it before designing anything.

**Split-half reliability** — does a player's hit rate in the first half of their starts predict
the second half? (2025/26, players with 20+ starts.)

| | hit rate | mean actions |
|---|---|---|
| DEF (57 players) | r = **0.70** | r = 0.77 |
| MID (49 players) | r = **0.74** | r = 0.82 |

**Does minutes explain it?**

| | DEF | MID |
|---|---|---|
| corr(mean minutes, hit rate) | +0.49 | +0.22 |
| corr(mean minutes, hit rate *residual after mean actions*) | −0.11 | −0.19 |

And the specific pair:

| player | hit rate | actions/match | actions **per 90** | avg mins/start |
|---|---|---|---|---|
| Alderete | 36% | 10.2 | 10.2 | 89 |
| Ballard | 67% | 10.6 | 10.9 | 88 |

**Three conclusions, one of which is against me:**

1. Hit rate is a **real repeatable trait** (r≈0.7), not noise. Your "is there any guarantee"
   worry does not sink it.
2. You are right that **minutes matter** (+0.49 correlation) — but that signal is already
   inside mean actions. The residual correlation is ≈0, so minutes does not explain hit-rate
   differences the mean cannot. Alderete and Ballard play the same minutes at the same per-90
   rate and still differ 36% vs 67%. That pair is genuinely distributional.
3. **My Casemiro/Ayari example was partly wrong.** Casemiro plays 80 min/start at 12.1 per 90
   vs Ayari's 88 min at 10.2 per 90 — so that gap *is* substantially a minutes-and-rate
   artifact, exactly the confound you described. I should not have presented it as a
   like-for-like pair.

**What this changes in the modelling:** mean actions is *more* reliable than hit rate
(0.77–0.82 vs 0.70–0.74), so raw hit rate is the wrong estimator too. The right one is
empirical-Bayes: shrink each player's observed hit rate toward the hit rate implied by their
per-90 rate and minutes, weighted by sample size. Small sample → trust the rate; large sample →
trust the observed hit rate. Priority 3 in `docs/prediction_roadmap.md` should be updated to say
that rather than "use hit rate".

**And this is the point of the app.** That exchange took three queries and produced a real
correction. The app should make that loop routine instead of something I do ad hoc when you
push back.

## 2. What this app is actually for

Not a dashboard. An **evaluation loop**:

> I change a method → regenerate → you see what moved and why → you tell me where it is wrong →
> that disagreement gets recorded → when results land, we find out who was right.

Three requirements fall out of that, and they drive every decision below:

- **Comparison is primary.** Seeing one list of numbers is nearly useless for judging a method.
  Seeing what a change *moved* is what tells you whether it helped.
- **Every number must be explainable.** You are hesitant about history-based prediction, which
  is the correct instinct. So no bare projections: each figure shows its components, its
  inputs, and its sample size.
- **Your disagreement must be capturable and scoreable.** Otherwise feedback stays in chat and
  evaporates.

## 3. Core design decision: runs are artifacts

The web app **never computes a projection**. A CLI writes an immutable run file; the app reads
run files.

```
uv run -m src.fpl.project --name "v3-shrunk-dc"
    -> data/2026-2027/runs/2026-08-16T14-02-11_v3-shrunk-dc.json

uv run -m src.web.serve      # FastAPI on :8000, reads data/<season>/runs/*.json
```

Why this way:

- **Comparison becomes trivial** — two runs are two files. Rank deltas, biggest movers, and
  eventually which one scored better.
- **The page is instant.** Projecting ~600 players takes seconds; a page load should not.
- **Reproducible.** A run records its inputs (snapshot timestamps), its config, and its code
  version, so "why did Saka move 12 places" is always answerable.
- **Our workflow already works this way.** `JsonSnapshotStore` is the same idea; runs are the
  derived-data sibling of `prior_season/`.

Run file shape:

```jsonc
{
  "run_id": "2026-08-16T14-02-11_v3-shrunk-dc",
  "created_at": "...", "season": "2026-2027", "gameweek_from": 1, "gameweek_to": 10,
  "method": { "name": "v3-shrunk-dc", "notes": "empirical-Bayes DC hit rate",
              "params": { "dc_shrinkage_k": 8, "friendly_weight": 0.35 } },
  "inputs":  { "bootstrap_snapshot": "...", "lineups_through": "2026-08-14" },
  "replacement_level": { "GKP": 31.9, "DEF": 33.3, "MID": 37.5, "FWD": 25.7 },
  "players": [
    { "player_id": 411, "web_name": "Haaland", "team": "MCI", "pos": "FWD",
      "projection": { "total": 65.4, "per_gw": 6.54 },
      "vorp": 39.7, "price": 15.5, "ownership": 73.5,
      "components": { "appearance": 18.7, "goals": 24.1, "assists": 9.2,
                      "clean_sheets": 0, "def_contrib": 0, "bonus": 13.7, "negatives": -0.3 },
      "inputs": { "p_start": 0.95, "mins_if_start": 87, "xgi_per_90": 0.86,
                  "dc_hit_rate": 0.04, "dc_sample": 34, "fixtures": [...] },
      "confidence": { "sample_matches": 34, "evidence": "competitive" },
      "flags": ["pen_taker"] }
  ]
}
```

Note `components`, `inputs`, `confidence` are not decoration — they are the explainability
requirement, and the reason you can audit a projection instead of trusting it.

## 4. Screens

**A. Draft board** (`/draft`) — sorted by VORP, not projected points. Shows replacement level
per position and tier breaks, so positional scarcity is visible. No price column. Marks players
already drafted (from `PlayerPresences`) so it doubles as a live draft aid and, in-season, a
waiver board over the unowned pool.

**B. FPL board** (`/fpl`) — sorted by projection, with price, points-per-million, ownership and
differential flags. Same underlying run, different lens.

Both: sortable/filterable by position, club, price, availability; component columns toggleable.

**C. Player detail** (`/player/{id}`) — the screen that answers your objection.
- Component waterfall: where the projected points come from.
- The inputs behind each component, each with its sample size.
- DC shown as `hit 36% (raw 41%, shrunk, n=32)` — shrinkage and evidence made visible.
- Per-match history strip for the underlying stat, so you can see consistency vs spikiness
  yourself rather than taking a summary statistic on faith.
- Pre-season friendly minutes and the `unavailable` listings from FotMob.
- The FPL `news` / `status` string verbatim.

**D. Run comparison** (`/compare?a=…&b=…`) — rank deltas, biggest risers/fallers, and a summary
of which parameters differ. This is how you evaluate a method change in ten seconds.

**E. Calibration** (`/calibration`) — once the backtest harness exists: per-component scores
against the naive baseline, calibration curves, and (as gameweeks resolve) actual-vs-projected
per run. Turns "this feels better" into "this is better".

## 5. Feedback as labelled data

On any player, you can record a disagreement:

- a reason code: `will_not_start`, `nailed_starter`, `role_changed`, `injury_doubt`,
  `fixture_wrong`, `just_wrong`
- optionally your own `p_start` or projected points
- a free-text note

Stored as `data/<season>/feedback/<gameweek>/<timestamp>.json`, keyed by run and player.

Two payoffs, and the second is the interesting one:

1. I read it and act on it.
2. **It becomes labelled evaluation data.** When the gameweek resolves we can score your
   overrides against the model's. If your judgement beats it consistently in some area — say,
   spotting rotation — that is a signal to encode, and where the model beats you we both learn
   something. Feedback stops being an opinion and becomes a measurable baseline.

## 6. Stack and layout

- **Backend:** FastAPI + uvicorn. This is already the repo's stated Phase 3 north star in
  `README.md`, and `src/mcp.py` already runs FastMCP — so the MCP server can later be
  auto-generated from the same OpenAPI spec, exactly as the README envisages.
- **Serialization:** reuse the existing `src/fpl/views/` dataclasses. They are already
  `asdict`-able and already the presentation layer; no parallel view models.
- **Frontend:** one self-contained HTML page, vanilla JS, no build step. A Python repo should
  not grow an npm toolchain for what is fundamentally a few sortable tables. I can edit it as a
  single file, and it works offline.
- **Storage:** JSON files on disk. No database. Consistent with `JsonSnapshotStore`.
- **Regeneration:** one command, `./refresh.sh`, chaining fetch → FotMob → project → new run,
  so "regenerate so I can see your updates" is a single step.

New package, following existing conventions (its own `README.md`, docstrings over comments,
fail-loudly):

```
src/web/
├── README.md
├── serve.py         # FastAPI app + static mount
├── api.py           # /api/runs, /api/runs/{id}, /api/compare, /api/feedback
├── runs.py          # read/write/list run artifacts
├── feedback.py      # feedback persistence
└── static/index.html
src/fpl/project.py   # CLI: build a run artifact from the current models
tests/test_runs.py   # run round-trip, comparison maths, feedback persistence
```

## 7. Phases

Each phase ends with something usable.

| Phase | Delivers | Rough size |
|---|---|---|
| **1** | Run artifact format + a new `src/fpl/project.py` generating one from the projection I already built + FastAPI serving the draft and FPL tables | the core ask, usable immediately |
| **2** | Player detail with component breakdown, inputs, sample sizes, per-match history | makes numbers auditable |
| **3** | Feedback capture and persistence | closes your half of the loop |
| **4** | Run comparison view | makes method changes evaluable |
| **5** | Calibration harness + page (roadmap item 1) | makes "better" objective |
| **6** | Then start iterating models: minutes model, shrunk DC, complete scoring function | the actual point |

Phase 1 deliberately reuses the projection from the draft board rather than waiting for better
models — you get the review surface first, and every model change after that is visible in it.

## 8. Alternatives considered

- **Streamlit.** Faster to stand up, but side-by-side run comparison is awkward, feedback POST
  is clumsy, and it would be a second stack alongside the README's FastAPI plan. Rejected.
- **Static generated HTML, no server.** Simplest, but cannot capture feedback, which is half
  the requirement. Rejected.
- **Compute projections live in the request.** Kills comparison and reproducibility, and makes
  page loads slow. Rejected — this is the decision I feel most strongly about.
- **A database.** No concurrent writers, small data, and JSON diffs are reviewable. Not worth it.

## 9. Open questions for you

1. **Both games from one run, or two?** I assume one projection with two lenses (draft ranks by
   VORP, FPL by points and price). Cheaper and keeps them consistent — but if you want to tune
   methods separately per game, say so now, it affects the artifact shape.
2. **Live draft aid?** Should the draft board have a "mark as taken" mode for use *during* the
   draft, recomputing VORP as the pool shrinks? Useful but adds state.
3. **How many runs kept?** I suggest keeping all — they are small — but pruning to the last N
   is easy if the directory gets noisy.
4. **Local only?** I am assuming `localhost`, single user, no auth. If you want it on your
   phone during a draft that changes things.

## Related docs
- Where the points are and what to model — `docs/prediction_roadmap.md`
- Repo conventions and traps — `CLAUDE.md`
- Documentation standards — `docs/metadoc.md`
