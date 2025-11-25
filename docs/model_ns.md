# Prediction Model: The North Star

This document describes the conceptual “north star” for how we want to predict fantasy points for players in Fantasy Premier League.  
It mirrors how I actually make decisions in the game and provides a target for the models in this project.

See **"Fantasy terms glossary"** — common vocabulary and game‑mode definitions — at `docs/glossary.md`.

## Task

In both Fantasy Premier League (Classic) and FPL Draft, the only truly actionable decision is **choosing players**:

- In Classic, that means deciding **who to buy/sell, who to start/bench, and who to captain** for each Gameweek.
- In Draft, that means **who to own**, **who to field**, and **who to trade/waiver in or out**.

Most of the time, I am choosing a player **for the next Gameweek**, so I primarily care about:

- How many **expected fantasy points (xPts)** the player will score in the **next fixture(s)**.
- How likely they are to **start and play enough minutes** to realize that upside.

Sometimes I choose players **for a longer horizon** (e.g. multiple upcoming Gameweeks or a long‑term draft pick).  
In those cases, **current statistical form matters less**, and **subjective conviction** (role, talent, tactical fit) matters more.

## Factors

At a high level, I choose players who are:

1. **In form statistically** (strong underlying numbers).
2. **Likely to start and play meaningful minutes** in the relevant fixture(s).

I think of the model in four main factor groups.

### 1. Stats (≈80% of the prediction)

**Statistics drive roughly 80% of my final prediction.**  
The core output I care about is **expected fantasy points (xPts)**, which I break down into components aligned with the scoring rules and glossary:

- **Goal threat**: mainly via **expected goals (xG)** and related shooting metrics.
- **Assist / creativity**: via **expected assists (xA)** and **expected goal involvement (xGI)**.
- **Clean sheets**: via **expected clean sheets (xCS)** or **clean‑sheet probability**, derived from **expected goals conceded (xGC)** and team strength.
- **Defensive contribution points**: via a notion of **expected defensive contribution (xDC)**, based on how often a player reaches the CBI/tackles thresholds.
- Optionally, **expected bonus points**, approximated from attacking/creative involvement, defensive contribution, and error proneness (BPS‑style inputs).

#### How we estimate xPts from stats

Currently, I estimate xPts roughly like this:

- For each player and **recent fixtures**, compute a set of **per‑fixture stats**:
  - xG, xA, xGI, shots, key passes, defensive actions, minutes, etc.
- **Normalize by Fixture Difficulty Rating (FDR)**:
  - Good output vs a high‑FDR (hard) opponent counts more than the same output vs a low‑FDR (easy) opponent.
  - Poor output vs a very tough fixture is penalized less than poor output vs an easy fixture.
- **Project these normalized stats into the next few fixtures**:
  - Use **upcoming FDRs** and team/role assumptions to scale the expected output.
- Convert projected xG, xA, xCS, xDC, etc. into **expected fantasy points** using the official scoring weights.

Clean‑sheet expectations are handled at the **team level** (since clean sheets are team properties), then mapped to individual players by position and minutes.

### 2. Availability Risks

Even if stats say a player is great, they are useless if they do not play.  
I try to capture **availability risk** as the probability that a player will **start and play enough minutes** in a fixture.

Inputs to this include:

- **Injury and fitness news**.
- **Suspensions**, travel, and personal issues.
- **Manager quotes and analyst commentary** (positive or negative).
- Recent **minutes and substitution patterns** (e.g. consistent 90s vs repeated early subs).

Ideally, the model would estimate something like:

- **P(start)** and **expected minutes** for the next fixture.
- Then scale xPts by **minutes / typical full‑match minutes** or a more explicit **minutes distribution**.

### 3. Tactical Analysis

Beyond raw availability, tactics affect both **role** and **upside**.

Examples:

- A midfielder might move into a more attacking role because of an injured teammate, increasing their **xG and xA**.
- A forward might be asked to press and defend more vs a top opponent, reducing goal threat but potentially boosting **defensive contribution**.
- A team’s shape may change (e.g. back three vs back four), shifting where chances come from and who benefits.

Some of this can be inferred from data over time, but much comes from **qualitative analysis** (watching games, reading reports).  
In the model, tactical analysis acts as a **modifier** on the statistical baseline (e.g. bumping or reducing projected xG/xA/xDC for specific fixtures).

### 4. Rotation and Squad Context

**Rotation** is about how a player fits into the club’s **depth chart** and schedule:

- If a first‑choice player has a strong backup in form, the starter may face **increased rotation risk**.
- If a starter gets injured, a backup may suddenly have **high minutes and xPts** in the next few fixtures.
- Congested schedules (European matches, cups) often increase rotation for **high‑minutes, high‑value** players.

In practical terms, rotation and squad context feed back into:

- **Availability risk** (P(start), expected minutes).
- Scenario planning for **medium‑term xPts**:
  - Some players are great one‑week punts but poor long‑term holds because of rotation risk.
  - Others are stable long‑term holds with slightly lower but more reliable xPts.

## How This Guides the Model

The “north star” for the prediction system is:

- **Primary target**: per‑player, per‑fixture **expected fantasy points (xPts)**.
- **Decomposition**: xPts comes from **xG, xA, xCS, xDC, expected bonus**, all normalized by **FDR** and scaled by **minutes and availability**.
- **Contextual adjustments**: tactical changes and rotation risk adjust the baseline probabilities rather than being bolted on afterwards.

As the code evolves, any concrete implementation should be traceable back to these concepts, and deviations from this framing should be explicit in the relevant module docs.
