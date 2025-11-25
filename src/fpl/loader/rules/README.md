# Overview
Extract the official Fantasy Premier League (FPL) and FPL Draft Rules from their websites and store them as Markdown. This module provides shared extraction logic (`base.py`) plus thin entry points for each game mode (`fpl.py`, `draft.py`).

# Key Concepts
- Only the Rules content is extracted (navigation, cookie banners, etc. are ignored).
- Allowed top‑level sections are constrained per game mode to keep the output stable.
- The extractor prefers structured DOM parsing and falls back to a heuristic text mode if needed.
- Per project policy, failures are loud (missing inputs or empty parses raise exceptions) to protect data completeness.

# Components
- `RulesExtractorConfig` in `src/fpl/loader/rules/base.py`: Holds HTML path, output path, allowed headings, and markdown title.
- `find_rules_content` / `extract_from_text_content` in `src/fpl/loader/rules/base.py`: Extract Rules sections from structured HTML or plain text.
- `convert_to_markdown` / `run_extractor` in `src/fpl/loader/rules/base.py`: Build final Markdown and write it to `data/2025-2026/rules/`.
- `main` in `src/fpl/loader/rules/fpl.py` and `src/fpl/loader/rules/draft.py`: Configure game‑specific settings and invoke `run_extractor`.

# Usage

## 1) Save the Rules HTML locally (temporary files)
- FPL (classic): Open `https://fantasy.premierleague.com/help/rules`, save the page as `fpl.html`, and place it at `src/fpl/loader/rules/fpl.html`.
- FPL Draft: Open `https://draft.premierleague.com/help` (Rules tab), save the page as `draft.html`, and place it at `src/fpl/loader/rules/draft.html`.

These HTML files are local inputs only and should not be committed.

## 2) Run the extractors (single supported mode)
From the repository root:

```bash
uv run -m src.fpl.loader.rules.fpl
uv run -m src.fpl.loader.rules.draft
```

Expected outputs:
- `data/2025-2026/rules/fpl.md`
- `data/2025-2026/rules/draft.md`

# Failure Modes
- `FileNotFoundError`: HTML input missing at the configured path.
- `ValueError`: No allowed Rules sections were found in the parsed HTML/text.

In both cases, fix the HTML snapshot or configuration and rerun the extractor.
