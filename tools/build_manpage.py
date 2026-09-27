#!/usr/bin/env python3
"""Build or verify the checked-in manual using only Python's standard library."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
# Direct invocation must use this checkout, not an older installed Driftstamp.
sys.path.insert(0, str(PROJECT_ROOT))

from driftstamp.manpage import render_manpage  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true",
                        help="exit 1 if the generated page is missing or stale; do not write")
    parser.add_argument("--output", type=Path, default=PROJECT_ROOT / "man" / "driftstamp.1",
                        help="output path (default: man/driftstamp.1 in this checkout)")
    args = parser.parse_args(argv)
    expected = render_manpage().encode("utf-8")
    try:
        if args.check:
            try:
                actual = args.output.read_bytes()
            except FileNotFoundError:
                actual = None
            if actual != expected:
                print(f"Manual is missing or stale: {args.output}; run python3 tools/build_manpage.py",
                      file=sys.stderr)
                return 1
            print(f"Manual is up to date: {args.output}")
            return 0
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_bytes(expected)
        print(f"Generated {args.output}")
        return 0
    except OSError as exc:
        print(f"Cannot {'check' if args.check else 'write'} manual: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
