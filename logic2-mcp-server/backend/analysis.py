"""
Logic2 analysis backend — hypothesis engine, assertion runner, GPIO marker injection.
Delegates signal math to shared/protocol_fingerprints.py.
"""

from __future__ import annotations
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "shared"))
from protocol_fingerprints import (
    ChannelStats, Hypothesis, ProtocolCandidate,
    compute_channel_stats, score_hypotheses, fingerprint_protocol,
    format_hypotheses, format_protocol_candidates,
)

# GPIO marker templates per platform
_MARKER_TEMPLATES: dict[str, dict[str, str]] = {
    "arduino": {
        "setup":  "pinMode({pin}, OUTPUT);",
        "set":    "digitalWrite({pin}, HIGH);",
        "clear":  "digitalWrite({pin}, LOW);",
    },
    "esp32": {
        "setup":  "gpio_set_direction(GPIO_NUM_{pin}, GPIO_MODE_OUTPUT);",
        "set":    "gpio_set_level(GPIO_NUM_{pin}, 1);",
        "clear":  "gpio_set_level(GPIO_NUM_{pin}, 0);",
    },
    "pico": {
        "setup":  "gpio_init({pin}); gpio_set_dir({pin}, GPIO_OUT);",
        "set":    "gpio_put({pin}, 1);",
        "clear":  "gpio_put({pin}, 0);",
    },
    "teensy": {
        "setup":  "pinMode({pin}, OUTPUT);",
        "set":    "digitalWriteFast({pin}, HIGH);",
        "clear":  "digitalWriteFast({pin}, LOW);",
    },
}


def analyse_capture(
    edges: list[tuple[float, int, int]],
    channels: list[int],
    capture_duration: float,
) -> tuple[list[ChannelStats], list[Hypothesis], list[ProtocolCandidate]]:
    """Full pipeline: edges → stats → hypotheses → protocol candidates."""
    stats_list = [compute_channel_stats(edges, ch, capture_duration) for ch in channels]
    stats_map = {s.channel: s for s in stats_list}
    hyps = score_hypotheses(stats_list)
    protocols = fingerprint_protocol(stats_map, hyps)
    return stats_list, hyps, protocols


def run_assertions(assertions: list[dict], capture_path: str, client: Any) -> list[dict]:
    """
    Run defined assertions against a capture.
    Returns list of {id, description, result: "PASS"|"FAIL"|"ERROR", detail}.
    """
    results = []
    for assertion in assertions:
        try:
            result = _evaluate_assertion(assertion, capture_path, client)
        except Exception as e:
            result = {"result": "ERROR", "detail": str(e)}
        results.append({
            "id": assertion["id"],
            "description": assertion["description"],
            **result,
        })
    return results


def _evaluate_assertion(assertion: dict, capture_path: str, client: Any) -> dict:
    kind = assertion["type"]
    params = assertion["params"]

    if kind == "timing":
        return _assert_timing(params, capture_path, client)
    if kind == "logic":
        return _assert_logic(params, capture_path, client)
    if kind == "protocol":
        return _assert_protocol(params, capture_path, client)
    return {"result": "ERROR", "detail": f"Unknown assertion type: {kind}"}


def _assert_timing(params: dict, capture_path: str, client: Any) -> dict:
    """
    params:
      channel: int
      measurement: "frequency" | "period" | "pulse_high" | "pulse_low" | "duty_cycle"
      expected: float
      tolerance_pct: float  (default 5.0)
    """
    ch = params["channel"]
    measurement = params["measurement"]
    expected = float(params["expected"])
    tolerance = float(params.get("tolerance_pct", 5.0))

    timing = client.measure_timing(capture_path, ch)
    if "error" in timing:
        return {"result": "ERROR", "detail": timing["error"]}

    key_map = {
        "frequency": "frequency_hz",
        "period": "period_us",
        "pulse_high": "pulse_width_high_us",
        "pulse_low": "pulse_width_low_us",
        "duty_cycle": "duty_cycle_pct",
    }
    key = key_map.get(measurement)
    if not key or key not in timing:
        return {"result": "ERROR", "detail": f"Measurement '{measurement}' not available for CH{ch}"}

    actual = timing[key]
    pct_diff = abs(actual - expected) / max(abs(expected), 1e-12) * 100
    passed = pct_diff <= tolerance
    return {
        "result": "PASS" if passed else "FAIL",
        "detail": f"CH{ch} {measurement}: expected={expected}, actual={actual:.4f}, diff={pct_diff:.1f}% (tolerance={tolerance}%)",
    }


