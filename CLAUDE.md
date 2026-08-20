# Working in this repo

Read this before writing code here. It is the short version; the linked docs are canonical.

## Non-negotiables

**Data completeness beats code robustness.** Never `continue` past an exception, never
best-effort a parse, never substitute zeros for missing data. If a player, match, team or field
could be missing or misparsed, raise with a message that says what to do about it. Everything
here runs manually and supervised, so an error in the face is better than a quietly short
dataset. Full rule: `.cursor/rules/coding-principles.mdc`.

A skip that is genuinely correct (an out-of-season fixture, a stale provider slug) must still be
counted and logged. "Not silent" is the bar, not "never skipped".

**Absence is data.** `Query.player_season()` returns `None` for a player with no prior Premier
League season, and callers must handle it. Do not paper over that with a zero-filled row.

**Docs move with code.** Before editing any `.py` or `.md`, read `docs/metadoc.md`. After
changing a module, update the `README.md` files along its path
(`.cursor/rules/update-md.mdc`). Prefer docstrings over inline comments; reserve comments for
non-obvious intent or invariants.

## Running things

Never bare `python`. `uv run` is the documented runner; `./run.sh` is a thin wrapper that
prefers it and falls back to a plain `.venv/` when uv is not installed, so the scripts work
either way:

```bash
./run.sh -m src.fpl.fetch [--baseline|--baseline-only]   # FPL snapshots + prior-season baseline
./run.sh -m src.fotmob.load [--team NAME] [--season S]   # FotMob lineups, friendlies and cups
./run.sh -m src.fpl.project [--game draft|fpl] [--method NAME]   # write a run artifact
./run.sh -m src.web.serve                                # review app on 127.0.0.1:8000
./refresh.sh [method]                                    # fetch -> fotmob -> project, in one go
./run.sh -m src.fpl.main                                 # in-season predictions & evaluation
./run.sh -m pytest                                       # full suite (needs cached data)
```

`NEXT_GAMEWEEK` no longer has to be set. `resolve_next_gameweek()` in
`src/fpl/loader/utils.py` prefers the environment variable, then derives the answer from the
stored fixtures snapshot, then falls back to GW1 with a warning. Every entry point uses it, so a
fresh checkout works without a `.env`.

`playwright install chromium` is per-environment, not per-machine: switching between uv's venv
and a plain one needs it again, because the two pin different browser builds.

## The projection loop

`src/fpl/projection/` projects points over a gameweek range and writes an **immutable run
artifact**; `src/web/` reads those artifacts and never projects. That split is deliberate: it
makes comparing two methods a diff, keeps pages instant, and means a run stays reproducible.

Change a model, generate a run, look at what moved:

```bash
./run.sh -m src.fpl.project                               # the default, v3-role-trust
./run.sh -m src.fpl.project --method v3-role-trust-flat   # its control: one knob different
./run.sh -m src.web.serve                                 # then the Compare screen
```

Methods live in `METHODS` in `src/fpl/projection/methods.py`. Add controls that differ from the
baseline in exactly one way — a comparison that changes two things at once settles nothing. When
you add a parameter, pin the existing methods to the old value rather than letting a live default
rewrite them; `FLAT_ROLE` is there for that reason. A method name that quietly changes meaning
invalidates every stored comparison and every piece of feedback that points at it.

Never overwrite a run. `write_run` refuses, and pruning skips any run stored feedback points at.

## Traps that have already bitten us

**Identifiers are not stable across seasons.** FPL reassigns element `id` *and* team `id` every
year — 16 of 20 team ids changed meaning between 2025/26 and 2026/27. Join on element `code` and
team `short_name`. Anything comparing raw ids across seasons is a bug.

**Bootstrap lies about players who changed club.** Before GW1 `bootstrap-static` carries last
season's totals, but for anyone who moved it either zeroes them (6 players in 2026/27) or
truncates them (1 player), while leaving a stale `defensive_contribution` behind. Jaidon
Anthony's 2,717-minute Burnley season reads as "never played". `element-summary/history_past` is
authoritative. See `src/fpl/loader/baseline.py`.

**FotMob match slugs are not season-scoped.** Last season's
`/matches/brentford-vs-liverpool/...` now serves *this* season's fixture, so a stale fixture-list
entry silently saves the wrong match. Two defences, both in `src/fotmob/load.py`: only fetch
fixtures inside `Season.window(season)`, and always file a match under the `matchId` its own
payload reports.

