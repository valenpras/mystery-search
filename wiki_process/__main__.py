"""CLI: python -m wiki_process enrich|flatten ..."""

from __future__ import annotations

import sys


def main(argv: list[str] | None = None) -> None:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        print("Subcommands: enrich, flatten")
        print("  python -m wiki_process enrich --help")
        print("  python -m wiki_process flatten --help")
        return

    cmd, rest = argv[0], argv[1:]
    if cmd == "enrich":
        from wiki_process.enricher import main as enrich_main

        enrich_main(rest)
    elif cmd == "flatten":
        from wiki_process.flatten import main as flatten_main

        flatten_main(rest)
    else:
        raise SystemExit(f"Unknown subcommand {cmd!r}. Use enrich or flatten.")


if __name__ == "__main__":
    main()
