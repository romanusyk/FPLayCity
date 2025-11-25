---
name: plan-md
description: Create or update an implementation plan Markdown file under plans/
---
@.cursor/commands/_md-common.md

Goal
- Create or update an implementation plan `.md` under `plans/` following `@docs/metadoc.md`.

Inputs (provide or confirm)
- Plan file path (default: `plans/<date>-<short-title>.md`)
- Brief change request or high‑level objective

Steps
1) Ensure file resides in `plans/`. Create folder if missing.
2) Use top‑down structure. Recommended skeleton:
   - Title and Summary (2–4 sentences: what and why)
   - Background and Context (link related docs with one‑sentence overviews + paths)
   - Goals / Non‑Goals
   - Scope and Assumptions
   - Approach (high‑level design, data/control flows; reference code symbols + paths)
   - Milestones / Tasks (concise, outcome‑oriented bullets)
   - Risks / Open Questions (with next steps to resolve)
   - Acceptance Criteria / Validation
3) Keep it concise; split deep details into separate docs in `plans/` or module docs and link.
4) Validate with the checklist in `_md-common.md`.

Output
- Provide the final Markdown content ready to write to the target path.
- If anything is uncertain, list concise clarifying questions and proposed defaults.

