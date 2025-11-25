## Documentation North Star (for humans and AIs)

### Purpose
Set clear, concise, top‑down standards so both humans and AIs can understand, navigate, and reuse project knowledge without duplication.

### Audience
- Humans and AIs. Write so both can parse quickly: minimal fluff, explicit paths and names.

### Style
- Concise, direct, simple language.
- Prefer short sentences. Avoid jargon unless defined.

### Structure (top‑down, always)
1) High‑level goals and concepts
2) Key components/modules and how they fit
3) Important flows/algorithms/APIs
4) Low‑level details, edge cases, references

Include a brief outline at the top when helpful.

### Where docs live
- `docs/`: general, cross‑cutting knowledge (project vision, conventions, standards, processes).
- Any subdirectory (mostly `src/`) may contain `.md` files that explain that module’s purpose, structure, and usage. Keep module docs near the code they describe.

### Linking and nesting
- Any document can link to other `.md` files.
- When referencing another doc, always provide:
  - A one‑sentence overview of what it covers (why a reader should open it)
  - A clear path to the file

Example:

```markdown
See "Fotmob adapter design" — high‑level data flow and parsing rules — at `src/fpl/models/fotmob_adapter.md`.
```

### Placement and duplication
- Put knowledge at the most appropriate level of the hierarchy to maximize understandability and minimize repetition.
- Use the closest canonical source and link to it rather than duplicating content.

### File size and granularity
- Prefer small, focused docs. If a section grows wordy, move it to a separate file and link it.
- Granular files make it easy to include just‑enough context in AI prompts.

### Referencing code
- When mentioning code, state both the symbol and its path.
- Format: `SymbolName` in `path/to/file.py` (and include class/method/function when relevant).

Examples:

```markdown
Class: `FotmobAdapter` in `src/fpl/models/fotmob_adapter.py`
Method: `FotmobAdapter.fetch_fixture_data` in `src/fpl/models/fotmob_adapter.py`
```

If code changes, update the docs in the same change set whenever possible.

### Code documentation preferences
- Prefer file/class/method/function docstrings over inline comments.
- Use the same concise, top‑down style as `.md` docs.
- Inline comments are reserved for truly non‑obvious intent, invariants, or caveats.

Minimal Python docstring example:

```python
class FotmobAdapter:
    """Top-level: Purpose and role in the system.

    Key responsibilities:
    - What it does (1–3 bullets)
    - Inputs/outputs at a high level
    - Critical invariants or assumptions
    """

    def fetch_fixture_data(self, fixture_id: int) -> dict:
        """Fetch fixture data from source and return normalized dict.

        Parameters:
        - fixture_id: Provider identifier for the match/fixture.

        Returns:
        - Normalized fixture payload ready for downstream processing.
        """
        ...
```

### Suggested module doc skeleton (in-module `.md`)

```markdown
# Overview
What this module does and why it exists (2–4 sentences).

# Key Concepts
Core ideas, models, and constraints.

# Components
Main classes/functions with one-line roles. Reference symbols with paths.

# Data/Control Flow
Primary flows and how parts interact.

# Public API (if applicable)
Entry points and expected inputs/outputs.

# Key Paths
Explicit file paths for discovery (e.g., `src/.../file.py`).

# Related Docs
Brief overviews + paths to linked docs.
```

### Maintenance
- Keep docs and code in lockstep. When renaming/moving code, update affected `.md` and docstrings.
- Prefer linking to a single canonical description over copying text.

### Notes
- This project prioritizes clarity and completeness of knowledge. If a fact is uncertain or missing, surface it explicitly rather than papering over it.*** End Patch

