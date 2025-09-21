"""Test configuration helpers for the Camofleet monorepo."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

for relative in ("control-plane", "runner", "worker", "shared"):
    candidate = str(ROOT / relative)
    if candidate not in sys.path:
        sys.path.insert(0, candidate)
