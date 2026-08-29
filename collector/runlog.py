#!/usr/bin/env python3
"""Run logging — tee collector output into collector/data/logs/<name>.log.

The GitHub Actions workflow commits everything under collector/data/, so these
logs travel with the data and every published artifact can be traced back to
the exact collection/build output that produced it. Logging is passive: if the
log directory cannot be written, the collector runs exactly as before.
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.environ.get("CEOTRADES_LOG_DIR") or os.path.join(HERE, "data", "logs")


class _Tee:
    def __init__(self, *streams):
        self.streams = streams

    def write(self, s):
        for st in self.streams:
            try:
                st.write(s)
            except Exception:  # noqa: BLE001
                pass
        return len(s)

    def flush(self):
        for st in self.streams:
            try:
                st.flush()
            except Exception:  # noqa: BLE001
                pass


def start(name: str):
    """Tee stdout/stderr to logs/<name>.log from now on. Returns the file or None."""
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        f = open(os.path.join(LOG_DIR, f"{name}.log"), "w", encoding="utf-8")
    except OSError:
        return None
    sys.stdout = _Tee(sys.stdout, f)
    sys.stderr = _Tee(sys.stderr, f)
    return f
