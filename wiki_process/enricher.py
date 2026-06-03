"""Enrich Wikipedia page JSON with location/status fields via Ollama."""

from __future__ import annotations

import json
import os
import re
import time
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

from wiki_process.text_utils import format_infobox, llm_body_text

DEFAULT_OLLAMA_HOST = "http://127.0.0.1:11434"
DEFAULT_OLLAMA_MODEL = "qwen3.5-32k"
DEFAULT_BATCH_SIZE = 1
DEFAULT_TIMEOUT = 300.0
DEFAULT_NUM_CTX = 32768
DEFAULT_NUM_PREDICT = 1024
DEFAULT_MAX_RETRIES = 3

ENRICHMENT_FIELDS = (
    "primary_location",
    "primary_location_explicit",
    "country",
    "case_status",
)

SYSTEM_PROMPT = """You extract structured metadata from English Wikipedia articles about mysteries, crimes, disappearances, and unexplained events.

Input: JSON array of pages. Each page has url, title, summary, optional infobox, and body_text.
Use ONLY title, summary, infobox, and body_text. Do NOT copy location fields from infobox blindly — infer the primary event location from narrative context when needed.

For EACH page output one object with:
- url (same as input)
- primary_location: string or null — main location where the mystery/crime/event occurred (city/region/country as appropriate)
- primary_location_explicit: true if clearly stated in text or infobox, false if inferred, null if primary_location is null
- country: string or null — country for primary_location; null if no location
- case_status: exactly one of "solved", "unsolved", "unknown"
  - "solved": perpetrator convicted, case officially closed, body found and case resolved, etc.
  - "unsolved": still open, missing person with no resolution, unidentified perpetrator, etc.
  - "unknown": insufficient information, disputed, or not a crime/mystery case

Return ONLY a JSON array of objects, same order and length as input. No markdown.

Example — input:
[
  {
    "url": "https://en.wikipedia.org/wiki/Murder_of_Jennifer_Dulos",
    "title": "Murder of Jennifer Dulos",
    "summary": "Jennifer Dulos disappeared on May 24, 2019 in New Canaan, Connecticut...",
    "infobox": {"Born": "September 27, 1968", "Disappeared": "May 24, 2019"},
    "body_text": "Jennifer Dulos was last seen in New Canaan, Connecticut. Her estranged husband was later charged..."
  }
]

Example — output:
[
  {
    "url": "https://en.wikipedia.org/wiki/Murder_of_Jennifer_Dulos",
    "primary_location": "New Canaan, Connecticut, United States",
    "primary_location_explicit": true,
    "country": "United States",
    "case_status": "unsolved"
  }
]"""


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def append_jsonl(path: Path, obj: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")


def load_done_titles(progress_path: Path) -> set[str]:
    if not progress_path.is_file():
        return set()
    with progress_path.open(encoding="utf-8") as f:
        data = json.load(f)
    return set(data.get("done_titles", []))


def save_done_titles(progress_path: Path, done: set[str]) -> None:
    progress_path.parent.mkdir(parents=True, exist_ok=True)
    with progress_path.open("w", encoding="utf-8") as f:
        json.dump({"done_titles": sorted(done)}, f, ensure_ascii=False)


def load_pages(path: Path) -> dict[str, dict[str, Any]]:
    with path.open(encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"expected dict keyed by page title, got {type(data).__name__}")
    return data


def llm_input_record(page_title: str, page: dict[str, Any]) -> dict[str, Any]:
    record: dict[str, Any] = {
        "url": page.get("url", ""),
        "title": page_title,
        "summary": page.get("summary", ""),
        "body_text": llm_body_text(page, page_title=page_title),
    }
    infobox = page.get("infobox") or {}
    if infobox:
        record["infobox"] = infobox
    return record


def parse_llm_json(content: str) -> list[dict[str, Any]]:
    text = content.strip()
    if not text:
        raise ValueError("empty response from Ollama")
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text).strip()

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("[")
        end = text.rfind("]")
        if start < 0 or end <= start:
            raise
        parsed = json.loads(text[start : end + 1])

    if isinstance(parsed, dict):
        return [parsed]
    if not isinstance(parsed, list):
        raise ValueError(f"expected JSON array, got {type(parsed).__name__}")
    return parsed


def load_failed_titles(failed_path: Path) -> list[str]:
    if not failed_path.is_file():
        return []
    titles: list[str] = []
    seen: set[str] = set()
    with failed_path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            title = json.loads(line).get("page_title", "")
            if title and title not in seen:
                seen.add(title)
                titles.append(title)
    return titles


