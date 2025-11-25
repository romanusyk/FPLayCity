#!/usr/bin/env python3
"""
Extract FPL rules from HTML file and convert to Markdown.

This script parses the FPL rules HTML file and extracts all rules content,
including headings and their associated text, then saves it as markdown.
"""

from pathlib import Path
import sys

# Allow running as script or module
if __name__ == "__main__":
    # When running as script, add parent directory to path
    sys.path.insert(0, str(Path(__file__).parent))
    from base import RulesExtractorConfig, run_extractor
else:
    from .base import RulesExtractorConfig, run_extractor


ALLOWED_TOP_LEVEL_HEADINGS = {
    # FPL "Rules" canonical sections (case-insensitive compare performed)
    "selecting your initial squad",
    "managing your squad",
    "transfers",
    "scoring",
    "leagues",
    "cups",
}


def main():
    """Main function to extract rules from HTML and save as Markdown."""
    script_dir = Path(__file__).parent
    html_file = script_dir / "fpl.html"
    output_file = Path("data/2025-2026/rules/fpl.md")
    
    config = RulesExtractorConfig(
        html_path=html_file,
        output_path=output_file,
        allowed_top_level_headings=ALLOWED_TOP_LEVEL_HEADINGS,
        markdown_title="FPL Rules"
    )
    
    run_extractor(config)


if __name__ == "__main__":
    main()

