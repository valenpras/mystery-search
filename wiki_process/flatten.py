"""Flatten enriched Wikipedia pages into Elasticsearch-ready JSONL."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from wiki_process.text_utils import extract_section_text, format_infobox, normalize_missing


def build_page_metadata(title: str, data: dict[str, Any]) -> dict[str, Any]:
    """Shared metadata copied onto every ES document for a page."""
    return {
        "page_title": title,
        "url": data.get("url"),
        "origin_title": data.get("origin_title"),
        "last_update": data.get("last_update_text"),
        "se_category": normalize_missing(data.get("SE_Category")),
        "se_location": normalize_missing(data.get("SE_Location")),
        "full_location": normalize_missing(data.get("Full Location")),
        "categories": data.get("categories") or [],
        "primary_location": data.get("primary_location"),
        "primary_location_explicit": data.get("primary_location_explicit"),
        "country": data.get("country"),
        "case_status": data.get("case_status"),
    }


def normalize_table_for_es(table: dict[str, Any]) -> dict[str, Any]:
    """Support both crawl table schemas (legacy section/headers/data and hierarchy/rows)."""
    if "section_hierarchy" in table or "rows" in table:
        return {
            "caption": table.get("caption"),
            "headers": _headers_from_rows(table.get("rows")),
            "rows": table.get("rows") or [],
            "section": _section_label_from_hierarchy(table.get("section_hierarchy")),
        }

    return {
        "caption": table.get("section"),
        "headers": table.get("headers") or [],
        "rows": table.get("data") or [],
        "section": table.get("section"),
    }


def _section_label_from_hierarchy(hierarchy: list[str] | None) -> str | None:
    if not hierarchy:
        return None
    return hierarchy[-1]


def _headers_from_rows(rows: list[Any] | None) -> list[str]:
    if not rows:
        return []
    first = rows[0]
    if not isinstance(first, list):
        return []
    headers: list[str] = []
    for cell in first:
        if isinstance(cell, dict) and cell.get("is_header"):
            headers.append(str(cell.get("text", "")))
        elif isinstance(cell, dict):
            headers.append(str(cell.get("text", "")))
    return headers


def build_table_map(title: str, tables: list[dict[str, Any]] | None) -> dict[str, list[dict[str, Any]]]:
    """Index tables by breadcrumb string for section attachment."""
    table_map: dict[str, list[dict[str, Any]]] = {}

    for table in tables or []:
        normalized = normalize_table_for_es(table)
        keys: set[str] = set()

        hierarchy = table.get("section_hierarchy")
        if hierarchy:
            keys.add(" > ".join([title, *hierarchy]))
        elif table.get("section"):
            keys.add(f"{title} > {table['section']}")

        for key in keys:
            table_map.setdefault(key, []).append(normalized)

    return table_map


def restructure_page(title: str, data: dict[str, Any]) -> list[dict[str, Any]]:
    """Turn one wiki page into many ES documents (lead + section chunks)."""
    docs: list[dict[str, Any]] = []
    page_metadata = build_page_metadata(title, data)

    summary = data.get("summary", "")
    infobox_str = format_infobox(data.get("infobox"))
    image_urls = [img.get("url") for img in data.get("images", []) if img.get("url")]

    lead_doc = {
        **page_metadata,
        "section_title": "Summary",
        "breadcrumb": [title],
        "content": summary,
        "infobox": infobox_str or None,
        "tables": [],
        "images": image_urls,
        "is_lead": True,
    }
    docs.append(lead_doc)

    table_map = build_table_map(title, data.get("table"))

    def walk_tree(sections: list[dict[str, Any]], parent_breadcrumb: list[str]) -> None:
        for section in sections:
            current_title = section.get("title", "")
            current_breadcrumb = parent_breadcrumb + [current_title]
            breadcrumb_str = " > ".join(current_breadcrumb)

            section_text, subsections = extract_section_text(section.get("content", []))

            relevant_tables = table_map.get(breadcrumb_str, [])
            if not relevant_tables and current_title:
                relevant_tables = table_map.get(f"{title} > {current_title}", [])

            if section_text.strip() or relevant_tables:
                docs.append(
                    {
                        **page_metadata,
                        "section_title": current_title,
                        "breadcrumb": current_breadcrumb,
                        "content": section_text.strip(),
                        "infobox": None,
                        "tables": relevant_tables,
                        "images": [],
                        "is_lead": False,
                    }
                )

            if subsections:
                walk_tree(subsections, current_breadcrumb)

    walk_tree(data.get("sections_tree", []), [title])
    return docs


def flatten_pages(pages: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    docs: list[dict[str, Any]] = []
    for title, data in pages.items():
        docs.extend(restructure_page(title, data))
    return docs


def save_jsonl(records: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")


def load_pages(path: Path) -> dict[str, dict[str, Any]]:
    with path.open(encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"expected dict keyed by page title, got {type(data).__name__}")
    return data


@dataclass
class FlattenConfig:
    input_path: Path
    output_path: Path
    limit: int | None = None


def run_flatten(cfg: FlattenConfig) -> None:
    pages = load_pages(cfg.input_path)
    items = list(pages.items())
    if cfg.limit is not None:
        items = items[: cfg.limit]
        pages = dict(items)

    docs = flatten_pages(pages)
    save_jsonl(docs, cfg.output_path)

    page_count = len(pages)
    print(
        f"Flattened {page_count} page(s) into {len(docs)} ES document(s) "
        f"-> {cfg.output_path}"
    )


def main(argv: list[str] | None = None) -> None:
    import argparse

    default_input = Path("data") / "wiki" / "cleaning_pages_db_enriched.json"
    default_output = Path("data") / "wiki" / "flat_wiki.jsonl"

    p = argparse.ArgumentParser(
        description="Flatten enriched Wikipedia JSON into Elasticsearch JSONL.",
    )
    p.add_argument(
        "--input",
        type=Path,
        default=default_input,
        help="Input pages JSON (enriched or raw cleaning_pages_db)",
    )
    p.add_argument(
        "--output",
        type=Path,
        default=default_output,
        help="Output flat JSONL for Elasticsearch",
    )
    p.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Flatten at most this many pages (for testing)",
    )
    args = p.parse_args(argv)

    run_flatten(
        FlattenConfig(
            input_path=args.input.resolve(),
            output_path=args.output.resolve(),
            limit=args.limit,
        )
    )


if __name__ == "__main__":
    main()
