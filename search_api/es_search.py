"""Elasticsearch search (from Try_Elastic_Search.ipynb)."""

from __future__ import annotations

from typing import Any

from elasticsearch import Elasticsearch

from search_api.config import get_settings

DEFAULT_SEARCH_FIELDS = [
    "page_title^5",
    "section_title^3",
    "infobox^2",
    "content",
]


def get_client() -> Elasticsearch:
    settings = get_settings()
    if not settings.elastic_api_key:
        raise RuntimeError("ELASTIC_API_KEY is not set")
    return Elasticsearch(
        settings.elastic_endpoint,
        api_key=settings.elastic_api_key,
    )


def ping() -> bool:
    try:
        return bool(get_client().ping())
    except Exception:
        return False


def build_search_body(
    query_text: str,
    *,
    size: int | None = None,
    search_fields: list[str] | None = None,
    filters: dict[str, Any] | None = None,
    unique_pages: bool = True,
) -> dict[str, Any]:
    if size is None:
        size = get_settings().default_size
    if not search_fields:
        search_fields = list(DEFAULT_SEARCH_FIELDS)

    body: dict[str, Any] = {
        "size": size,
        "query": {
            "bool": {
                "must": {
                    "multi_match": {
                        "query": query_text,
                        "fields": search_fields,
                        "type": "best_fields",
                    }
                },
                "filter": [],
            }
        },
    }

    if filters:
        for field, value in filters.items():
            if isinstance(value, list):
                body["query"]["bool"]["filter"].append({"terms": {field: value}})
            else:
                body["query"]["bool"]["filter"].append({"term": {field: value}})

    if unique_pages:
        body["collapse"] = {"field": "page_title.keyword"}

    return body


def search_wikipedia_raw(
    query_text: str,
    *,
    size: int | None = None,
    search_fields: list[str] | None = None,
    filters: dict[str, Any] | None = None,
    unique_pages: bool = True,
    client: Elasticsearch | None = None,
) -> dict[str, Any]:
    settings = get_settings()
    es = client or get_client()
    body = build_search_body(
        query_text,
        size=size or settings.default_size,
        search_fields=search_fields,
        filters=filters,
        unique_pages=unique_pages,
    )
    return es.search(index=settings.index_name, body=body)
