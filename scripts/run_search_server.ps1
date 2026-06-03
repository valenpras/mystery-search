# Run Mystery Search UI + API (from repo root)
Set-Location $PSScriptRoot\..

if (-not (Test-Path ".\venv\Scripts\python.exe")) {
    Write-Error "Create venv first: python -m venv venv"
    exit 1
}

.\venv\Scripts\pip.exe install -q -r requirements-search-api.txt

# Copy .env.example to .env and set ELASTIC_API_KEY for live Elasticsearch.
.\venv\Scripts\python.exe -m uvicorn search_api.main:app --host 127.0.0.1 --port 8000 --reload
