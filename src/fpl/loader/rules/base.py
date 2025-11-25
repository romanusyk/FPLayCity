#!/usr/bin/env python3
"""
Shared HTML-to-Markdown rules extractor for Fantasy Premier League variants.

This module provides the core extraction logic used by both FPL (classic) and
FPL Draft rules extractors. Game-specific configuration is passed via RulesExtractorConfig.
"""

import re
from dataclasses import dataclass
from pathlib import Path
from bs4 import BeautifulSoup, NavigableString, Tag


@dataclass
class RulesExtractorConfig:
    """Configuration for rules extraction."""
    html_path: Path
    output_path: Path
    allowed_top_level_headings: set[str]
    markdown_title: str


def extract_text_content(element, indent: int = 0) -> str:
    """Extract text content from an element, preserving structure."""
    if element is None:
        return ""
    
    # Convert lists to proper Markdown (support nested lists and numbering)
    if element.name in ["ul", "ol"]:
        items = []
        for idx, li in enumerate(element.find_all("li", recursive=False), start=1):
            bullet = f"{idx}. " if element.name == "ol" else "- "

            main_parts = []
            nested_blocks = []
            for child in li.children:
                if isinstance(child, NavigableString):
                    text_child = str(child).strip()
                    if text_child:
                        main_parts.append(text_child)
                elif isinstance(child, Tag):
                    if child.name in ["ul", "ol"]:
                        nested_blocks.append(extract_text_content(child, indent + 1))
                    else:
                        main_parts.append(extract_text_content(child, indent))
            main_text = " ".join(part for part in main_parts if part).strip()
            prefix = "  " * indent + bullet
            items.append(prefix + main_text if main_text else prefix.rstrip())

            for nested in nested_blocks:
                for line in nested.splitlines():
                    if line.strip():
                        items.append("  " * (indent + 1) + line)

        return "\n".join(items)

    # Paragraphs: keep internal <br> as newlines and preserve paragraph boundaries
    if element.name == "p":
        text = element.get_text(separator="\n", strip=True)
        text = re.sub(r"[ \t]+\n", "\n", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text

    # Treat generic containers by walking immediate block children
    if element.name in ["div", "section", "article", "span"]:
        blocks = []
        for child in element.children:
            if isinstance(child, NavigableString):
                raw = str(child).strip()
                if raw:
                    blocks.append(raw)
            elif isinstance(child, Tag):
                if child.name in ["p", "ul", "ol", "br"]:
                    blocks.append(extract_text_content(child, indent))
        if blocks:
            return "\n\n".join(b for b in blocks if b)
        # Fallback to flat text
        return element.get_text(separator=" ", strip=True)

    if element.name == "br":
        return ""

    # Fallback: flat text
    text = element.get_text(separator=" ", strip=True)
    text = re.sub(r"\s{2,}", " ", text)
    return text


def element_contains_heading(element) -> bool:
    """Return True if the element subtree contains any heading tags."""
    if not hasattr(element, "find_all"):
        return False
    return bool(element.find_all(["h1", "h2", "h3", "h4", "h5", "h6"]))


def find_rules_content(soup, allowed_top_level_headings: set[str]):
    """
    Find the main rules content in the HTML.
    
    Since this is a React app, we need to look for content in various places:
    1. In the body's rendered content
    2. In script tags with JSON data
    3. In data attributes
    """
    content_sections = []
    
    # Try to find the main content area
    # Look for common content containers
    main_content = soup.find("main") or soup.find("div", id="root") or soup.find("body")
    
    if main_content:
        # Find all headings (h1-h6)
        headings = main_content.find_all(["h1", "h2", "h3", "h4", "h5", "h6"])
        
        # Track nesting to know whether we are inside an allowed Rules top-level section
        stack = []  # list of dicts: {"level": int, "text": str, "included": bool}
        for heading in headings:
            heading_text = heading.get_text(strip=True)
            
            # Skip navigation and footer headings
            if any(skip in heading_text.lower() for skip in ["navigation", "footer", "cookie", "sponsor", "menu"]):
                continue
            
            # Skip empty headings
            if not heading_text:
                continue
            
            # Determine heading level
            level = int(heading.name[1])
            
            # Maintain stack
            while stack and stack[-1]["level"] >= level:
                stack.pop()
            parent_included = any(
                h["included"] and h["level"] in (1, 2, 3, 4, 5) for h in stack
            )
            is_allowed_top = (heading_text.lower() in allowed_top_level_headings and level in (1, 2, 3, 4, 5))
            included_here = is_allowed_top or parent_included
            stack.append({"level": level, "text": heading_text, "included": included_here})
            if not included_here:
                # Skip headings outside the target "Rules" sections
                continue
            
            # Get content following this heading
            content_parts = []
            current = heading.next_sibling
            
            # Collect content until we hit another heading of same or higher level
            while current:
                if isinstance(current, str):
                    text = current.strip()
                    if text:
                        content_parts.append(text)
                elif hasattr(current, "name"):
                    if current.name in ["h1", "h2", "h3", "h4", "h5", "h6"]:
                        next_level = int(current.name[1])
                        if next_level <= level:
                            break
                    elif current.name in ["p", "div", "span", "ul", "ol", "li"]:
                        # Intentionally include containers even if they have nested headings.
                        # Per project policy we prefer data completeness over de‑duplication:
                        # it's better to capture paragraphs/lists once too often than to
                        # silently drop entire Rules sections because their wrappers also
                        # contain sub‑headings (common on the FPL Draft Rules page).
                        text = extract_text_content(current)
                        if text:
                            content_parts.append(text)
                
                current = current.next_sibling
            
            # Combine content
            content = "\n\n".join(filter(None, content_parts))
            
            if heading_text or content:
                content_sections.append({
                    "level": level,
                    "heading": heading_text,
                    "content": content
                })
    
    return content_sections


def extract_from_text_content(soup, allowed_top_level_headings: set[str]):
    """
    Alternative extraction: look for text patterns that indicate rules.
    """
    body = soup.find("body")
    if not body:
        return []
    
    # Get all text content
    text = body.get_text(separator="\n", strip=True)
    
    # Look for patterns that indicate rules sections
    # This is a fallback if structured extraction doesn't work
    lines = text.split("\n")
    sections = []
    current_heading = None
    current_level = 0
    current_content = []
    
    for line in lines:
        line = line.strip()
        if not line:
            continue
        
        # Check if line looks like a heading (short, capitalized, etc.)
        # This is heuristic-based
        if len(line) < 100 and (
            line.isupper() or
            (line[0].isupper() and line.count(" ") < 10) or
            re.match(r"^[A-Z][a-z]+(\s+[A-Z][a-z]+)*$", line)
        ):
            # Save previous section
            if current_heading:
                content = "\n".join(current_content).strip()
                if content:
                    sections.append({
                        "level": current_level,
                        "heading": current_heading,
                        "content": content
                    })
            
            # Start new section
            current_heading = line
            current_level = 2  # Default to h2
            current_content = []
        else:
            if current_heading:
                current_content.append(line)
    
    # Save last section
    if current_heading:
        content = "\n".join(current_content).strip()
        if content:
            sections.append({
                "level": current_level,
                "heading": current_heading,
                "content": content
            })
    
    # Filter to only allowed top-level buckets
    filtered = []
    inside_allowed = False
    for sec in sections:
        if sec["heading"].lower() in allowed_top_level_headings:
            inside_allowed = True
            filtered.append(sec)
            continue
        if inside_allowed:
            filtered.append(sec)
    return filtered


def convert_to_markdown(sections, markdown_title: str):
    """Convert extracted sections to markdown format."""
    markdown_lines = [f"# {markdown_title}", ""]
    
    for section in sections:
        level = section["level"]
        heading = section["heading"]
        content = section["content"]
        
        # Add heading
        markdown_lines.append(f"{'#' * level} {heading}")
        markdown_lines.append("")
        
        # Add content
        if content:
            # Normalize multiple blank lines
            normalized = re.sub(r"\n{3,}", "\n\n", content.strip())
            markdown_lines.append(normalized)
            markdown_lines.append("")
    
    return "\n".join(markdown_lines)


def run_extractor(config: RulesExtractorConfig) -> None:
    """
    Main extraction workflow: read HTML, parse, extract sections, validate, convert to Markdown, write.
    
    Per project policy, fails loudly on missing/invalid inputs or unexpectedly empty parses.
    """
    if not config.html_path.exists():
        raise FileNotFoundError(f"HTML file not found: {config.html_path}")
    
    print(f"Reading HTML file: {config.html_path}")
    with open(config.html_path, "r", encoding="utf-8") as f:
        html_content = f.read()
    
    print("Parsing HTML...")
    soup = BeautifulSoup(html_content, "html.parser")
    
    print("Extracting rules content...")
    sections = find_rules_content(soup, config.allowed_top_level_headings)
    
    # If we didn't find much content, try alternative extraction
    if len(sections) < 5:
        print("Trying alternative extraction method...")
        alt_sections = extract_from_text_content(soup, config.allowed_top_level_headings)
        if len(alt_sections) > len(sections):
            sections = alt_sections
    
    # Fail loudly if we don't have any allowed rule sections
    if not sections:
        raise ValueError(
            f"No rules content found. Ensure the HTML is the '{config.markdown_title}' page and that "
            f"top-level sections exist in the allowed set: {sorted(config.allowed_top_level_headings)}"
        )
    # Sanity-check that at least one allowed top-level section was captured
    if not any(sec["heading"].lower() in config.allowed_top_level_headings for sec in sections):
        raise ValueError(
            "Parsed content did not include any known Rules top-level sections. "
            "We only output rules, not general site content. Please verify the input HTML."
        )
    
    print(f"Found {len(sections)} sections")
    
    print("Converting to Markdown...")
    markdown_content = convert_to_markdown(sections, config.markdown_title)
    
    # Ensure output directory exists
    config.output_path.parent.mkdir(parents=True, exist_ok=True)
    print(f"Writing Markdown to: {config.output_path}")
    with open(config.output_path, "w", encoding="utf-8") as f:
        f.write(markdown_content)
    
    print(f"Successfully extracted rules to {config.output_path}")

