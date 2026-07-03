"""Map Elasticsearch page section hits to Mystery Search detail API JSON."""

from __future__ import annotations

import re
from typing import Any

from search_api.format_results import (
    SOURCE_WIKIPEDIA,
    _case_status_from_source,
    _category_from_source,
    _country_from_source,
    make_doc_id,
)

_SECTION_ID_RE = re.compile(r"[^a-z0-9]+")
RELATED_SEED_MAX_LEN = 1500


def _slug_section_id(section_title: str) -> str:
    slug = _SECTION_ID_RE.sub("-", section_title.lower()).strip("-")
    return slug or "section"


def _primary_location_from_source(src: dict[str, Any]) -> str | None:
    for key in ("primary_location", "full_location", "se_location", "location"):
        val = src.get(key)
        if val and str(val).strip() not in ("", "-", "null"):
            return str(val).strip()
    return None


def _primary_location_explicit_from_source(src: dict[str, Any]) -> bool | None:
    val = src.get("primary_location_explicit")
    if val is None:
        return None
    return bool(val)


def _normalize_images(raw: Any) -> list[str]:
    if not raw:
        return []
    urls: list[str] = []
    for item in raw:
        if isinstance(item, str) and item.strip():
            urls.append(item.strip())
        elif isinstance(item, dict) and item.get("url"):
            urls.append(str(item["url"]).strip())
    return urls


def _normalize_tables(raw: Any) -> list[dict[str, Any]]:
    if not raw or not isinstance(raw, list):
        return []
    return [t for t in raw if isinstance(t, dict)]


def _parse_infobox_string(infobox: str | None) -> list[dict[str, str]]:
    """Split flattened infobox text (key: val. key: val) into display rows."""
    if not infobox or not str(infobox).strip():
        return []

    rows: list[dict[str, str]] = []
    for part in str(infobox).split(". "):
        part = part.strip()
        if not part:
            continue
        if ": " in part:
            label, _, value = part.partition(": ")
            label = label.strip()
            value = value.strip()
            if label or value:
                rows.append({"label": label, "value": value})
        else:
            rows.append({"label": "", "value": part})
    return rows


def _chunk_order_from_source(src: dict[str, Any]) -> int | None:
    val = src.get("chunk_order")
    if val is None:
        return None
    try:
        return int(val)
    except (TypeError, ValueError):
        return None


def _legacy_source_sort_key(src: dict[str, Any]) -> tuple:
    is_lead = 0 if src.get("is_lead") else 1
    breadcrumb = src.get("breadcrumb") or []
    depth = len(breadcrumb) if isinstance(breadcrumb, list) else 0
    title = str(src.get("section_title") or "")
    return (is_lead, depth, title)


def _section_sort_key(hit: dict[str, Any]) -> tuple:
    src = hit.get("_source") or {}
    order = _chunk_order_from_source(src)
    if order is not None:
        return (0, order)
    legacy = _legacy_source_sort_key(src)
    return (1, legacy[0], legacy[1], legacy[2])


def _hit_to_section(hit: dict[str, Any]) -> dict[str, Any]:
    src = hit.get("_source") or {}
    section_title = str(src.get("section_title") or "Section")
    breadcrumb = src.get("breadcrumb")
    if not isinstance(breadcrumb, list):
        breadcrumb = [str(breadcrumb)] if breadcrumb else []

    section: dict[str, Any] = {
        "section_id": _slug_section_id(section_title),
        "section_title": section_title,
        "breadcrumb": breadcrumb,
        "is_lead": bool(src.get("is_lead")),
        "content": str(src.get("content") or "").strip(),
        "images": _normalize_images(src.get("images")),
        "tables": _normalize_tables(src.get("tables")),
    }
    order = _chunk_order_from_source(src)
    if order is not None:
        section["chunk_order"] = order
    return section


def _page_metadata_from_sources(sources: list[dict[str, Any]]) -> dict[str, Any]:
    """Pick page-level fields; prefer lowest chunk_order / lead section."""
    ordered = sorted(
        sources,
        key=lambda s: (
            _chunk_order_from_source(s)
            if _chunk_order_from_source(s) is not None
            else 999999,
            0 if s.get("is_lead") else 1,
        ),
    )
    meta = ordered[0] if ordered else {}

    url = meta.get("url")
    infobox = None
    for src in ordered:
        if src.get("infobox"):
            infobox = str(src.get("infobox")).strip() or None
            break

    page_title = meta.get("page_title") or meta.get("title") or "Untitled"

    return {
        "title": page_title,
        "url": url if url else None,
        "category": _category_from_source(meta),
        "country": _country_from_source(meta),
        "primary_location": _primary_location_from_source(meta),
        "primary_location_explicit": _primary_location_explicit_from_source(meta),
        "case_status": _case_status_from_source(meta),
        "infobox": infobox,
    }


def format_page_response(doc_id: str, raw: dict[str, Any]) -> dict[str, Any]:
    hits = raw.get("hits") or {}
    hit_list = hits.get("hits") or []
    if not hit_list:
        raise ValueError("No sections found for this page")

    hit_list = sorted(hit_list, key=_section_sort_key)
    sources = [h.get("_source") or {} for h in hit_list]
    meta = _page_metadata_from_sources(sources)
    sections = [_hit_to_section(h) for h in hit_list]

    title = meta["title"]
    url = meta["url"]
    external: dict[str, str] = {}
    if url:
        external["wikipedia"] = url

    return {
        "doc_id": doc_id,
        "source": SOURCE_WIKIPEDIA,
        "title": title,
        "url": url,
        "category": meta["category"],
        "country": meta["country"],
        "primary_location": meta["primary_location"],
        "primary_location_explicit": meta["primary_location_explicit"],
        "case_status": meta["case_status"],
        "section_count": len(sections),
        "sections": sections,
        "infobox": meta["infobox"],
        "infobox_rows": _parse_infobox_string(meta["infobox"]),
        "external_links": external,
    }


def format_page_response_for_title(page_title: str, raw: dict[str, Any]) -> dict[str, Any]:
    return format_page_response(make_doc_id(page_title), raw)


def build_related_seed(page_title: str, raw: dict[str, Any]) -> str:
    """Build seed text for related-page search (matches index-time embedding input)."""
    hits = (raw.get("hits") or {}).get("hits") or []
    if not hits:
        return page_title.strip()

    hit_list = sorted(hits, key=_section_sort_key)
    sources = [h.get("_source") or {} for h in hit_list]

    content = ""
    for src in sources:
        if src.get("is_lead") or str(src.get("section_title") or "") == "Summary":
            content = str(src.get("content") or "").strip()
            break

    if not content:
        content = str(sources[0].get("content") or "").strip()

    seed_body = content[:RELATED_SEED_MAX_LEN]
    return f"{page_title} {seed_body}".strip()
