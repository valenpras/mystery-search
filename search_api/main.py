"""FastAPI app: /api/search + static Mystery Search UI."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from search_api import config
from search_api.es_search import (
    get_page_sections_raw,
    ping,
    search_related_pages_raw,
    search_wikipedia_raw,
    warmup_embedding_model,
)
from search_api.format_page import build_related_seed, format_page_response
from search_api.format_results import format_search_response, parse_doc_id

logger = logging.getLogger(__name__)
logging.getLogger("search_api").setLevel(logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = config.get_settings()
    if settings.configured:
        try:
            warmup_embedding_model()
        except Exception as exc:
            logger.warning("Embedding model warmup failed: %s", exc)
    yield


app = FastAPI(title="Mystery Search API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)

UI_DIR = config.UI_DIR

NOT_CONFIGURED_MSG = (
    "Elasticsearch is not configured. Set ELASTIC_API_KEY (and ELASTIC_ENDPOINT) in .env."
)


def _require_elasticsearch(settings: config.Settings) -> None:
    if not settings.configured:
        raise HTTPException(status_code=503, detail=NOT_CONFIGURED_MSG)


def _build_filters(
    country: str | None,
    category: str | None,
    case_status: str | None,
) -> dict[str, Any] | None:
    """Build API facet filters (country, category, case_status)."""
    filters: dict[str, Any] = {}
    if country:
        filters["country"] = country
    if category:
        filters["category"] = category
    if case_status:
        filters["case_status"] = case_status
    return filters or None


@app.get("/api/health")
def health() -> dict[str, Any]:
    settings = config.get_settings()
    configured = settings.configured
    return {
        "ok": configured,
        "configured": configured,
        "elasticsearch": ping() if configured else False,
        "index": settings.index_name,
    }


@app.get("/api/search")
def api_search(
    q: str = Query(..., min_length=1, description="Search query"),
    size: int = Query(20, ge=1, le=50),
    from_: int = Query(0, ge=0, alias="from"),
    country: str | None = Query(None),
    category: str | None = Query(None),
    case_status: str | None = Query(None, pattern="^(unsolved|solved)$"),
) -> dict[str, Any]:
    query = q.strip()
    if not query:
        raise HTTPException(status_code=400, detail="Query cannot be empty")

    settings = config.get_settings()
    _require_elasticsearch(settings)

    try:
        raw = search_wikipedia_raw(
            query,
            size=size,
            from_=from_,
            filters=_build_filters(country, category, case_status),
            unique_pages=True,
        )
        body = raw.body if hasattr(raw, "body") else raw
        if not isinstance(body, dict):
            body = dict(body)
        return format_search_response(query, body, from_=from_, size=size)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Search failed: {exc}",
        ) from exc


@app.get("/api/related")
def api_related(
    doc_id: str = Query(..., min_length=1, description="Document id, e.g. wikipedia:Page_Title"),
    size: int = Query(6, ge=1, le=12),
) -> dict[str, Any]:
    page_title = parse_doc_id(doc_id.strip())
    if not page_title:
        raise HTTPException(
            status_code=400,
            detail="Unsupported doc_id. Expected wikipedia:<page_title>.",
        )

    settings = config.get_settings()
    _require_elasticsearch(settings)

    try:
        sections_raw = get_page_sections_raw(page_title)
        if not isinstance(sections_raw, dict):
            sections_raw = dict(sections_raw)

        hit_list = (sections_raw.get("hits") or {}).get("hits") or []
        if not hit_list:
            raise HTTPException(status_code=404, detail="No sections found for this page")

        seed = build_related_seed(page_title, sections_raw)
        raw = search_related_pages_raw(page_title, seed, size=size)
        if not isinstance(raw, dict):
            raw = dict(raw)

        formatted = format_search_response(page_title, raw, size=size)
        related = [
            r for r in formatted["results"] if r.get("title") != page_title
        ]

        return {
            "doc_id": doc_id.strip(),
            "source_page": page_title,
            "related": related,
            "took_ms": formatted.get("took_ms", 0),
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Related search failed: {exc}",
        ) from exc


@app.get("/api/page")
def api_page(
    doc_id: str = Query(..., min_length=1, description="Document id, e.g. wikipedia:Page_Title"),
) -> dict[str, Any]:
    page_title = parse_doc_id(doc_id.strip())
    if not page_title:
        raise HTTPException(
            status_code=400,
            detail="Unsupported doc_id. Expected wikipedia:<page_title>.",
        )

    settings = config.get_settings()
    _require_elasticsearch(settings)

    try:
        raw = get_page_sections_raw(page_title)
        if not isinstance(raw, dict):
            raw = dict(raw)
        return format_page_response(doc_id.strip(), raw)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Page load failed: {exc}",
        ) from exc


def _ui_file(name: str) -> Path:
    path = UI_DIR / name
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"Missing UI file: {name}")
    return path


@app.get("/")
def home() -> FileResponse:
    return FileResponse(_ui_file("index.html"))


@app.get("/search.html")
def search_page() -> FileResponse:
    return FileResponse(_ui_file("search.html"))


@app.get("/detail.html")
def detail_page() -> FileResponse:
    return FileResponse(_ui_file("detail.html"))


if UI_DIR.is_dir():
    app.mount("/js", StaticFiles(directory=UI_DIR / "js"), name="js")
    assets_dir = UI_DIR / "assets"
    if assets_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")


@app.get("/favicon.ico")
def favicon() -> FileResponse:
    path = UI_DIR / "assets" / "favicon.ico"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Missing favicon.ico")
    return FileResponse(path, media_type="image/x-icon")


@app.get("/styles.css")
def styles() -> FileResponse:
    return FileResponse(_ui_file("styles.css"))


def run() -> None:
    import uvicorn

    uvicorn.run("search_api.main:app", host="127.0.0.1", port=8000, reload=True)


if __name__ == "__main__":
    run()