def call_ollama(
    host: str,
    model: str,
    pages: list[dict[str, Any]],
    timeout: float,
    *,
    think: bool = False,
    num_ctx: int = DEFAULT_NUM_CTX,
    num_predict: int = DEFAULT_NUM_PREDICT,
) -> list[dict[str, Any]]:
    url = f"{host.rstrip('/')}/api/chat"
    user_content = json.dumps(pages, ensure_ascii=False)
    payload: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        "stream": False,
        "format": "json",
        "options": {
            "num_ctx": num_ctx,
            "num_predict": num_predict,
        },
    }
    if not think:
        payload["think"] = False

    t0 = time.time()
    r = requests.post(url, json=payload, timeout=timeout)
    elapsed = time.time() - t0
    r.raise_for_status()
    body = r.json()
    content = body.get("message", {}).get("content", "")
    if not content.strip():
        raise ValueError("empty response from Ollama")
    print(
        f"  ollama: {elapsed:.1f}s, num_ctx={num_ctx}, "
        f"prompt_chars={len(SYSTEM_PROMPT) + len(user_content)}"
    )
    return parse_llm_json(content)


def merge_enrichment(page: dict[str, Any], enrichment: dict[str, Any]) -> dict[str, Any]:
    out = deepcopy(page)
    for field in ENRICHMENT_FIELDS:
        out[field] = enrichment.get(field)
    return out


def load_sidecar_map(sidecar_path: Path) -> dict[str, dict[str, Any]]:
    """Load sidecar JSONL; last entry wins per page_title."""
    pages: dict[str, dict[str, Any]] = {}
    if not sidecar_path.is_file():
        return pages
    with sidecar_path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            title = row["page_title"]
            pages[title] = row["page"]
    return pages


def merge_sidecar_into_pages(
    pages: dict[str, dict[str, Any]],
    sidecar_path: Path,
) -> dict[str, dict[str, Any]]:
    """Overlay LLM fields from sidecar onto base pages (rebase-friendly)."""
    if not sidecar_path.is_file():
        return pages
    merged = deepcopy(pages)
    for title, sidecar_page in load_sidecar_map(sidecar_path).items():
        if title in merged:
            merged[title] = merge_enrichment(merged[title], sidecar_page)
        else:
            merged[title] = sidecar_page
    return merged


