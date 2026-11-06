#!/usr/bin/env python3
"""Artwork regression entry point. Baseline review logs remain in this directory."""
from pathlib import Path
import subprocess
import sys
root = Path(__file__).resolve().parents[1]
raise SystemExit(subprocess.call([sys.executable, str(root / "rebuild-artifacts/run_regressions.py")], cwd=root))
