## Overview

This document describes the **high‑level design north star** for delivering the product in `docs/product_ns.md`.  
It focuses on major components, boundaries, and protocols, not low‑level implementation details.  
All components are expected to stay aligned with **"Prediction Model: The North Star"** at `docs/model_ns.md`.

The system has three main technical pieces:

- **HTTP API**: Fast, typed, inspectable service exposing player lists and details.
- **UI app**: React‑based SPA for exploration and profiling.
- **AI chat**: MCP server built from the API’s OpenAPI spec, plus opinionated prompts.

---

## 1. API Design

The API is a **Python service layer** (e.g. FastAPI) that:

- Uses **Pydantic models** for all request/response schemas.
- Exposes a complete, accurate **OpenAPI specification** that other components (including MCP) can consume.
- Wraps the underlying prediction/model logic (e.g. pipelines and loaders) without hiding important details.

### Endpoints

- **`GET /players`**
  - Purpose:
    - Serve as the **main collection endpoint** for table views and bulk queries.
  - Behavior:
    - Returns a list of **player summary objects** with all fields needed for:
      - Sorting (e.g. xPts, price, xG/xA, P(start), rotation risk).
      - Filtering (e.g. position, team, price range, availability thresholds).
    - Accepts query parameters to control:
      - Historical window (e.g. number of past gameweeks used for form and trends).
      - Prediction horizon (e.g. single GW or block of GWs).
      - Optional filters (position, team, price ranges, minimum minutes, risk ranges, etc.).
  - Contract:
    - All returned fields must be **well‑typed** via Pydantic and documented in OpenAPI.
    - The endpoint is **idempotent and read‑only**.

- **`GET /players/{player_id}`**
  - Purpose:
    - Provide a **detailed player profile** used by the UI side panel and AI analysis.
  - Behavior:
    - Returns a single **player detail object** that:
      - Includes all relevant summary fields from `GET /players`.
      - Adds:
        - A **structured breakdown of prediction components** (e.g. xG/xA/xCS/xDC contributions, minutes adjustments).
        - Recent stats and trends (per‑fixture/per90 metrics, FDR‑adjusted where relevant).
        - **Availability and rotation evidence** (minutes history, rivals and substitution patterns, risk scores).
        - **News items and manager quotes** with URLs to original sources.
      - Is shaped to make the **confirmability principle** in `docs/product_ns.md` straightforward to implement in the UI and AI chat.
  - Contract:
    - Uses a well‑defined Pydantic model, fully described in OpenAPI.
    - Fails loudly if required data is missing or inconsistent, rather than silently omitting fields.

### Technology and standards

- **Framework**:
  - FastAPI is the preferred implementation choice, given:
    - First‑class support for Pydantic models.
    - Automatic OpenAPI generation.
    - Async I/O for integration with existing loaders and HTTP clients.
- **Schema and documentation**:
  - The OpenAPI spec is treated as a **public contract**:
    - Kept in sync with code.
    - Used directly by the MCP server and optionally by other tools.

---

## 2. UI App Design

The UI app is a **React single‑page application** built for dense, interactive data exploration.

- **Technology choices**:
  - **React** for component model and ecosystem.
  - **TanStack Table (or similar)** for:
    - Efficient rendering and virtualization of large player tables.
    - Column‑level sorting, filtering, and visibility toggles.
  - Explicitly **not** a Streamlit or Dash app:
    - The UI is intended as a long‑lived SPA, not a quick notebook‑style dashboard.

- **High‑level structure**:
  - A main **players table view**:
    - Fetches data from `GET /players`.
    - Implements:
      - Client‑side sorting on relevant metrics.
      - Client‑side filtering and column selection on top of server‑side query parameters where appropriate.
    - Keeps the shape of row objects as close as practical to the `GET /players` response.
  - A **right‑hand player profile panel**:
    - Triggered when a row is selected.
    - Fetches full data via `GET /players/{player_id}`.
    - Renders:
      - Headline prediction summary (e.g. xPts, P(start), key flags).
      - Stats and trends (tables or small charts).
      - Availability, rotation, and rivalry information.
      - News/quote snippets with clickable URLs.

- **Confirmability in design**:
  - The UI is responsible for making **evidence easy to inspect**:
    - Summaries and badges should link visually to the underlying raw data (e.g. hover to show explanation, expand to show sources).
    - Data needed for confirmability must already be exposed by the API; the UI should not invent opaque judgments.
  - Features like **“My squad” overlay** and **saved filters/views** are:
    - Recognized as part of the long‑term UX vision.
    - Explicitly **out of scope for the initial implementation**, and may be defined in a separate doc when prioritized.

---

## 3. AI Chat / MCP Design

The AI chat surface is implemented as an **MCP server** that consumes the API’s OpenAPI spec and exposes tools and prompts tailored to FPL decision‑making.

- **Core mechanism**:
  - Build the MCP server using `FastMCP.from_openapi()` (or equivalent) to:
    - Automatically generate tools that mirror:
      - `GET /players`
      - `GET /players/{player_id}`
    - Ensure the server stays in sync with the HTTP API as defined by OpenAPI.
  - The MCP server becomes the **single integration point** between the LLM and the FPL API.

- **MCP tools**:
  - Auto‑generated tools for:
    - Listing players with filters (mirroring `GET /players`).
    - Fetching detailed data for a specific player (mirroring `GET /players/{player_id}`).
  - These tools enforce:
    - Correct parameter schemas (via OpenAPI).
    - Consistent response shapes for use in prompts.

- **MCP prompts (guardrails and higher‑level operations)**:
  - On top of the raw tools, define **opinionated MCP Prompts** for common workflows, such as:
    - **Compare two players**:
      - Fetch both players’ data.
      - Summarize and contrast xPts, risk, fixtures, and evidence.
    - **Select N players by position**:
      - E.g. “select 3 midfielders for the next 2–3 GWs,” with or without price constraints.
      - Use filters via `GET /players` plus ranking logic inside the prompt.
    - **Budget‑aware or budget‑agnostic selection**:
      - Prompts can specify whether to include price as a constraint or ignore it.
  - These prompts act as **guardrails**:
    - They define exactly how the model may call the tools.
    - They bias the assistant towards **transparent, metric‑driven explanations** (referencing xPts, xG/xA, P(start), risk scores, and evidence links).

- **Separation of concerns**:
  - The **HTTP API** remains responsible for data correctness, completeness, and confirmability.
  - The **MCP server** handles:
    - Tool exposure (based on OpenAPI).
    - Prompt templates and comparison/selection logic.
  - The **LLM** provides narrative explanations and rankings, but always grounded in data retrieved via MCP tools.

Future, more detailed prompt specifications (exact comparison rules, tie‑breakers, horizon handling, etc.) can live in a separate AI/UX‑focused doc once the core API and MCP scaffolding are in place.


