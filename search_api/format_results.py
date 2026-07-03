"""Map Elasticsearch hits to Mystery Search API JSON."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote

from search_api.config import get_settings

SOURCE_WIKIPEDIA = "wikipedia"


def make_doc_id(page_title: str) -> str:
    return f"wikipedia:{quote(page_title, safe='')}"


def parse_doc_id(doc_id: str) -> str | None:
    if doc_id.startswith("wikipedia:"):
        from urllib.parse import unquote

        raw = doc_id[len("wikipedia:") :]
        while True:
            decoded = unquote(raw)
            if decoded == raw:
                break
            raw = decoded
        return raw
    return None


def _snippet_from_source(src: dict[str, Any]) -> str:
    text = src.get("content") or src.get("summary") or ""
    text = re.sub(r"\s+", " ", str(text)).strip()
    max_len = get_settings().snippet_max_len
    if len(text) <= max_len:
        return text
    return text[: max_len - 1].rstrip() + "…"


def _country_from_source(src: dict[str, Any]) -> str | None:
    for key in ("country", "location", "se_location", "full_location"):
        val = src.get(key)
        if val and str(val).strip() not in ("", "-", "null"):
            return str(val).strip()
    return None


def _case_status_from_source(src: dict[str, Any]) -> str | None:
    val = src.get("case_status")
    if val in ("solved", "unsolved", "unknown"):
        return val
    return None


def _category_from_source(src: dict[str, Any]) -> str | None:
    for key in ("SE_category", "category", "se_category", "SE_Category"):
        val = src.get(key)
        if val and str(val).strip() not in ("", "-"):
            return str(val).strip()
    return None


def hit_to_result(hit: dict[str, Any]) -> dict[str, Any]:
    src = hit.get("_source") or {}
    page_title = src.get("page_title") or src.get("title") or "Untitled"
    score = float(hit.get("_score") or 0.0)

    return {
        "doc_id": make_doc_id(page_title),
        "title": page_title,
        "snippet": _snippet_from_source(src),
        "score": round(score, 2),
        "source": SOURCE_WIKIPEDIA,
        "country": _country_from_source(src),
        "case_status": _case_status_from_source(src),
        "category": _category_from_source(src),
    }


def format_search_response(
    query: str,
    raw: dict[str, Any],
    *,
    source_label: str = SOURCE_WIKIPEDIA,
    from_: int = 0,
    size: int | None = None,
) -> dict[str, Any]:
    hits = raw.get("hits") or {}
    hit_list = hits.get("hits") or []
    took_ms = int(raw.get("took") or 0)

    results = [hit_to_result(h) for h in hit_list]
    top_score = results[0]["score"] if results else 0.0
    low_confidence = (
        from_ == 0 and bool(results) and top_score < get_settings().low_score_threshold
    )
    page_size = size if size is not None else len(results)
    has_more = len(results) == page_size and page_size > 0

    return {
        "query": query,
        "source": source_label,
        "results": results,
        "from": from_,
        "size": page_size,
        "total": len(results),
        "has_more": has_more,
        "took_ms": took_ms,
        "low_confidence": low_confidence,
    }