**`defensive_contribution` is a raw action count, not points.** CBI + tackles for defenders,
plus recoveries for everyone else. The 2-point threshold is 10 for defenders and 12 for others,
and whether a player clears it can only be judged per match — season averages mislead. The
per-gameweek history in `data/<season>/elements/*.json` is what you want.

**Friendlies are weak evidence, not no evidence.** In pre-season they are all we have.
`MatchKind` tags them, availability (`unavailable`) is never discounted — an injury is an injury.
Backtested against 2025/26, pre-season start share beats last season's start share on its own, and
blending the two beats either. How much weight it earns is *not* a constant — see the role curve
below. Missing every friendly is a real signal, not an artifact: those 134 players averaged a 0.07
start share over GW1–5. Working in `src/fpl/projection/minutes.py`.

Everything fitted about pre-season rests on **one summer** — 101 stored club-match records for
2025/26, 90 friendly and 11 competitive, 2 to 8 per club — because that is the only season on disk
with pre-season lineups. Cross-validating by club says the role curve generalises to unseen clubs
(MAE 0.1934 flat → 0.1816, both refitted per fold); nothing can yet say it generalises to another
August. Keep this season's lineups and next year gives the first real out-of-sample test.

**Not every pre-season match is a friendly, and the exceptions are the informative ones.** The
Community Shield, UEFA Super Cup and Club World Cup fall before GW1 and are the only fixtures in
the window where a big club picks a real XI. `build_preseason_roles` reads *everything* before
the GW1 deadline and weights by kind; an earlier version filtered to friendlies and threw the
Community Shield away, which had Arsenal's reserve keeper ranked above David Raya. The projection
weights a friendly at **0.20** against 1.0 competitive (`preseason_friendly_weight`, fitted);
`RotationConfig`'s own default stays 0.35 for the in-season rotation view, which is a different
question.

