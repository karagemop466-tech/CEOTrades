#!/usr/bin/env python3
"""Deprecated safeguard.

Earlier revisions used this filename for a hand-populated sample fixture. That
violates the current project rule: production insider-trade data must come only
from official SEC collectors, and market prices must come only from real market
price sources/caches. This script is intentionally disabled so it cannot insert
manual rows or synthetic price paths.

Use instead:

    python3 collector/collect_ytd.py --year 2026 --replace-year
    python3 collector/build_data.py
    python3 collector/build_pages.py
"""
from __future__ import annotations

import sys

MESSAGE = (
    "collector/populate_verified_data.py is disabled. "
    "Use collector/collect_ytd.py or collector/bulk_backfill.py + collector/collect.py "
    "to gather data from official SEC sources. No hard-coded trades or synthetic prices are allowed."
)


def main() -> int:
    print(MESSAGE, file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
