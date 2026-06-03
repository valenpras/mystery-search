"""Enrich cases.jsonl with location and status fields via Ollama (Qwen)."""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

DEFAULT_OLLAMA_HOST = "http://127.0.0.1:11434"
DEFAULT_OLLAMA_MODEL = "qwen3.5:latest"
DEFAULT_BATCH_SIZE = 1
DEFAULT_TIMEOUT = 300.0

KEEP_FIELDS = (
    "article_url",
    "archive_tag",
    "gallery_category",
    "title",
    "body_text",
    "content_images",
)

ENRICHMENT_FIELDS = (
    "primary_location",
    "primary_location_explicit",
    "country",
    "case_status",
)

SYSTEM_PROMPT = """You extract structured metadata from Unsolved Mysteries case articles.

Input: JSON array of cases. Each case has article_url, title, body_text.
Use ONLY title and body_text. Ignore promotional links (Amazon, YouTube, etc.).

For EACH case output one object with:
- article_url (same as input)
- primary_location: string or null — main location of the mystery/event
- primary_location_explicit: true if stated in text, false if inferred, null if primary_location is null
- country: string or null — infer from primary_location (e.g. "United States"); null if no location
- case_status: exactly one of "solved", "unsolved", "unknown"

Return ONLY a JSON array of objects, same order and length as input. No markdown.

Example 1 — input:
[
  {
    "article_url": "https://unsolved.com/gallery/bill-beatyaes-haunted-mansion/",
    "title": "Bill Beaty's Haunted Mansion",
    "body_text": "In 1923, a wealthy executive named Bill Beaty, and his wife, started building a seventeenth century style Norman castle on 150 acres of woodland in Basking Ridge, New Jersey. In 1930, before construction was complete, the Beatys moved into the castle with their four children. But Bill would never see the completion of his dream. Just one year later, he died of the flu at the age of 45."
  }
]

Example 1 — output:
[
  {
    "article_url": "https://unsolved.com/gallery/bill-beatyaes-haunted-mansion/",
    "primary_location": "Basking Ridge, New Jersey, United States",
    "primary_location_explicit": true,
    "country": "United States",
    "case_status": "unknown"
  }
]

Example 2 — input:
[
  {
    "article_url": "https://unsolved.com/gallery/example-case/",
    "title": "Example Case",
    "body_text": "In the early 1990s, hikers in a remote part of Colorado discovered unusual tracks in the snow. The case remains open and no suspect has ever been identified."
  }
]

Example 2 — output:
[
  {
    "article_url": "https://unsolved.com/gallery/example-case/",
    "primary_location": "Colorado, United States",
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


def load_done_lines(progress_path: Path) -> set[int]:
    if not progress_path.is_file():
        return set()
    with progress_path.open(encoding="utf-8") as f:
        data = json.load(f)
    return set(data.get("done_lines", []))


def save_done_lines(progress_path: Path, done: set[int]) -> None:
    progress_path.parent.mkdir(parents=True, exist_ok=True)
    with progress_path.open("w", encoding="utf-8") as f:
        json.dump({"done_lines": sorted(done)}, f)


def slim_source_record(row: dict[str, Any]) -> dict[str, Any]:
    return {k: row[k] for k in KEEP_FIELDS if k in row}


def llm_input_record(row: dict[str, Any]) -> dict[str, str]:
    return {
        "article_url": row["article_url"],
        "title": row.get("title", ""),
        "body_text": row.get("body_text", ""),
    }


def parse_llm_json(content: str) -> list[dict[str, Any]]:
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    parsed = json.loads(text)
    if isinstance(parsed, dict):
        return [parsed]
    if not isinstance(parsed, list):
        raise ValueError(f"expected JSON array, got {type(parsed).__name__}")
    return parsed


def call_ollama(
    host: str,
    model: str,
    cases: list[dict[str, str]],
    timeout: float,
    *,
    think: bool = False,
) -> list[dict[str, Any]]:
    url = f"{host.rstrip('/')}/api/chat"
    user_content = json.dumps(cases, ensure_ascii=False)
    payload: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        "stream": False,
        "format": "json",
    }
    if not think:
        payload["think"] = False

    t0 = time.time()
    r = requests.post(url, json=payload, timeout=timeout)
    elapsed = time.time() - t0
    r.raise_for_status()
    body = r.json()
    content = body.get("message", {}).get("content", "")
    if not content:
        raise ValueError("empty response from Ollama")
    print(
        f"  ollama: {elapsed:.1f}s, "
        f"prompt_chars={len(SYSTEM_PROMPT) + len(user_content)}"
    )
    return parse_llm_json(content)


def merge_enrichment(
    source: dict[str, Any],
    enrichment: dict[str, Any],
) -> dict[str, Any]:
    out = slim_source_record(source)
    for field in ENRICHMENT_FIELDS:
        out[field] = enrichment.get(field)
    return out


def append_failure(
    path: Path,
    *,
    source_line: int,
    article_url: str,
    reason: str,
) -> None:
    append_jsonl(
        path,
        {
            "source_line": source_line,
            "article_url": article_url,
            "reason": reason,
            "ts": _utc_now_iso(),
        },
    )


@dataclass
class EnrichConfig:
    input_path: Path
    output_path: Path
    failed_path: Path
    progress_path: Path
    ollama_host: str = DEFAULT_OLLAMA_HOST
    ollama_model: str = DEFAULT_OLLAMA_MODEL
    batch_size: int = DEFAULT_BATCH_SIZE
    timeout: float = DEFAULT_TIMEOUT
    think: bool = False
    limit: int | None = None


def run_enrich(cfg: EnrichConfig) -> None:
    done_lines = load_done_lines(cfg.progress_path)
    written = 0

    with cfg.input_path.open(encoding="utf-8") as f:
        rows: list[tuple[int, dict[str, Any]]] = []
        for line_num, line in enumerate(f):
            line = line.strip()
            if not line:
                continue
            if line_num in done_lines:
                continue
            rows.append((line_num, json.loads(line)))

    pending = rows
    if cfg.limit is not None:
        pending = pending[: cfg.limit]

    total = len(pending)
    if total == 0:
        print("Nothing to enrich (all lines done or input empty).")
        return

    print(f"Enriching {total} row(s), batch_size={cfg.batch_size}, model={cfg.ollama_model}")

    for batch_start in range(0, total, cfg.batch_size):
        batch = pending[batch_start : batch_start + cfg.batch_size]
        line_nums = [ln for ln, _ in batch]
        sources = [row for _, row in batch]
        llm_cases = [llm_input_record(row) for row in sources]

        try:
            enrichments = call_ollama(
                cfg.ollama_host,
                cfg.ollama_model,
                llm_cases,
                cfg.timeout,
                think=cfg.think,
            )
            if len(enrichments) != len(sources):
                raise ValueError(
                    f"expected {len(sources)} enrichment(s), got {len(enrichments)}"
                )

            for (line_num, source), enrichment in zip(batch, enrichments):
                if enrichment.get("article_url") != source.get("article_url"):
                    raise ValueError(
                        f"article_url mismatch at line {line_num}: "
                        f"{enrichment.get('article_url')!r} != {source.get('article_url')!r}"
                    )
                record = merge_enrichment(source, enrichment)
                append_jsonl(cfg.output_path, record)
                done_lines.add(line_num)
                written += 1

            save_done_lines(cfg.progress_path, done_lines)
            print(f"  progress: {written}/{total} (through source line {line_nums[-1]})")

        except Exception as e:
            reason = repr(e)
            for line_num, source in batch:
                append_failure(
                    cfg.failed_path,
                    source_line=line_num,
                    article_url=source.get("article_url", ""),
                    reason=reason,
                )
            print(f"  batch failed (lines {line_nums}): {e}")

    print(f"Done. Wrote {written} enriched record(s) to {cfg.output_path}")


def main(argv: list[str] | None = None) -> None:
    import argparse

    default_input = Path("data") / "unsolved" / "cases.jsonl"
    default_output = Path("data") / "unsolved" / "cases_enriched.jsonl"

    p = argparse.ArgumentParser(
        description="Enrich cases.jsonl with location/status fields via Ollama (Qwen).",
        epilog="Environment: UNSOLVED_ENRICH_OLLAMA_HOST, UNSOLVED_ENRICH_OLLAMA_MODEL.",
    )
    p.add_argument(
        "--input",
        type=Path,
        default=default_input,
        help="Input cases.jsonl from crawler",
    )
    p.add_argument(
        "--output",
        type=Path,
        default=default_output,
        help="Output enriched JSONL",
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
        default=os.environ.get("UNSOLVED_ENRICH_OLLAMA_HOST", DEFAULT_OLLAMA_HOST),
        help=f"Ollama base URL (default: {DEFAULT_OLLAMA_HOST})",
    )
    p.add_argument(
        "--model",
        default=os.environ.get("UNSOLVED_ENRICH_OLLAMA_MODEL", DEFAULT_OLLAMA_MODEL),
        help=f"Ollama model name (default: {DEFAULT_OLLAMA_MODEL})",
    )
    p.add_argument(
        "--batch-size",
        type=int,
        default=DEFAULT_BATCH_SIZE,
        help=f"Cases per Ollama request (default: {DEFAULT_BATCH_SIZE})",
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
        help="Process at most this many not-yet-done input lines (for testing)",
    )
    args = p.parse_args(argv)

    output_path = args.output.resolve()
    failed_path = (args.failed or output_path.with_suffix(".failed.jsonl")).resolve()
    progress_path = (args.progress or output_path.with_suffix(".progress.json")).resolve()

    run_enrich(
        EnrichConfig(
            input_path=args.input.resolve(),
            output_path=output_path,
            failed_path=failed_path,
            progress_path=progress_path,
            ollama_host=args.ollama_host,
            ollama_model=args.model,
            batch_size=max(1, args.batch_size),
            timeout=args.timeout,
            think=args.think,
            limit=args.limit,
        )
    )


if __name__ == "__main__":
    main()
