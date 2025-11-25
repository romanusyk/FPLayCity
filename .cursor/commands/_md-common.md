---
description: Shared guidance for docs-md and plan-md commands
---
@docs/metadoc.md

You are about to create or update a Markdown document. Follow the Documentation North Star strictly: concise, direct language; top‑down structure; clear paths when linking; code references include symbol and path; avoid duplication; keep docs and code in lockstep.

Expected inputs (from the user/request):
- Target Markdown file path to create or update
- Brief description of intended changes and scope

Your responsibilities:
- Ensure completeness and validity of the document according to `@docs/metadoc.md`
- Ask brief, targeted clarification questions if anything is ambiguous; propose reasonable defaults when safe
- Keep documents small; if a section grows large, propose/perform a split into a separate file and link it with a one‑sentence overview + explicit path
- Place knowledge at the best level in the hierarchy (general vs module‑local); link to canonical sources rather than duplicating
- Reference code precisely by symbol and path; if code changed, note what doc updates are required

Validation checklist (apply before finalizing):
- Top‑down structure present (overview → components/flows → details)
- Links include a one‑sentence overview + explicit path
- Code references include symbol + file path
- No silent omissions of important context; surface unknowns explicitly
- File location follows placement rules (general in `docs/`, module docs alongside code, plans in `plans/`)

