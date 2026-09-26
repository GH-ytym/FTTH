"""Compatibility wrapper for `ftth doctor` (QGIS, worker modules, frozen algorithms)."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.cli import main

if __name__ == "__main__":
    raise SystemExit(main(["doctor", *sys.argv[1:]]))
