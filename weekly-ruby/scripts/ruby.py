"""Entry point for the weekly-ruby skill."""

from __future__ import annotations

from pathlib import Path
import sys

RUNTIME_ROOT = Path(__file__).resolve().parent / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from weekly_ruby.cli import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
