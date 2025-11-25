## Overview

This document describes the **north star product vision** for the Fantasy Premier League assistant.  
It focuses on **what** the product should do (capabilities and guarantees), not **how** it is implemented.  
For model goals and factor definitions, see **"Prediction Model: The North Star"** at `docs/model_ns.md`.

The product has three tightly connected surfaces:

- **API**: canonical source of model outputs and raw evidence.
- **UI app**: interactive player explorer and profile view.
- **AI chat**: opinionated, explainable assistant grounded in the API.

Across all three, a core principle applies:

- **Confirmability**: every prediction, judgment, or label must be backed by concrete, inspectable data.

---

## 1. API North Star

The API is the **single source of truth** for all player data, predictions, and supporting evidence.

- **List players (players collection endpoint)**:
  - Returns **all players** with fields needed for **querying, filtering, and sorting**, including (non‑exhaustive:
    - Identity: player id, name, team, position, price.
    - **Current and projected stats**: recent per‑fixture/per90 stats, FDR‑normalized metrics, xG/xA/xGI/xCS/xDC, expected fantasy points (xPts) over specified horizons.
    - **Availability and role**: `p_start` (probability of starting), expected minutes, recent minutes trend, qualitative role/position tags where available.
    - **Derived rotation and risk metrics**: e.g. `rotation_risk_score`, `injury_status`, `manager_verdict`, and similar fields that summarise risk and context.
  - Accepts parameters to control **history and horizon**, such as:
    - Number of historical gameweeks to use (for form and trends).
    - Prediction horizon (e.g. single GW, short block of GWs, or longer window).
  - Designed to support **flexible client‑side sorting and filtering** without additional bespoke endpoints.

- **Player details (single‑player endpoint)**:
  - Returns a **richer player profile** with everything from the list view plus:
    - A **clear breakdown of how predictions were built**:
      - Contributions from xG, xA, xCS, xDC, and any other components used.
      - Any adjustments for fixture difficulty, minutes, or tactical/rotation assumptions.
    - **Evidence for availability and rotation**:
      - Recent minutes history and substitution patterns.
      - Rivalry/rotation information (e.g. competitors for minutes, sub‑in/out counts).
    - **News and manager quotes**:
      - Key extracted snippets relevant to injury, role, and selection.
      - **Source URLs** pointing to the full article/press conference text.
    - Any **derived flags** (e.g. “likely to start”, “high rotation risk”) with enough context to justify them.

### Confirmability Principle

Across all API responses, **any strong statement must be traceable to raw data**:

- If the system states or implies:
  - “This player is projected to score **X points** next gameweek,”
  - “This player is **likely to be benched/started**,”
  - “This player has **high rotation risk**,”
  - or similar,
- Then the API must expose sufficient underlying evidence so the user can **confirm or challenge** that statement, including:
  - The underlying **model inputs and intermediate metrics** (e.g. recent xG/xA, minutes, fixture difficulty, team defensive strength).
  - Relevant **rivalry and rotation data** (who competes for the spot, how often they sub for each other).
  - Relevant **news items and quotes with URLs**.

The API should never return a “magic” judgment without providing the **data that supports it**.

---

## 2. UI App North Star

The UI is a **player exploration and profiling tool** built on top of the API.

- **Core layout**:
  - A main **players table**:
    - Shows a dense list of players with key columns (team, position, price, xPts, availability/risk, key stats).
    - Supports **interactive sorting and filtering** by any relevant metric (e.g. xPts, xG/xA, price, P(start), rotation risk).
  - A **right‑side player profile panel**:
    - Opens when a player row is selected.
    - Shows a **richer, layered view** of the player:
      - Headline prediction summary (xPts and risk).
      - Recent stats and form (charts or compact tables).
      - Availability and rotation context (minutes trend, rivals, risk scores).
      - Extracted news and manager quotes with links.

- **Confirmability in the UI**:
  - Any strong label, summary, or badge (e.g. “high rotation risk”, “likely starter”, “injury doubt”) must:
    - Be backed by **visible evidence** in the profile panel (raw metrics, timelines, rival lists, quotes).
    - Allow the user to **drill from summary → underlying data** (e.g. expand a minutes chart, show list of rival fixtures, open source URLs in a browser).
  - The UI should make it **obvious where each conclusion comes from**, not just show opaque scores.

- **Future but explicitly out‑of‑scope for the initial north star**:
  - **“My squad” overlay**:
    - Ability to overlay the user’s own squad on the table and profiles (e.g. highlight owned vs target players).
    - This feature is desirable and part of the long‑term vision but **not required in the initial product**; for now it is only noted as a future extension.
  - **Saved filters and views**:
    - User‑defined presets for common searches (e.g. “budget defenders next 3 GWs”).
    - Also a desirable future enhancement but **explicitly out of the immediate scope**.

---

## 3. AI Chat North Star

The AI chat is a **second‑opinion assistant** that consumes the same API as the UI, accessed via MCP (Model Context Protocol).

- **Role**:
  - Provide **structured, explainable analysis** of:
    - Individual players.
    - Head‑to‑head comparisons.
    - Small groups of players (e.g. pick N options for a slot).
  - Help counteract personal bias by offering a **consistent, data‑grounded perspective**.

- **Data source and grounding**:
  - AI chat **does not use its own private data** about players; instead, it:
    - Calls the same **API player listing and player details** endpoints as the UI.
    - Bases every judgment on **numbers and evidence** exposed by those endpoints.
  - For any recommendation, it should:
    - Reference **xPts and key components** (xG/xA/xCS/xDC, minutes, risk scores).
    - Point to **supporting evidence** (e.g. minutes trend, rivals, quotes) in a way that the user can cross‑check via the UI or raw API.

- **No special comparison endpoint required**:
  - The API does **not** need dedicated comparison endpoints (e.g. `/compare`).
  - Comparison logic lives in the **AI layer and/or MCP prompts**, which:
    - Orchestrate calls to the existing player list/details endpoints.
    - Implement comparison and selection logic in the AI side while remaining fully grounded in API data.

The end state is that **API, UI, and AI chat** all reflect the same underlying model and evidence, and the user can always **see and verify** why the system thinks what it thinks.


