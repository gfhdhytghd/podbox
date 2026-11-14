"""Unified WPS replaces FU22's separate screen; run production source checks."""
from pathlib import Path
import subprocess,sys
r=Path(__file__).resolve().parent
subprocess.run([sys.executable,str(r/'cider_source_test.py'),sys.argv[1]],check=True)
