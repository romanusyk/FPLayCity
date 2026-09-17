# Fantasy Terms Glossary

High‑level glossary for fantasy football concepts used in this project, with a focus on Fantasy Premier League (Classic) and FPL Draft.

See **"Fantasy Premier League rules"** — full game rules for the salary‑cap game — at `data/2025-2026/rules/fpl.md`.  
See **"FPL Draft rules"** — full game rules for the draft‑based mode — at `data/2025-2026/rules/draft.md`.

## Game Modes

### Fantasy Premier League (Classic)

The standard **Fantasy Premier League** (often just “FPL”) is a salary‑cap game:

- You build a **15‑player squad** (2 GKs, 5 DEFs, 5 MIDs, 3 FWDs) within a **£100m budget** and with **max 3 players per real club**.
- Each **Gameweek**, you pick a **starting XI**, plus a **captain** (score doubled) and **vice‑captain** (inherits the armband if the captain does not play at all).
- You can make **transfers** between Gameweeks; you get **1 free transfer per Gameweek**, can **roll** some spare transfers, and spend points for extra transfers.
- You can play **chips** such as **Wildcard**, **Free Hit**, **Bench Boost**, and **Triple Captain** under the constraints described in `data/2025-2026/rules/fpl.md`.
- Scoring uses the standard FPL points rules (goals, assists, clean sheets, cards, bonus points, etc.).

This is the main game mode assumed by most prediction logic in this project.

### FPL Draft

**FPL Draft** is an alternative mode based on unique player ownership:

- You still end up with a **15‑player squad** (same positional structure), but there is **no budget**.
- Players are assigned through a **snake draft** among managers in a league; **each real‑world player can only be owned by one team** in that league.
- After the draft, you manage your squad via **waivers**, **free agency**, and **trades** instead of budgeted transfers.
- There are **no captains** in Draft; scoring is otherwise intended to be **identical to Classic** (see `data/2025-2026/rules/draft.md`).
- Leagues can be **Classic** (total points) or **Head‑to‑Head** (weekly matchups).

Most model concepts here (expected points, fixture difficulty, minutes, etc.) apply directly to Draft, but roster and transaction constraints differ.

## Core Objects

### Player

A **player** is a single real‑world footballer in the Premier League, with:

- A **position** (GK, DEF, MID, FWD).
- A **club** (real‑world team).
- A historical and projected set of **statistics** that drive fantasy points.

In this project, when we talk about predicted outcomes (e.g. xG, xA, expected points), they are usually **per player per fixture**.

### Team / Club

A **team** (or **club**) is a real‑world Premier League side (e.g. Arsenal, City).  

Fantasy constraints and scoring often aggregate at team level (e.g. clean sheets, max 3 players per club).

### Fixture

A **fixture** is a single scheduled match between two teams (e.g. ARS vs MCI in Gameweek 5), defined by:

- **Home and away teams**.
- **Kickoff time**.
- The corresponding **Gameweek**.

Most model features are defined **per player per fixture** or **per team per fixture** (e.g. xG for a player in a given match, clean‑sheet probability for the team).

### Gameweek (GW)

A **Gameweek** is FPL’s scoring window:

- All fixtures in the Gameweek contribute to fantasy points.
- Deadlines are typically **90 minutes before the first kickoff** in that Gameweek (see rules docs for details).
- In model terms, we usually predict **per‑fixture** outcomes and then aggregate them to **per‑Gameweek expected points**.

### League

A **league** is a competition structure among managers:

- **Classic leagues**: ranked by **total fantasy points** across Gameweeks.
- **Head‑to‑Head leagues**: each Gameweek you play a “match” against another team; 3 points for a win, 1 for a draw.

Leagues exist in both Classic and Draft modes, but the way you build and update squads differs.

## Scoring Vocabulary

### Clean Sheet

A **clean sheet** is:

- For a player: **not conceding a goal while on the pitch and playing at least 60 minutes** (excluding stoppage time).  
- For a team: the team concedes **0 goals** in the fixture.

Relevant points rules:

- GKs and DEFs get clean‑sheet points.
- Once a qualifying player is subbed off, later goals conceded do **not** remove the clean sheet for that player.

