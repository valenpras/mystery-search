"""Staged Ollama connectivity / generation tests for debugging unsolved_enrich."""

from __future__ import annotations

import json
import sys
import time

import requests

HOST = "http://127.0.0.1:11434"
TIMEOUT = 600


def section(title: str) -> None:
    print(f"\n{'=' * 60}\n{title}\n{'=' * 60}")


def tags() -> None:
    section("Step 1: GET /api/tags")
    t0 = time.time()
    r = requests.get(f"{HOST}/api/tags", timeout=10)
    elapsed = round(time.time() - t0, 2)
    print(f"status={r.status_code} elapsed={elapsed}s")
    r.raise_for_status()
    names = [m["name"] for m in r.json().get("models", [])]
    print("models:", names)


def chat(
    model: str,
    user: str,
    *,
    system: str | None = None,
    fmt: str | None = None,
    extra: dict | None = None,
) -> dict:
    payload: dict = {
        "model": model,
        "messages": ([{"role": "system", "content": system}] if system else [])
        + [{"role": "user", "content": user}],
        "stream": False,
    }
    if fmt:
        payload["format"] = fmt
    if extra:
        payload.update(extra)

    print(f"model={model!r} format={fmt!r} extra={extra!r}")
    print(f"user preview: {user[:120]!r}{'...' if len(user) > 120 else ''}")

    t0 = time.time()
    r = requests.post(f"{HOST}/api/chat", json=payload, timeout=TIMEOUT)
    elapsed = round(time.time() - t0, 2)
    print(f"status={r.status_code} elapsed={elapsed}s")
    r.raise_for_status()
    data = r.json()
    content = data.get("message", {}).get("content") or ""
    print(f"content preview: {content[:500]!r}")
    return data


def main() -> int:
    try:
        tags()

        section("Step 2: Tiny chat — llama3:latest")
        chat("llama3:latest", "Reply with exactly: hello")

        section("Step 3: Tiny chat — qwen3.5:latest")
        chat("qwen3.5:latest", "Reply with exactly: hello")

        section("Step 4: Qwen + format=json (minimal)")
        chat("qwen3.5:latest", 'Return {"ok": true}', fmt="json")

        section("Step 5: Qwen + think=false (if supported)")
        try:
            chat("qwen3.5:latest", "Reply with exactly: hello", extra={"think": False})
        except Exception as e:
            print(f"think=false failed: {e}")

        section("Step 6: Enrichment-shaped payload (short body)")
        user_payload = json.dumps(
            [
                {
                    "article_url": "https://unsolved.com/gallery/bill-beatyaes-haunted-mansion/",
                    "title": "Bill Beaty's Haunted Mansion",
                    "body_text": (
                        "In 1923, Bill Beaty started building a castle in Basking Ridge, New Jersey. "
                        "He died in 1931 before it was finished."
                    ),
                }
            ],
            ensure_ascii=False,
        )
        system = (
            "Extract primary_location, primary_location_explicit, country, case_status "
            "for each case. Return ONLY a JSON array."
        )
        chat("qwen3.5:latest", user_payload, system=system, fmt="json", extra={"think": False})

        print("\nAll steps completed.")
        return 0

    except requests.RequestException as e:
        print(f"\nREQUEST FAILED: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"\nFAILED: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