**What pre-season tells you depends on who the player is, and the relationship is not
monotonic.** One flat blend weight put Bruno Fernandes — 35 starts, same club, fit, every set
piece — at `p_start` 0.47 and exactly on the replacement line, because he started 1 of 6
pre-season matches. Fitting the weight inside buckets of last season's start share gives a hump:
**0.3** below a 0.30 prior (a fringe player's friendly starts are cheap), **1.0** between 0.30 and
0.80 (an open place, decided in pre-season), **0.2** above 0.80 (absence is load management).
`PRESEASON_ROLE_KNOTS` in `src/fpl/projection/minutes.py`. Movers are exempt — pre-season is the
only observation of their new squad, and for them MAE falls monotonically as the weight rises.
Two things this is *not*: a reason to distrust a low `p_start` in general (of 12 established
players who started no pre-season match in 2025/26, 11 started none of GW1–5), and licence to key
the curve on `starts / appearances` instead — that was measured, and it promotes backup keepers to
"nailed".

**Thin club evidence must be trusted less, and match *count* is the wrong variable.** Reliability
is not monotonic in it — clubs with 3–4 stored pre-season matches predict GW1–5 starts better
(r=0.776) than clubs with 5+ (r=0.643). What works is a ramp on evidence *weight*,
`min(1, weight / PRESEASON_TRUST_WEIGHT)`, so one competitive fixture reaches full trust alone.
Cuts error 21% on clubs with 1–2 matches at no aggregate cost. An earlier `n/(n+k)` attempt on
match count made things worse and was correctly rejected — for the wrong reason.

**VORP is not supposed to move when a star is drafted, and that is the commonest "bug" report
about it.** Taking the best forward removes one player from the pool *and* one pick from those
still to come, so the player who will be taken last is unchanged and replacement level holds.
Only a *reach* — a pick spent below the line — moves it. That makes VORP the right cross-position
value metric and a poor "who do I take now" metric, which is why `src/web/opportunity.py` exists
alongside it: `Δnext` and the cost-of-waiting cards answer the on-the-clock question and do move
on every pick.

**Price VORP against starting slots, not roster slots.** Eight keepers get drafted in a
four-manager league, so roster slots measure a starting keeper against the ninth best — a backup
worth ~22 points. That put three goalkeepers in the top ten. Your second keeper scores you
nothing; `DRAFT_STARTING_SLOTS` (1/4/4/2) is the default for that reason.

**The league average is the wrong fallback for a player with no Premier League record.** It
averages over clubs, and a promoted club is not an average club: first-season players at promoted
clubs managed 0.308 xG/90 as forwards against 0.419 league-wide, a ratio of 0.74 — near enough the
0.73 attack rating the model already assigns a promoted side. `RateModel.estimate` scales the
fallback by club attack for that reason. Saves are excluded: a weak club's keeper faces *more*
shots.

**A start share does not survive a transfer.** It describes the squad the player just left. Of
the 119 players who started 70%+ of 2024/25, those who stayed went on to start 0.74 of GW1–5,
those who moved 0.48, and those who moved to a clearly stronger club **0.40**. BOU→ARS went from
0.82 to 0.00. `transfer_role_multiplier` in `src/fpl/projection/minutes.py` discounts the
prior-season term only — the pre-season term is already an observation of the new squad.

**Team strength ratings are zero before kickoff.** `strength_attack_home` and its three siblings
are all 0 for all 20 clubs in a pre-season bootstrap; only `strength_overall_home` carries the
1–5 tier. Anything that reads them in August rates every club identically.
`src/fpl/projection/strength.py` measures ratings from last season's results instead.

**Step-function scoring is not linear.** Saves score in threes, goals conceded in twos, defensive
contribution against a threshold. `E[floor(saves/3)]` is 0.66 at 3.0 saves, not 1.0 — use
`src/fpl/projection/poisson.py`, never a plain division.

**Anything keyed on an element id goes stale in July.** `PLAYER_MAPPING_OVERRIDES` used to store
`fpl_player_id`, which quietly pointed FotMob's Gabriel at J.Timber once 2026/27 ids landed. It
now stores `fpl_player_code`. Same class of bug as the team-id table that used to sit in
`src/fotmob/rotation/fotmob_adapter.py`. If you write a literal id into source, you have written a time bomb.

**Name matching needs a floor, and a higher one across clubs.** Three academy players called Josh
tie with each other on a shared first name, and "George King" ties Tom King with Josh King league
wide. `MIN_MATCH_SCORE` and `MIN_GLOBAL_MATCH_SCORE` in `src/fotmob/rotation/fotmob_adapter.py`
turn those spurious ties into an honest "no candidate". Pre-season callers pass
`allow_unmatched=True` because academy players genuinely are not FPL elements — 187 of them in
2026/27 — and the adapter counts and logs every one.

## Where we want to take the models

`docs/prediction_roadmap.md` — measured breakdown of where FPL points actually come from
(56% is appearance points), what the older `forecast/` model covers (45%), and a prioritised list
of what to build. Read it before changing anything in `src/fpl/forecast/` or
`src/fpl/projection/`.

`docs/webapp_plan.md` — why the review app has the shape it does, and the split-half work behind
the shrinkage decisions.

## Where things live

- Season names, ordering and windows: `Season` in `src/fpl/loader/utils.py`. `Season.CURRENT` is
  the single place the active season is declared.
- FPL fetch/populate: `src/fpl/loader/load.py`; converters in `src/fpl/loader/convert/`.
  `load_from_snapshots()` is the offline path used by the projector and the app.
- Prior-season baseline: `src/fpl/loader/baseline.py`.
- Collections and the `Query` facade: `src/fpl/models/immutable.py`.
- FotMob capture and parsing: `src/fotmob/load.py`; club rosters per season in
  `src/fotmob/models/fotmob_metadata.py`.
- Rotation analysis: `src/fpl/models/rotation.py` plus `src/fotmob/rotation/`.
- Horizon projection, run artifacts, VORP: `src/fpl/projection/` (see its `README.md`).
- Review app: `src/web/` (see its `README.md`).

## Adding a new season

1. Add the directory name to `Season.ORDERED` and point `Season.CURRENT` at it.
2. Add its 20 clubs to `SEASON_TEAMS` in `src/fotmob/models/fotmob_metadata.py`, adding any
   promoted club to `FOTMOB_TEAM_IDS` and `FPL_SHORT_NAMES`.
3. Run `./run.sh -m src.fpl.fetch --baseline-only` *before* the season's first kickoff.
4. `validate_against_fpl(bootstrap["teams"], season)` will tell you if step 2 is wrong.