### Defensive Contribution (DC)

**Defensive Contribution** is an extra points category introduced to reward defensive actions:

- **Defenders** earn 2 points if they reach **10+ combined clearances, blocks, interceptions (CBI), and tackles**.
- **Midfielders and forwards** earn 2 points if they reach **12+ combined CBI, tackles, blocked shots, and recoveries**.

In model terms, we may talk about **expected defensive contribution points** for a player in a given fixture or Gameweek.

### Assist (Fantasy Definition)

An **assist** in FPL is awarded to the player from the scoring team who has the final relevant touch before a goal, according to the official (and sometimes nuanced) rules:

- Includes passes, certain deflected passes, rebounds from shots, winning penalties or direct free‑kicks, and touches leading directly to a scoring chance.
- Excludes situations with too many defensive touches, regaining possession, or some handball scenarios.

For exact edge cases, see the **Assists** sections in:

- `data/2025-2026/rules/fpl.md`
- `data/2025-2026/rules/draft.md`

### Bonus Points System (BPS)

The **Bonus Points System (BPS)** converts underlying match statistics into a **BPS score per player**:

- The top three BPS scorers in a match get **3, 2, and 1 bonus points** respectively (with tie‑breaking rules).
- BPS uses a wide set of stats (key passes, big chances, errors, etc.) provided by Opta/Stats Perform.

In models, we might estimate **expected bonus points** by approximating or learning from BPS‑driven outcomes.

## Difficulty and Analytics Metrics

### Fixture Difficulty Rating (FDR)

**Fixture Difficulty Rating (FDR)** is a numeric rating for how hard a fixture is for a team:

- Conceptually, low FDR = “easy” fixture, high FDR = “hard” fixture.
- Often represented on a **discrete scale (e.g. 1–5)** in public tools, but in this project we can treat FDR as a **continuous difficulty feature**.

In the prediction model, historical stats are often normalized or re‑weighted by FDR, so that good output vs tough opponents counts more than vs weak opponents.

### Expected Goals (xG)

**Expected goals (xG)** is the **probability‑based expectation of goals** given the chances a player or team has:

- Each shot has a probability of becoming a goal; xG is the sum of these probabilities.
- xG can be defined **per shot, per player, per team, per fixture, or per Gameweek**.

We use xG to approximate **goal‑scoring threat** beyond raw goals scored.

### Expected Assists (xA)

**Expected assists (xA)** is the expected value of assists:

- Based on chance creation / passes leading to shots and their qualities.
- Typically aggregates over the quality and context of passes or chances created.

We use xA to capture a player’s **creative threat**.

### Expected Goal Involvement (xGI)

**Expected goal involvement (xGI)** is:

- Roughly **xG + xA** for a player (sometimes with small adjustments).
- Measures how likely a player is to be **directly involved in goals** (scoring or assisting).

For ranking attacking players, xGI is often more stable and informative than goals or assists alone.

### Expected Goals Conceded (xGC)

**Expected goals conceded (xGC)** is:

- The expected number of goals a team will concede, given the chances faced.
- Derived from the opponent’s xG and defensive metrics.

Lower xGC implies higher clean‑sheet potential and better defensive outlook.

### Expected Clean Sheets (xCS) / Clean‑Sheet Probability

**Expected clean sheets (xCS)** is the expected number of clean sheets, often approximated as:

- \( \text{xCS} \approx P(\text{no goals conceded}) \) for a team in a fixture.
- In practice, we often work directly with **clean‑sheet probability** per fixture.

We use xCS or clean‑sheet probability to assign **expected clean‑sheet points** for players.

### Expected Defensive Contribution Points

We can define **expected defensive contribution (xDC)** as:

- The **expected probability** that a player reaches the **CBI/tackles threshold** that earns DC points.
- Often estimated indirectly from player role, defensive stats, and opponent style.

This feeds into the overall **expected points** for defenders and defensively active midfielders/forwards.

### Expected Fantasy Points

**Expected fantasy points (xPts)** is the **single summary metric** we usually care about:

