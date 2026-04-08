"""
Sigrok analysis backend — hypothesis engine, assertion runner, GPIO marker injection.
Re-uses logic2 analysis module (identical logic, different capture client).
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "logic2-mcp-server" / "backend"))

# Import everything from the logic2 analysis module — identical logic
from analysis import (  # noqa: F401
    analyse_capture,
    run_assertions,
    inject_gpio_markers,
    profile_from_markers,
    format_hypotheses,
    format_protocol_candidates,
    _assert_timing,
    _assert_logic,
    _assert_protocol,
)
