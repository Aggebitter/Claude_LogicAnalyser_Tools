"""Sigrok backend build/flash helpers — thin wrapper around shared layer."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "shared"))

from build_flash import (  # noqa: F401  re-export
    build, flash, build_and_flash, detect_platform, BuildFlashError,
)
