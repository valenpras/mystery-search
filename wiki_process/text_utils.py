"""Shared text helpers for Wikipedia page processing."""

from __future__ import annotations

from typing import Any


def normalize_missing(value: Any) -> Any:
    """Map friend-cleaning sentinels to JSON null."""
    if value is None:
        return None
    if isinstance(value, str) and value.strip() in ("-", ""):
        return None
    return value


def format_infobox(infobox: dict[str, Any] | None) -> str:
    if not infobox:
        return ""
    return ". ".join(f"{k}: {v}" for k, v in infobox.items())


def extract_section_text(raw_content: Any) -> tuple[str, list[dict[str, Any]]]:
    """Return paragraph text and nested subsection nodes from a content field."""
    section_text = ""
    subsections: list[dict[str, Any]] = []

    if isinstance(raw_content, list):
        for item in raw_content:
            if isinstance(item, str):
                section_text += item + " "
            elif isinstance(item, dict):
                subsections.append(item)
            elif isinstance(item, list):
                for subitem in item:
                    if isinstance(subitem, str):
                        section_text += subitem + " "
                    elif isinstance(subitem, dict):
                        subsections.append(subitem)
    elif isinstance(raw_content, dict):
        subsections.append(raw_content)

    return section_text, subsections


def flatten_sections_tree(sections_tree: list[dict[str, Any]] | None) -> str:
    """Concatenate all section paragraph text into one body string."""

    parts: list[str] = []

    def walk(sections: list[dict[str, Any]]) -> None:
        for section in sections:
            text, subsections = extract_section_text(section.get("content", []))
            if text.strip():
                parts.append(text.strip())
            if subsections:
                walk(subsections)

    walk(sections_tree or [])
    return "\n\n".join(parts)


def llm_body_text(page: dict[str, Any], *, page_title: str) -> str:
    """Build encyclopedic body text for LLM input (excludes labeled location fields)."""
    chunks: list[str] = []
    summary = (page.get("summary") or "").strip()
    if summary:
        chunks.append(summary)

    infobox_str = format_infobox(page.get("infobox"))
    if infobox_str:
        chunks.append(f"Infobox: {infobox_str}")

    body = flatten_sections_tree(page.get("sections_tree"))
    if body:
        chunks.append(body)

    return "\n\n".join(chunks)
