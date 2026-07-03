"""Elasticsearch hybrid search (from Try_Elastic_Search_v2.ipynb)."""

from __future__ import annotations

import logging
import os
import time
from typing import Any

from elasticsearch import ApiError, Elasticsearch

from search_api.config import get_settings

logger = logging.getLogger(__name__)

DEFAULT_SEARCH_FIELDS = [
    "page_title^5",
    "section_title^3",
    "infobox^2",
    "content",
]

# API filter keys → ES field names (first match wins via bool.should)
FILTER_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "country": ("country", "location"),
    "category": ("SE_category", "se_category", "category"),
    "case_status": ("case_status",),
}

RETRYABLE_HTTP_STATUSES = frozenset({429, 502, 503, 504})
RETRYABLE_EXCEPTION_NAMES = frozenset(
    {"ConnectionError", "ConnectionTimeout", "TransportError"}
)

_embedding_model: Any | None = None
_client: Elasticsearch | None = None


def _create_client() -> Elasticsearch:
    settings = get_settings()
    if not settings.elastic_api_key:
        raise RuntimeError("ELASTIC_API_KEY is not set")
    return Elasticsearch(
        settings.elastic_endpoint,
        api_key=settings.elastic_api_key,
        request_timeout=settings.elastic_request_timeout,
        max_retries=0,
        retry_on_timeout=False,
    )


def reset_client() -> None:
    """Drop the cached client so the next request opens a fresh connection."""
    global _client
    if _client is not None:
        try:
            _client.close()
        except Exception:
            pass
    _client = None


def get_client() -> Elasticsearch:
    global _client
    if _client is None:
        _client = _create_client()
    return _client


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, ApiError):
        return exc.status_code in RETRYABLE_HTTP_STATUSES
    if isinstance(exc, (TimeoutError, ConnectionError, OSError)):
        return True
    return type(exc).__name__ in RETRYABLE_EXCEPTION_NAMES


def _search_with_retry(
    body: dict[str, Any],
    *,
    index: str | None = None,
    context: str = "search",
) -> dict[str, Any]:
    """Run es.search with retries on transient connection or server errors."""
    settings = get_settings()
    index_name = index or settings.index_name
    max_retries = settings.elastic_search_max_retries
    last_exc: BaseException | None = None

    for attempt in range(max_retries + 1):
        try:
            res = get_client().search(index=index_name, body=body)
            return res.body if hasattr(res, "body") else res
        except Exception as exc:
            last_exc = exc
            if attempt >= max_retries or not _is_retryable(exc):
                raise
            logger.info(
                "Elasticsearch %s retry attempt %s/%s after %s: %s",
                context,
                attempt + 1,
                max_retries,
                type(exc).__name__,
                exc,
            )
            reset_client()
            time.sleep(settings.elastic_search_retry_backoff_sec * (attempt + 1))

    assert last_exc is not None
    raise last_exc


def ping() -> bool:
    try:
        return bool(get_client().ping())
    except Exception:
        return False


def _get_embedding_model() -> Any:
    global _embedding_model
    if _embedding_model is None:
        os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
        from sentence_transformers import SentenceTransformer

        _embedding_model = SentenceTransformer(get_settings().embedding_model)
    return _embedding_model


def encode_query(query_text: str) -> list[float]:
    """Encode a search query with the same model used at index time."""
    return _get_embedding_model().encode(query_text, show_progress_bar=False).tolist()


def warmup_embedding_model() -> None:
    """Load model and run one encode so hybrid search is ready before first request."""
    settings = get_settings()
    logger.info("Loading embedding model %s...", settings.embedding_model)
    encode_query("warmup")
    logger.info("Embedding model ready")


def _term_clause(field: str, value: Any) -> dict[str, Any]:
    if isinstance(value, list):
        return {"terms": {field: value}}
    return {"term": {field: value}}


