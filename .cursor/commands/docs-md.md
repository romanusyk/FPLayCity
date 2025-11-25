---
name: docs-md
description: Create or update a documentation Markdown file
---
@.cursor/commands/_md-common.md

Goal
- Create or update a project documentation `.md` file following `@docs/metadoc.md`.

Inputs (provide or confirm)
- Target doc path (e.g., `docs/topic.md` or `src/.../module.md`)
- Brief change request (bullets are fine)

Steps
1) Determine correct placement:
   - General/cross‑cutting → `docs/`
   - Module‑specific → place `.md` alongside the module (e.g., `src/.../module.md`)
2) If creating a module doc, prefer this skeleton:
   - Overview (2–4 sentences: purpose and role)
   - Key Concepts
   - Components (main classes/functions; reference symbols + paths)
   - Data/Control Flow
   - Public API (if applicable)
   - Key Paths (explicit file paths)
   - Related Docs (one‑sentence overview + path)
3) If updating an existing doc, refactor to top‑down, remove duplication, and add explicit links/paths.
4) Keep file small; if large, split and link with one‑sentence overviews.
5) Validate with the checklist in `_md-common.md`.

Output
- Provide the final Markdown content ready to write to the target path.
- If anything is uncertain, list concise clarifying questions and proposed defaults.

