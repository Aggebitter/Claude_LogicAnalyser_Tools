"""Sigrok backend session helpers — thin wrapper around shared session."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "shared"))

from session import (  # noqa: F401  re-export
    load, save, annotate_channel, set_protocol, set_device,
    add_assertion, remove_assertion, clear_assertions,
    set_capture, reset, summary,
)