def expand_filters(filters: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Map API filter keys to ES clauses, supporting legacy and v2 field names."""
    if not filters:
        return []

    clauses: list[dict[str, Any]] = []
    for key, value in filters.items():
        fields = FILTER_FIELD_ALIASES.get(key, (key,))
        field_clauses = [_term_clause(field, value) for field in fields]
        if len(field_clauses) == 1:
            clauses.append(field_clauses[0])
        else:
            clauses.append(
                {
                    "bool": {
                        "should": field_clauses,
                        "minimum_should_match": 1,
                    }
                }
            )
    return clauses


def _apply_filters_to_body(body: dict[str, Any], filter_clauses: list[dict[str, Any]]) -> None:
    """Apply facet filters to RRF, BM25, and kNN retrievers."""
    if not filter_clauses:
        return

    rrf = body["retriever"]["rrf"]
    rrf["filter"].extend(filter_clauses)

    std_bool = rrf["retrievers"][0]["standard"]["query"]["bool"]
    std_bool.setdefault("filter", []).extend(filter_clauses)

    knn = rrf["retrievers"][1]["knn"]
    knn["filter"].extend(filter_clauses)


def _exclude_page_clause(page_title: str) -> dict[str, Any]:
    return {
        "bool": {
            "must_not": [
                {"term": {"page_title.keyword": page_title}},
            ]
        }
    }


def _apply_exclude_page(body: dict[str, Any], page_title: str) -> None:
    """Exclude a page from hybrid search results (for related-case queries)."""
    clause = _exclude_page_clause(page_title)
    rrf = body["retriever"]["rrf"]
    rrf["filter"].append(clause)

    std_bool = rrf["retrievers"][0]["standard"]["query"]["bool"]
    std_bool.setdefault("filter", []).append(clause)

    knn = rrf["retrievers"][1]["knn"]
    knn["filter"].append(clause)


def build_search_body(
    query_text: str,
    *,
    size: int | None = None,
    from_: int = 0,
    search_fields: list[str] | None = None,
    filters: dict[str, Any] | None = None,
    unique_pages: bool = True,
    query_vector: list[float] | None = None,
) -> dict[str, Any]:
    """Build hybrid BM25 + kNN query body (Elasticsearch RRF retriever API)."""
    if size is None:
        size = get_settings().default_size
    if not search_fields:
        search_fields = list(DEFAULT_SEARCH_FIELDS)
    if query_vector is None:
        query_vector = encode_query(query_text)

    knn_k = min(max(size * 2, from_ + size), 100)

    body: dict[str, Any] = {
        "from": from_,
        "size": size,
        "retriever": {
            "rrf": {
                "retrievers": [
                    {
                        "standard": {
                            "query": {
                                "bool": {
                                    "must": [
                                        {
                                            "multi_match": {
                                                "query": query_text,
                                                "fields": search_fields,
                                                "type": "best_fields",
                                                "minimum_should_match": "75%",
                                                "boost": 2,
                                            }
                                        }
                                    ],
                                    "should": [
                                        {
                                            "multi_match": {
                                                "query": query_text,
                                                "fields": search_fields,
                                                "type": "phrase",
                                                "slop": 4,
                                                "boost": 2.0,
                                            }
                                        }
                                    ],
                                }
                            }
                        }
                    },
                    {
                        "knn": {
                            "field": "content_vector",
                            "query_vector": query_vector,
                            "k": knn_k,
                            "num_candidates": 100,
                            "filter": [
                                {
                                    "multi_match": {
                                        "query": query_text,
                                        "fields": search_fields,
                                        "minimum_should_match": "1",
                                    }
                                }
                            ],
                        }
                    },
                ],
                "rank_window_size": 100,
                "rank_constant": 60,
                "filter": [],
            }
        },
    }

    filter_clauses = expand_filters(filters)
    _apply_filters_to_body(body, filter_clauses)

    if unique_pages:
        body["collapse"] = {"field": "page_title.keyword"}

    return body


def search_wikipedia_hybrid(
    query_text: str,
    *,
    size: int | None = None,
    from_: int = 0,
    search_fields: list[str] | None = None,
    filters: dict[str, Any] | None = None,
    unique_pages: bool = True,
    client: Elasticsearch | None = None,
) -> dict[str, Any]:
    settings = get_settings()
    body = build_search_body(
        query_text,
        size=size or settings.default_size,
        from_=from_,
        search_fields=search_fields,
        filters=filters,
        unique_pages=unique_pages,
    )
    if client is not None:
        res = client.search(index=settings.index_name, body=body)
        return res.body if hasattr(res, "body") else res
    return _search_with_retry(body, context="hybrid search")


def search_wikipedia_raw(
    query_text: str,
    *,
    size: int | None = None,
    from_: int = 0,
    search_fields: list[str] | None = None,
    filters: dict[str, Any] | None = None,
    unique_pages: bool = True,
    client: Elasticsearch | None = None,
) -> dict[str, Any]:
    """Run hybrid search; kept for callers expecting the raw ES response dict."""
    return search_wikipedia_hybrid(
        query_text,
        size=size,
        from_=from_,
        search_fields=search_fields,
        filters=filters,
        unique_pages=unique_pages,
        client=client,
    )


def search_related_pages_raw(
    page_title: str,
    seed_text: str,
    *,
    size: int = 6,
    client: Elasticsearch | None = None,
) -> dict[str, Any]:
    """Hybrid search for pages similar to seed text, excluding the source page."""
    query = (seed_text or page_title).strip() or page_title
    body = build_search_body(query, size=size, unique_pages=True)
    _apply_exclude_page(body, page_title)

    settings = get_settings()
    if client is not None:
        res = client.search(index=settings.index_name, body=body)
        return res.body if hasattr(res, "body") else res
    return _search_with_retry(body, context="related pages")


_PAGE_SECTIONS_SORT: list[dict[str, Any]] = [{"chunk_order": {"order": "asc"}}]


def _search_page_sections(
    search_fn,
    body: dict[str, Any],
) -> dict[str, Any]:
    """Run page-sections query; retry without sort if chunk_order is not mapped."""
    try:
        return search_fn(body)
    except ApiError as exc:
        if exc.status_code != 400 or "chunk_order" not in str(exc):
            raise
        body_no_sort = {k: v for k, v in body.items() if k != "sort"}
        return search_fn(body_no_sort)


def get_page_sections_raw(
    page_title: str,
    *,
    size: int = 100,
    client: Elasticsearch | None = None,
) -> dict[str, Any]:
    """Return all indexed sections for a Wikipedia page title."""
    settings = get_settings()
    index = settings.index_name

    def _search(body: dict[str, Any]) -> dict[str, Any]:
        if client is not None:
            res = client.search(index=index, body=body)
            return res.body if hasattr(res, "body") else res
        return _search_with_retry(body, index=index, context="page sections")

    body: dict[str, Any] = {
        "size": size,
        "query": {"term": {"page_title.keyword": page_title}},
        "sort": _PAGE_SECTIONS_SORT,
    }
    raw = _search_page_sections(_search, body)
    hit_list = (raw.get("hits") or {}).get("hits") or []
    if hit_list:
        return raw

    # Fallback when mapping lacks .keyword or uses legacy field names
    body = {
        "size": size,
        "query": {
            "bool": {
                "should": [
                    {"match_phrase": {"page_title": page_title}},
                    {"match_phrase": {"title": page_title}},
                ],
                "minimum_should_match": 1,
            }
        },
        "sort": _PAGE_SECTIONS_SORT,
    }
    raw = _search_page_sections(_search, body)
    hits = (raw.get("hits") or {}).get("hits") or []
    filtered = [
        h
        for h in hits
        if (h.get("_source") or {}).get("page_title") == page_title
        or (h.get("_source") or {}).get("title") == page_title
    ]
    if filtered:
        raw["hits"]["hits"] = filtered
    return raw
