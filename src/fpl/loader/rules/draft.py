"""
Extract FPL Draft rules from a saved HTML file and convert them to Markdown.

This module is intended to be run as a module with `uv run -m src.fpl.loader.rules.draft`.
It parses the FPL Draft Rules HTML page, extracts all relevant rules content (headings
and associated text), and writes a normalized Markdown file under `data/2025-2026/rules/`.
"""

from pathlib import Path

from .base import RulesExtractorConfig, run_extractor


ALLOWED_TOP_LEVEL_HEADINGS = {
    # FPL Draft "Rules" canonical sections (case-insensitive compare performed)
    # Based on the structure found in draft.html, these are the main h5 sections
    # under "Game Rules" (h3)
    "game entry",
    "leagues",
    "the draft",
    "transactions (unsigned players)",
    "transactions (player trades)",
    "managing your squad",
    "deadlines",
    "scoring",
}


def main():
    """Main function to extract rules from HTML and save as Markdown."""
    script_dir = Path(__file__).parent
    html_file = script_dir / "draft.html"
    output_file = Path("data/2025-2026/rules/draft.md")
    
    config = RulesExtractorConfig(
        html_path=html_file,
        output_path=output_file,
        allowed_top_level_headings=ALLOWED_TOP_LEVEL_HEADINGS,
        markdown_title="FPL Draft Rules"
    )
    
    run_extractor(config)


if __name__ == "__main__":
    main()