def write_sidecar(path: Path, entries: list[tuple[str, dict[str, Any]]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for title, page in entries:
            f.write(json.dumps({"page_title": title, "page": page}, ensure_ascii=False) + "\n")


def write_merged_json(pages: dict[str, dict[str, Any]], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as f:
        json.dump(pages, f, ensure_ascii=False, indent=2)


def append_failure(
    path: Path,
    *,
    page_title: str,
    url: str,
    reason: str,
) -> None:
    append_jsonl(
        path,
        {
            "page_title": page_title,
            "url": url,
            "reason": reason,
            "ts": _utc_now_iso(),
        },
    )


@dataclass
class WikiEnrichConfig:
    input_path: Path
    output_path: Path
    sidecar_path: Path
    failed_path: Path
    progress_path: Path
    ollama_host: str = DEFAULT_OLLAMA_HOST
    ollama_model: str = DEFAULT_OLLAMA_MODEL
    batch_size: int = DEFAULT_BATCH_SIZE
    timeout: float = DEFAULT_TIMEOUT
    think: bool = False
    num_ctx: int = DEFAULT_NUM_CTX
    num_predict: int = DEFAULT_NUM_PREDICT
    max_retries: int = DEFAULT_MAX_RETRIES
    limit: int | None = None
    merge_only: bool = False
    rebase: bool = False
    retry_failed: bool = False
    titles: tuple[str, ...] = ()


def run_rebase(cfg: WikiEnrichConfig) -> None:
    """Re-attach LLM fields from sidecar onto current input pages (e.g. 4e -> 4g)."""
    pages = load_pages(cfg.input_path)
    sidecar_map = load_sidecar_map(cfg.sidecar_path)
    done_titles = load_done_titles(cfg.progress_path)
    if not done_titles:
        done_titles = set(sidecar_map)

    rebased: list[tuple[str, dict[str, Any]]] = []
    missing_in_input: list[str] = []
    missing_in_sidecar: list[str] = []

    for title in sorted(done_titles):
        if title not in sidecar_map:
            missing_in_sidecar.append(title)
            continue
        if title not in pages:
            missing_in_input.append(title)
            continue
        enrichment = {field: sidecar_map[title].get(field) for field in ENRICHMENT_FIELDS}
        rebased.append((title, merge_enrichment(pages[title], enrichment)))

    backup_path = cfg.sidecar_path.with_suffix(".jsonl.bak")
    if cfg.sidecar_path.is_file():
        backup_path.write_bytes(cfg.sidecar_path.read_bytes())

    write_sidecar(cfg.sidecar_path, rebased)
    merged = merge_sidecar_into_pages(pages, cfg.sidecar_path)
    write_merged_json(merged, cfg.output_path)

    print(
        f"Rebased {len(rebased)} page(s) onto {cfg.input_path.name}; "
        f"sidecar backup: {backup_path.name if backup_path.is_file() else 'none'}"
    )
    if missing_in_sidecar:
        print(f"  warning: {len(missing_in_sidecar)} done title(s) missing from sidecar")
    if missing_in_input:
        print(f"  warning: {len(missing_in_input)} title(s) not in input JSON")


def enrich_batch_with_retries(
    cfg: WikiEnrichConfig,
    llm_pages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    last_error: Exception | None = None
    attempts = max(1, cfg.max_retries)
    for attempt in range(1, attempts + 1):
        try:
            return call_ollama(
                cfg.ollama_host,
                cfg.ollama_model,
                llm_pages,
                cfg.timeout,
                think=cfg.think,
                num_ctx=cfg.num_ctx,
                num_predict=cfg.num_predict,
            )
        except Exception as e:
            last_error = e
            if attempt < attempts:
                print(f"  retry {attempt}/{attempts - 1} after: {e}")
                time.sleep(1.0)
    assert last_error is not None
    raise last_error


def run_enrich(cfg: WikiEnrichConfig) -> None:
    pages = load_pages(cfg.input_path)

    if cfg.rebase:
        run_rebase(cfg)
        return

    if cfg.merge_only:
        merged = merge_sidecar_into_pages(pages, cfg.sidecar_path)
        write_merged_json(merged, cfg.output_path)
        print(f"Merged {len(merged)} page(s) to {cfg.output_path}")
        return

    done_titles = load_done_titles(cfg.progress_path)

    if cfg.titles:
        pending = [
            (title, pages[title])
            for title in cfg.titles
            if title in pages and title not in done_titles
        ]
    elif cfg.retry_failed:
        retry_titles = [
            title for title in load_failed_titles(cfg.failed_path) if title not in done_titles
        ]
        pending = [(title, pages[title]) for title in retry_titles if title in pages]
        if cfg.limit is not None:
            pending = pending[: cfg.limit]
    else:
        pending = [
            (title, page) for title, page in pages.items() if title not in done_titles
        ]
        if cfg.limit is not None:
            pending = pending[: cfg.limit]

    total = len(pending)
    if total == 0:
        merged = merge_sidecar_into_pages(pages, cfg.sidecar_path)
        write_merged_json(merged, cfg.output_path)
        print("Nothing to enrich (all pages done). Wrote merged JSON.")
        return

    print(
        f"Enriching {total} page(s), batch_size={cfg.batch_size}, "
        f"model={cfg.ollama_model}, num_ctx={cfg.num_ctx}"
        + (" (retry-failed mode)" if cfg.retry_failed else "")
    )

    written = 0
    for batch_start in range(0, total, cfg.batch_size):
        batch = pending[batch_start : batch_start + cfg.batch_size]
        llm_pages = [llm_input_record(title, page) for title, page in batch]

        try:
            enrichments = enrich_batch_with_retries(cfg, llm_pages)
            if len(enrichments) != len(batch):
                raise ValueError(
                    f"expected {len(batch)} enrichment(s), got {len(enrichments)}"
                )

            for (title, source), enrichment in zip(batch, enrichments):
                if enrichment.get("url") != source.get("url"):
                    raise ValueError(
                        f"url mismatch for {title!r}: "
                        f"{enrichment.get('url')!r} != {source.get('url')!r}"
                    )
                enriched_page = merge_enrichment(source, enrichment)
                append_jsonl(
                    cfg.sidecar_path,
                    {"page_title": title, "page": enriched_page},
                )
                done_titles.add(title)
                written += 1

            save_done_titles(cfg.progress_path, done_titles)
            print(f"  progress: {written}/{total} (last: {batch[-1][0]!r})")

        except Exception as e:
            reason = repr(e)
            for title, source in batch:
                append_failure(
                    cfg.failed_path,
                    page_title=title,
                    url=source.get("url", ""),
                    reason=reason,
                )
            print(f"  batch failed ({[t for t, _ in batch]}): {e}")

    merged = merge_sidecar_into_pages(pages, cfg.sidecar_path)
    write_merged_json(merged, cfg.output_path)
    print(f"Done. Wrote {written} new page(s); merged JSON at {cfg.output_path}")


def main(argv: list[str] | None = None) -> None:
    import argparse

    default_input = Path("cleaning_pages_db_4g.json")
    default_output = Path("data") / "wiki" / "cleaning_pages_db_enriched.json"

    p = argparse.ArgumentParser(
        description="Enrich Wikipedia page JSON with location/status fields via Ollama.",
        epilog="Environment: WIKI_ENRICH_OLLAMA_HOST, WIKI_ENRICH_OLLAMA_MODEL.",
    )
    p.add_argument(
        "--input",
        type=Path,
        default=default_input,
        help="Input pages JSON (dict keyed by title)",
    )
    p.add_argument(
        "--output",
        type=Path,
        default=default_output,
        help="Output enriched pages JSON (friend's dict shape + 4 fields)",
    )
    p.add_argument(
        "--sidecar",
        type=Path,
        default=None,
        help="Resume sidecar JSONL (default: <output>.jsonl)",
    )
    p.add_argument(
        "--failed",
        type=Path,
        default=None,
        help="Failed enrichments JSONL (default: <output>.failed.jsonl)",
    )
    p.add_argument(
        "--progress",
        type=Path,
        default=None,
        help="Resume progress file (default: <output>.progress.json)",
    )
    p.add_argument(
        "--ollama-host",
        default=os.environ.get("WIKI_ENRICH_OLLAMA_HOST", DEFAULT_OLLAMA_HOST),
        help=f"Ollama base URL (default: {DEFAULT_OLLAMA_HOST})",
    )
    p.add_argument(
        "--model",
        default=os.environ.get("WIKI_ENRICH_OLLAMA_MODEL", DEFAULT_OLLAMA_MODEL),
        help=f"Ollama model name (default: {DEFAULT_OLLAMA_MODEL})",
    )
    p.add_argument(
        "--batch-size",
        type=int,
        default=DEFAULT_BATCH_SIZE,
        help=f"Pages per Ollama request (default: {DEFAULT_BATCH_SIZE})",
    )
    p.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT,
        help=f"Ollama request timeout in seconds (default: {DEFAULT_TIMEOUT})",
    )
    p.add_argument(
        "--think",
        action="store_true",
        help="Enable Qwen thinking mode (slower; default is think=false)",
    )
    p.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Process at most this many pages (for testing)",
    )
    p.add_argument(
        "--num-ctx",
        type=int,
        default=int(os.environ.get("WIKI_ENRICH_NUM_CTX", DEFAULT_NUM_CTX)),
        help=f"Ollama context window in tokens (default: {DEFAULT_NUM_CTX})",
    )
    p.add_argument(
        "--num-predict",
        type=int,
        default=int(os.environ.get("WIKI_ENRICH_NUM_PREDICT", DEFAULT_NUM_PREDICT)),
        help=f"Max output tokens per response (default: {DEFAULT_NUM_PREDICT})",
    )
    p.add_argument(
        "--max-retries",
        type=int,
        default=DEFAULT_MAX_RETRIES,
        help=f"Retries per batch on Ollama/JSON errors (default: {DEFAULT_MAX_RETRIES})",
    )
    p.add_argument(
        "--retry-failed",
        action="store_true",
        help="Re-enrich page titles listed in the failed JSONL (skips done titles)",
    )
    p.add_argument(
        "--titles",
        nargs="+",
        default=None,
        help="Only enrich these page titles (for targeted retries/tests)",
    )
    p.add_argument(
        "--rebase",
        action="store_true",
        help="Re-apply sidecar LLM fields onto --input pages (e.g. switch 4e enrichments to 4g)",
    )
    p.add_argument(
        "--merge-only",
        action="store_true",
        help="Merge existing sidecar JSONL into output JSON without calling Ollama",
    )
    args = p.parse_args(argv)

    output_path = args.output.resolve()
    sidecar_path = (args.sidecar or output_path.with_suffix(".jsonl")).resolve()
    failed_path = (args.failed or output_path.with_suffix(".failed.jsonl")).resolve()
    progress_path = (args.progress or output_path.with_suffix(".progress.json")).resolve()

    run_enrich(
        WikiEnrichConfig(
            input_path=args.input.resolve(),
            output_path=output_path,
            sidecar_path=sidecar_path,
            failed_path=failed_path,
            progress_path=progress_path,
            ollama_host=args.ollama_host,
            ollama_model=args.model,
            batch_size=max(1, args.batch_size),
            timeout=args.timeout,
            think=args.think,
            num_ctx=max(1, args.num_ctx),
            num_predict=max(1, args.num_predict),
            max_retries=max(1, args.max_retries),
            limit=args.limit,
            merge_only=args.merge_only,
            rebase=args.rebase,
            retry_failed=args.retry_failed,
            titles=tuple(args.titles or ()),
        )
    )


if __name__ == "__main__":
    main()
