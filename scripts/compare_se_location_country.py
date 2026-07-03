"""Compare SE_Location vs Qwen-enriched country on enriched Wikipedia pages."""

from __future__ import annotations

import json
import re
import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = ROOT / "data" / "wiki" / "cleaning_pages_db_enriched.json"


def is_empty(v) -> bool:
    if v is None:
        return True
    if isinstance(v, str) and v.strip().lower() in ("", "-", "null", "none"):
        return True
    return False


def norm(s) -> str:
    if s is None:
        return ""
    s = str(s).strip().lower()
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"\s+", " ", s)
    return s


def main() -> None:
    input_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_INPUT
    with input_path.open(encoding="utf-8") as f:
        pages = json.load(f)

    total = len(pages)
    empty_sl: list[dict] = []
    nonempty_sl: list[dict] = []

    for title, page in pages.items():
        row = {
            "title": title,
            "SE_Location": page.get("SE_Location"),
            "country": page.get("country"),
            "Full Location": page.get("Full Location"),
            "primary_location": page.get("primary_location"),
        }
        if is_empty(row["SE_Location"]):
            empty_sl.append(row)
        else:
            nonempty_sl.append(row)

    n = len(nonempty_sl)
    exact = 0
    substring_match = 0
    country_empty = 0
    country_nonempty = 0
    mismatches: list[tuple[str, dict]] = []

    for row in nonempty_sl:
        sl, c = row["SE_Location"], row["country"]
        if is_empty(c):
            country_empty += 1
            mismatches.append(("country_empty", row))
            continue
        country_nonempty += 1
        nsl, nc = norm(sl), norm(c)
        if nsl == nc:
            exact += 1
        elif nsl in nc or nc in nsl:
            substring_match += 1
        else:
            mismatches.append(("mismatch", row))

    exact_plus_sub = exact + substring_match
    clear_mismatch = country_nonempty - exact - substring_match

    print(f"=== Dataset: {input_path.name} ===")
    print(f"Total pages: {total}")
    print(f"SE_Location empty: {len(empty_sl)} ({100 * len(empty_sl) / total:.1f}%)")
    print(f"SE_Location non-empty: {n} ({100 * n / total:.1f}%)")
    print()
    print("=== Among SE_Location non-empty ===")
    print(f"Enriched country empty: {country_empty} ({100 * country_empty / n:.1f}%)")
    print(f"Enriched country non-empty: {country_nonempty} ({100 * country_nonempty / n:.1f}%)")
    print()
    print("=== SE_Location vs enriched country (SE_Location non-empty only) ===")
    print(f"Exact match (normalized): {exact}/{n} = {100 * exact / n:.2f}%")
    if country_nonempty:
        print(
            f"Exact match (both fields set): {exact}/{country_nonempty} "
            f"= {100 * exact / country_nonempty:.2f}%"
        )
    print(f"Substring match only: {substring_match}/{n} = {100 * substring_match / n:.2f}%")
    print(
        f"Exact OR substring: {exact_plus_sub}/{n} = {100 * exact_plus_sub / n:.2f}%"
    )
    if country_nonempty:
        print(
            f"Clear mismatch (both set, no match): {clear_mismatch}/{country_nonempty} "
            f"= {100 * clear_mismatch / country_nonempty:.2f}%"
        )
    print()
    print("=== Mismatch breakdown ===")
    by_kind: dict[str, int] = {}
    for kind, _ in mismatches:
        by_kind[kind] = by_kind.get(kind, 0) + 1
    for kind, count in sorted(by_kind.items()):
        print(f"  {kind}: {count}")

    print()
    print("=== Sample mismatches (up to 15) ===")
    shown = 0
    for kind, row in mismatches:
        if shown >= 15:
            break
        print(
            f"  [{kind}] {row['title'][:60]!r}\n"
            f"    SE_Location={row['SE_Location']!r}\n"
            f"    country={row['country']!r}\n"
            f"    primary_location={row['primary_location']!r}"
        )
        shown += 1


if __name__ == "__main__":
    main()
