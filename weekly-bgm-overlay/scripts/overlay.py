"""Entry point for the bundled deterministic runtime."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent / "runtime"))
from weekly_overlay.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
