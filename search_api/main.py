"""FastAPI app: /api/search + static Mystery Search UI."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from search_api import config
from search_api.es_search import ping, search_wikipedia_raw
from search_api.format_results import format_search_response

app = FastAPI(title="Mystery Search API", version="0.1.0")

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
) -> dict[str, Any] | None:
    filters: dict[str, Any] = {}
    if country:
        filters["location"] = country
    if category:
        filters["category"] = category
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
    country: str | None = Query(None),
    category: str | None = Query(None),
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
            filters=_build_filters(country, category),
            unique_pages=True,
        )
        body = raw.body if hasattr(raw, "body") else raw
        if not isinstance(body, dict):
            body = dict(body)
        return format_search_response(query, body)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Search failed: {exc}",
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


@app.get("/styles.css")
def styles() -> FileResponse:
    return FileResponse(_ui_file("styles.css"))


def run() -> None:
    import uvicorn

    uvicorn.run("search_api.main:app", host="127.0.0.1", port=8000, reload=True)


if __name__ == "__main__":
    run()
