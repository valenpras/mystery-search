"""Environment configuration for Elasticsearch."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UI_DIR = ROOT / "ui"

DEFAULT_ELASTIC_ENDPOINT = (
    "https://15fda340ed3e43b9b14d6adaab5b74d8.asia-east1.gcp.elastic-cloud.com:443"
)

_ENV_LOADED = False


def _load_dotenv() -> None:
    """Load `.env` from project root; file values override (incl. empty OS vars)."""
    global _ENV_LOADED
    if _ENV_LOADED:
        return

    env_path = ROOT / ".env"
    if not env_path.is_file():
        _ENV_LOADED = True
        return

    for line in env_path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            os.environ[key] = value

    _ENV_LOADED = True


@dataclass(frozen=True)
class Settings:
    elastic_endpoint: str
    elastic_api_key: str
    index_name: str
    default_size: int
    snippet_max_len: int
    low_score_threshold: float
    ui_dir: Path

    @property
    def configured(self) -> bool:
        return bool(self.elastic_api_key)


def get_settings() -> Settings:
    _load_dotenv()
    return Settings(
        elastic_endpoint=os.environ.get("ELASTIC_ENDPOINT", DEFAULT_ELASTIC_ENDPOINT),
        elastic_api_key=os.environ.get("ELASTIC_API_KEY", "").strip(),
        index_name=os.environ.get("ELASTIC_INDEX", "pages"),
        default_size=20,
        snippet_max_len=320,
        low_score_threshold=float(
            os.environ.get("SEARCH_LOW_SCORE_THRESHOLD", "3.0")
        ),
        ui_dir=UI_DIR,
    )


_load_dotenv()
_settings = get_settings()
ELASTIC_ENDPOINT = _settings.elastic_endpoint
ELASTIC_API_KEY = _settings.elastic_api_key
INDEX_NAME = _settings.index_name
DEFAULT_SIZE = _settings.default_size
SNIPPET_MAX_LEN = _settings.snippet_max_len
LOW_SCORE_THRESHOLD = _settings.low_score_threshold
