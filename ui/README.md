# Mystery Search UI

Wikipedia search interface backed by Elasticsearch (via `search_api/`).

## Run

From repo root:

```powershell
.\scripts\run_search_server.ps1
```

Or:

```powershell
pip install -r requirements-search-api.txt
python -m uvicorn search_api.main:app --host 127.0.0.1 --port 8080 --reload
```

Open http://127.0.0.1:8080

## Elasticsearch

1. Copy `.env.example` to `.env`
2. Set `ELASTIC_API_KEY` and `ELASTIC_ENDPOINT` from Elastic Cloud
3. Restart the server after editing `.env`

Without `ELASTIC_API_KEY`, `/api/search` returns HTTP 503 with a clear error message.

## API

`GET /api/search?q=jack+ripper&country=United+States&category=murder`

`GET /api/health` — `configured: true` when the API key is set; `elasticsearch: true` when the cluster responds.