def _assert_logic(params: dict, capture_path: str, client: Any) -> dict:
    """
    params:
      description: str  (human-readable, stored but not evaluated programmatically)
      channel_a: int
      channel_b: int
      relationship: "a_before_b" | "a_after_b"
      max_delay_us: float (optional)
    """
    # For logic ordering assertions we inspect edge data
    ch_a = params.get("channel_a")
    ch_b = params.get("channel_b")
    relationship = params.get("relationship", "a_before_b")
    max_delay_us = params.get("max_delay_us")

    edges = client.get_raw_edges(capture_path, [ch_a, ch_b])
    if not edges:
        return {"result": "ERROR", "detail": "No edge data returned from capture"}

    first_a = next((t for t, ch, lvl in edges if ch == ch_a and lvl == 0), None)  # falling = active-low CS
    first_b = next((t for t, ch, lvl in edges if ch == ch_b and lvl == 1), None)  # rising = first clock

    if first_a is None:
        return {"result": "ERROR", "detail": f"No transition found on CH{ch_a}"}
    if first_b is None:
        return {"result": "ERROR", "detail": f"No transition found on CH{ch_b}"}

    if relationship == "a_before_b":
        passed = first_a < first_b
        delay_us = (first_b - first_a) * 1e6
        detail = f"CH{ch_a} at {first_a*1e6:.2f}µs, CH{ch_b} at {first_b*1e6:.2f}µs, delay={delay_us:.2f}µs"
        if passed and max_delay_us is not None:
            if delay_us > max_delay_us:
                return {"result": "FAIL", "detail": f"Order correct but delay {delay_us:.2f}µs > max {max_delay_us}µs"}
        return {"result": "PASS" if passed else "FAIL", "detail": detail}

    if relationship == "a_after_b":
        passed = first_a > first_b
        delay_us = (first_a - first_b) * 1e6
        detail = f"CH{ch_b} at {first_b*1e6:.2f}µs, CH{ch_a} at {first_a*1e6:.2f}µs, delay={delay_us:.2f}µs"
        return {"result": "PASS" if passed else "FAIL", "detail": detail}

    return {"result": "ERROR", "detail": f"Unknown relationship: {relationship}"}


def _assert_protocol(params: dict, capture_path: str, client: Any) -> dict:
    """
    params:
      analyzer: str  (Logic 2 analyzer name)
      settings: dict
      expect_frame_type: str (optional)
      expect_data: str (optional, hex string)
    """
    analyzer = params.get("analyzer", "")
    settings = params.get("settings", {})
    frames = client.run_protocol_analyzer(capture_path, analyzer, settings)

    if not frames:
        return {"result": "FAIL", "detail": f"No frames decoded by {analyzer} analyzer"}

    expect_type = params.get("expect_frame_type")
    expect_data = params.get("expect_data")

    for frame in frames:
        if expect_type and frame.get("type") != expect_type:
            continue
        if expect_data:
            frame_data = str(frame.get("data", "")).lower()
            if expect_data.lower() in frame_data:
                return {"result": "PASS", "detail": f"Found expected frame: {frame}"}
        else:
            return {"result": "PASS", "detail": f"Frames decoded ({len(frames)} total). First: {frames[0]}"}

    return {
        "result": "FAIL",
        "detail": f"Expected data not found in {len(frames)} decoded frames",
    }


def inject_gpio_markers(
    source_file: str,
    function_name: str,
    gpio_pin: int,
    platform: str,
) -> dict:
    """
    Insert GPIO SET at function entry and GPIO CLEAR at all return points.
    Returns dict with modified source and diff summary.
    """
    templates = _MARKER_TEMPLATES.get(platform.lower())
    if not templates:
        return {"error": f"Unsupported platform: {platform}. Supported: {list(_MARKER_TEMPLATES)}"}

    src_path = Path(source_file)
    if not src_path.exists():
        return {"error": f"File not found: {source_file}"}

    original = src_path.read_text()
    pin_str = str(gpio_pin)
    marker_set   = templates["set"].replace("{pin}", pin_str)
    marker_clear = templates["clear"].replace("{pin}", pin_str)

    # Find the function body
    # Match: return_type function_name(args) { ... }
    pattern = rf"(\b\w[\w\s\*]+\b\s+{re.escape(function_name)}\s*\([^)]*\)\s*\{{)"
    match = re.search(pattern, original)
    if not match:
        return {"error": f"Function '{function_name}' not found in {source_file}"}

    # Insert SET marker after opening brace
    insert_pos = match.end()
    modified = (
        original[:insert_pos]
        + f"\n    {marker_set}  /* GPIO profiling: {function_name} entry */"
        + original[insert_pos:]
    )

    # Insert CLEAR before each return statement inside the function
    # Simple approach: find all 'return' inside this function
    modified = re.sub(
        rf"(\breturn\b\s*[^;]*;)",
        rf"{marker_clear}  /* GPIO profiling: {function_name} exit */\n    \1",
        modified,
    )

    src_path.write_text(modified)

    return {
        "file": source_file,
        "function": function_name,
        "gpio_pin": gpio_pin,
        "platform": platform,
        "marker_set": marker_set,
        "marker_clear": marker_clear,
        "status": "injected",
    }


def profile_from_markers(
    capture_path: str,
    marker_channel: int,
    client: Any,
) -> dict:
    """Compute execution time statistics from GPIO toggle pairs (SET=entry, CLEAR=exit)."""
    import statistics
    edges = client.get_raw_edges(capture_path, [marker_channel])
    ch_edges = [(t, lvl) for t, ch, lvl in edges if ch == marker_channel]

    durations = []
    entry_time: float | None = None
    for t, lvl in ch_edges:
        if lvl == 1:
            entry_time = t
        elif lvl == 0 and entry_time is not None:
            durations.append((t - entry_time) * 1e6)  # µs
            entry_time = None

    if not durations:
        return {"error": f"No complete entry/exit pairs found on CH{marker_channel}"}

    return {
        "channel": marker_channel,
        "samples": len(durations),
        "min_us": min(durations),
        "max_us": max(durations),
        "mean_us": statistics.mean(durations),
        "stdev_us": statistics.stdev(durations) if len(durations) > 1 else 0.0,
    }