- Combines expected goals, assists, clean sheets, defensive contribution, and sometimes bonus points, each weighted by the official scoring rules.
- Often computed **per player per fixture**, then **aggregated across upcoming fixtures / Gameweeks**.

Most ranking and decision logic in this project is expressed in terms of **xPts**, adjusted by **minutes and availability risk**.

## Projection and Draft-Board Vocabulary

Terms that are ours rather than the game's — they appear as columns in the review app and as
symbols in `src/fpl/projection/`. Definitions here are deliberately short; each names the
canonical source. The same text, written for reading on the spot, is in the app under
`#/glossary` and on hover over any dotted label.

### p(start), role share, availability

**p(start)** is the probability a player starts a given match, and it gates almost everything else
— a clean sheet needs 60 minutes, a defensive contribution needs a start. It factors as
**role share** (his standing in the squad, blended from last season and pre-season) × **availability**
(FPL's own injury and suspension feed). Keeping them separate is what lets the app say "first
choice, but injured" as the two facts it is.

Model: `MinutesModel`, `MinutesEstimate` in `src/fpl/projection/minutes.py`.

### Pre-season role curve

How much pre-season outweighs last season is **not one number**. It depends on how nailed the
player was: for a first-choice player pre-season absence is rest, for a squad player it is the
manager deciding an open place, and for a fringe player a friendly start is cheap. The weight is
therefore a curve over last season's start share — low at both ends, near 1.0 in the middle.
Players who changed club are exempt.

Constant and the measurement behind it: `PRESEASON_ROLE_KNOTS` in `src/fpl/projection/minutes.py`.

### Shrinkage

Pulling a player's own rate toward the average for his position when there is little evidence
behind it. Every rate in the projection is shrunk, and the app always shows the sample size next
to the number so a rate from 3 matches is not mistaken for a rate from 30.

### Replacement level

The points available at a position **for free** — the best player who will still be undrafted once
every starting slot in the league is filled. Priced against *starting* slots (1 GKP, 4 DEF, 4 MID,
2 FWD per manager), not roster slots: your second keeper scores you nothing, so measuring a
starting keeper against a backup inflates every keeper on the board.

`replacement_levels`, `DRAFT_STARTING_SLOTS` in `src/fpl/projection/vorp.py`.

### VORP (Value Over Replacement Player)

Projected points minus replacement level for that position. The right way to compare a forward
with a midfielder, because scarcity differs by position.

VORP is **deliberately stable** during a draft, which surprises everyone once: taking a star
removes one player from the pool *and* one pick from those still to come, so the last man standing
does not change. Only a *reach* — a pick spent below the line — moves it.

`value_over_replacement` in `src/fpl/projection/vorp.py`.

### Δnext, and the cost of waiting

The other half of the draft question, and the one you have on the clock. **Δnext** is a player's
points over the *next available* player in his position: the cost of not taking him. **Cost of
waiting** is the same idea per position — the best available now against the best still there at
your next pick. Unlike VORP, both move on every pick.

`next_best_drop`, `wait_costs` in `src/web/opportunity.py`.

### Waiver, free agency, and locked players

After the draft, the only players you can sign are the ones nobody in your league owns, and every
signing is a **swap in the same position** — you name the player you are releasing. Two modes:

- **Waivers.** Claims are submitted before a deadline and processed together, in **waiver order**
  (lowest number first). Contested claims go to the manager higher in that order.
- **Free agency.** Between waiver windows, whoever asks first gets the player.

A player is **locked** when he was added to the game or dropped by another squad too recently; he
shows as unowned but cannot be claimed until the next deadline. `element-status` on the draft API
reports all three states — `a` available, `o` owned, `l` locked — and
`src/fpl/loader/draft_league.py` treats anything it does not recognise as *not* claimable.

### Waiver gain (squad value, not player value)

What a claim is worth: your **squad's** points over a horizon after the swap, minus before it,
picking the best legal eleven each gameweek (1 keeper, at least 3 defenders, at least 1 forward).

This is a different question from VORP and usually a much smaller number. Signing the tenth-best
midfielder in the game gains nothing if he would sit behind your other five, and the metric says so.
Two consequences worth knowing:

- The player it is best to **drop** is usually the one who never starts, not the one the signing
  displaces.
- A good signing can be worth more than the gap to the man he replaces, because the eleven reshapes
  around him — three defenders and five midfielders instead of four and four.

`waiver_board`, `SquadValuation` in `src/web/waivers.py`.

### Current-form ramps

Two weights that walk from last season toward this one as matches are played, because everything
in the model was fitted on pre-season and pre-season stops being the freshest evidence:

- **Minutes.** The pre-season role curve reaches the recent window in full by **five gameweeks**.
  A nailed starter who has not started in five weeks is not a nailed starter.
- **Club ratings.** This season's goals weigh equally with all of last season at **five matches**,
  so a club that has genuinely changed gets priced in by autumn rather than next July.

Both are inert before GW1, and **neither number is fitted** — one gameweek cannot fit a ramp. They
are judgements with controls: `v4-form-minutes` and `v4-form-strength` change one each,
`v3-role-trust` changes neither.

`current_season_ramp` in `src/fpl/projection/minutes.py`, `TeamStrength` in
`src/fpl/projection/strength.py`.

### My squad (classic FPL)

The fifteen your own FPL team held at the end of the last gameweek that has *started*. The FPL
board tags them, filters to them, and values them: `1-11` were the eleven you submitted, `12-15`
the bench in order, `C` and `V` the armbands as they were then.

Two limits, both stated on the page rather than buried here. It is **never the upcoming
gameweek**: FPL publishes a squad only after its deadline, so a transfer you have already made for
next week is not in the API at all, let alone in this. And the squad's value is the best legal XI
each gameweek with **no captain doubling and no auto-subs** - the worth of the fifteen, not a
predicted score.

`FplSquad` in `src/fpl/loader/fpl_squad.py`; the entry id lives in `data/fpl_entry.json`, never in
source, because a hardcoded one turned out to be a stranger's team for eight months.

### Owner scope

Who a list is showing, on the waivers screen. **Available to me** is your fifteen plus the free
agents — everyone you could field or sign this week. **Mine** is your squad alone, for picking an
eleven. **All** adds your rivals' squads, which only a trade can reach. **Free agents** is the
claim pool on its own.

`owner_kind` in `src/web/waivers.py` is the underlying tag: `mine`, `rival`, `free`, `locked`.

### Horizon

How many gameweeks ahead a number looks. A run stores per-fixture points, so any horizon up to the
run's length is a sum rather than a new projection — which is why the waivers and FPL screens can
show 1, 3 and 5 gameweeks side by side for the price of one. Ten is the cap, because that is as far
as the fixture-level projection goes.

The same swap is scored at every horizon on purpose: a claim that wins the next gameweek and loses
the next five is a decision, not a recommendation.

One of the three **decides**: it ranks the table, and on the FPL board it also drives `per GW` and
`pts/£`. The other two are there to show what that decision costs elsewhere. Both screens read the
same three from the same parser, so a horizon means the same window everywhere; what does *not*
follow the horizon is the FPL board's `Proj` column and its component columns, because a run stores
points per fixture but components only as run totals.

`parse_horizons`, `span_gameweeks`, `resolve_sort_horizon` and `horizon_totals` in
`src/web/waivers.py`.

### Tier

A group of players separated from the next group by a sharp drop in VORP. A tier break means the
fall-off below is real: pick now, or accept a worse player.

`tier_breaks` in `src/fpl/projection/vorp.py`.

### Run, method, and calibration

A **run** is one saved projection — an immutable file, never edited after it is written, so two
runs can always be diffed. A **method** is the named parameter set behind it; controls differ from
the default in exactly one parameter so a comparison settles something. **Calibration** scores a
run against what actually happened, per component, against a stated naive baseline.

`src/fpl/projection/artifacts.py`, `src/fpl/projection/methods.py`, `src/web/calibration.py`.

### Related Docs

- How a projection is produced, component by component — `src/fpl/projection/README.md`
- Where league ownership comes from, and why draft ids are season-scoped — `src/fpl/loader/README.md`
- The app that reads runs, and its screens — `src/web/README.md`
- Where FPL points actually come from, measured — `docs/prediction_roadmap.md`
