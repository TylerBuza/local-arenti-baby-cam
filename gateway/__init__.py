"""Authenticated home connector; no vendor APIs, telemetry or media storage."""
from pathlib import Path
import sys

DEPS = Path(__file__).resolve().parents[1] / 'runtime' / 'gateway-deps'
if DEPS.is_dir():
    sys.path.insert(0, str(DEPS))
