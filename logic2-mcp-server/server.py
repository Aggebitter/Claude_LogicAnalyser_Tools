#!/usr/bin/env python3
"""
Logic2 MCP Server — Saleae Logic 2 backend.
Connects to the running Logic 2 application and exposes all analyser tools via MCP.

Usage:
  python3 server.py [--port 10430]

Register in ~/.claude/settings.json:
  "logic2": {
    "command": "python3",
    "args": ["/home/agge/claude/logic-analyser/logic2-mcp-server/server.py"]
  }
"""

import asyncio
import json
import sys
import argparse
from pathlib import Path

# Add shared and backend to path
_HERE = Path(__file__).parent
sys.path.insert(0, str(_HERE / "backend"))
sys.path.insert(0, str(_HERE.parent / "shared"))

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp import types

import session as sess
from capture import Logic2Client, Logic2Error, CAPTURES_DIR
from analysis import (
    analyse_capture, run_assertions, inject_gpio_markers, profile_from_markers,
    format_hypotheses, format_protocol_candidates,
)
from build_flash import build, flash, build_and_flash, detect_platform, BuildFlashError

# ---------------------------------------------------------------------------
# Server setup
# ---------------------------------------------------------------------------

server = Server("logic2-mcp-server")
_client: Logic2Client | None = None


def _get_client(port: int = 10430) -> Logic2Client:
    global _client
    if _client is None:
        _client = Logic2Client(port=port)
        _client.connect()
    return _client


def _ok(data) -> list[types.TextContent]:
    return [types.TextContent(type="text", text=json.dumps(data, indent=2) if not isinstance(data, str) else data)]


def _err(msg: str) -> list[types.TextContent]:
    return [types.TextContent(type="text", text=f"ERROR: {msg}")]


# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

@server.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="list_devices",
            description="List connected Saleae devices and their capabilities.",
            inputSchema={"type": "object", "properties": {}},
        ),
        types.Tool(
            name="configure_capture",
            description="Configure capture parameters (channels, sample rate, duration, trigger).",
            inputSchema={
                "type": "object",
                "properties": {
                    "channels": {"type": "array", "items": {"type": "integer"}, "description": "Channel indices to capture"},
                    "sample_rate_hz": {"type": "integer", "description": "Sample rate in Hz (e.g. 10000000 for 10 MSa/s)"},
                    "duration_seconds": {"type": "number", "description": "Capture duration in seconds"},
                    "trigger_channel": {"type": "integer", "description": "Channel to trigger on (optional)"},
                    "trigger_type": {"type": "string", "enum": ["rising", "falling", "high", "low"], "description": "Trigger edge type"},
                },
            },
        ),
        types.Tool(
            name="start_capture",
            description="Start a capture with current configuration. Returns path to saved capture file.",
            inputSchema={
                "type": "object",
                "properties": {
                    "capture_name": {"type": "string", "description": "Name for this capture (optional)"},
                    "channels": {"type": "array", "items": {"type": "integer"}},
                    "sample_rate_hz": {"type": "integer"},
                    "duration_seconds": {"type": "number"},
                    "trigger_channel": {"type": "integer"},
                    "trigger_type": {"type": "string", "enum": ["rising", "falling", "high", "low"]},
                    "save_as": {"type": "string", "enum": ["latest", "baseline"], "description": "Save this capture as latest or baseline in session"},
                },
            },
        ),
        types.Tool(
            name="observe_all_channels",
            description="Passive capture across all channels. Returns signal statistics and activity per channel.",
            inputSchema={
                "type": "object",
                "properties": {
                    "duration_seconds": {"type": "number", "default": 2.0},
                    "sample_rate_hz": {"type": "integer", "default": 10000000},
                    "num_channels": {"type": "integer", "description": "Number of channels to observe (default: all available)"},
                },
            },
        ),
        types.Tool(
            name="score_pin_hypotheses",
            description="Analyse a capture and return ranked pin function hypotheses for each channel.",
            inputSchema={
                "type": "object",
                "properties": {
                    "capture_path": {"type": "string", "description": "Path to .sal capture file (uses latest if omitted)"},
                    "channels": {"type": "array", "items": {"type": "integer"}},
                    "capture_duration": {"type": "number"},
                },
            },
        ),
        types.Tool(
            name="fingerprint_protocol",
            description="Identify the protocol used by a group of channels.",
            inputSchema={
                "type": "object",
                "properties": {
                    "capture_path": {"type": "string"},
                    "channels": {"type": "array", "items": {"type": "integer"}},
                    "capture_duration": {"type": "number"},
                },
            },
        ),
        types.Tool(
            name="run_analyzer",
            description="Run a Logic 2 protocol analyzer (SPI, I2C, Async Serial, CAN, 1-Wire, I2S, etc.) on a capture.",
            inputSchema={
                "type": "object",
                "required": ["analyzer", "settings"],
                "properties": {
                    "analyzer": {"type": "string", "description": "Analyzer name: 'SPI', 'I2C', 'Async Serial', 'CAN', '1-Wire', 'I2S', 'Manchester', 'JTAG'"},
                    "settings": {"type": "object", "description": "Analyzer settings (channel assignments, baud rate, etc.)"},
                    "capture_path": {"type": "string", "description": "Path to .sal file (uses latest if omitted)"},
                },
            },
        ),
        types.Tool(
            name="get_analyzer_frames",
            description="Return decoded frames from the most recent analyzer run.",
            inputSchema={
                "type": "object",
                "properties": {
                    "capture_path": {"type": "string"},
                    "analyzer": {"type": "string"},
                    "settings": {"type": "object"},
                    "max_frames": {"type": "integer", "default": 100},
                },
            },
        ),
        types.Tool(
            name="measure_timing",
            description="Measure pulse width, period, frequency, and duty cycle on a channel.",
            inputSchema={
                "type": "object",
                "required": ["channel"],
                "properties": {
                    "channel": {"type": "integer"},
                    "capture_path": {"type": "string"},
                },
            },
        ),
        types.Tool(
            name="annotate_channels",
            description="Label one or more channels and save to session state.",
            inputSchema={
                "type": "object",
                "required": ["labels"],
                "properties": {
                    "labels": {
                        "type": "object",
                        "description": "Map of channel index (string) to label, e.g. {\"0\": \"SPI_CLK\", \"1\": \"MOSI\"}",
                        "additionalProperties": {"type": "string"},
                    },
                },
            },
        ),
        types.Tool(
            name="compare_captures",
            description="Diff two captures and highlight timing/signal changes.",
            inputSchema={
                "type": "object",
                "properties": {
                    "capture_a": {"type": "string", "description": "Path to first capture (uses 'baseline' if omitted)"},
                    "capture_b": {"type": "string", "description": "Path to second capture (uses 'latest' if omitted)"},
                    "channels": {"type": "array", "items": {"type": "integer"}},
                },
            },
        ),
        types.Tool(
            name="define_assertion",
            description="Define a timing or logic assertion to verify on the next capture.",
            inputSchema={
                "type": "object",
                "required": ["id", "description", "type", "params"],
                "properties": {
                    "id": {"type": "string"},
                    "description": {"type": "string"},
                    "type": {"type": "string", "enum": ["timing", "logic", "protocol"]},
                    "params": {"type": "object"},
                },
            },
        ),
        types.Tool(
            name="run_assertions",
            description="Run all defined assertions against a capture. Returns PASS/FAIL per assertion.",
            inputSchema={
                "type": "object",
                "properties": {
                    "capture_path": {"type": "string"},
                },
            },
        ),
        types.Tool(
            name="build_firmware",
            description="Build firmware for the specified platform.",
            inputSchema={
                "type": "object",
                "required": ["project_path"],
                "properties": {
                    "project_path": {"type": "string"},
                    "platform": {"type": "string", "enum": ["arduino", "esp32", "pico", "teensy"]},
                    "fqbn": {"type": "string", "description": "Arduino FQBN (e.g. arduino:avr:uno)"},
                    "board": {"type": "string", "description": "Pico board target (pico, pico2, pico_w)"},
                },
            },
        ),
        types.Tool(
            name="flash_firmware",
            description="Flash firmware to the target MCU.",
            inputSchema={
                "type": "object",
                "required": ["project_path"],
                "properties": {
                    "project_path": {"type": "string"},
                    "platform": {"type": "string", "enum": ["arduino", "esp32", "pico", "teensy"]},
                    "port": {"type": "string", "description": "Serial port (e.g. /dev/ttyACM0)"},
                    "fqbn": {"type": "string"},
                    "binary_name": {"type": "string", "description": "Binary name without extension (Pico only)"},
                },
            },
        ),
        types.Tool(
            name="build_and_flash",
            description="Build, flash, and arm capture trigger in one step.",
            inputSchema={
                "type": "object",
                "required": ["project_path"],
                "properties": {
                    "project_path": {"type": "string"},
                    "platform": {"type": "string", "enum": ["arduino", "esp32", "pico", "teensy"]},
                    "port": {"type": "string"},
                    "fqbn": {"type": "string"},
                    "board": {"type": "string"},
                    "binary_name": {"type": "string"},
                    "capture_channels": {"type": "array", "items": {"type": "integer"}},
                    "capture_duration_seconds": {"type": "number", "default": 5.0},
                    "sample_rate_hz": {"type": "integer", "default": 10000000},
                    "trigger_channel": {"type": "integer"},
                    "trigger_type": {"type": "string", "enum": ["rising", "falling", "high", "low"]},
                },
            },
        ),
        types.Tool(
            name="inject_gpio_markers",
            description="Insert GPIO profiling toggles at function entry and exit in source code.",
            inputSchema={
                "type": "object",
                "required": ["source_file", "function_name", "gpio_pin", "platform"],
                "properties": {
                    "source_file": {"type": "string"},
                    "function_name": {"type": "string"},
                    "gpio_pin": {"type": "integer"},
                    "platform": {"type": "string", "enum": ["arduino", "esp32", "pico", "teensy"]},
                },
            },
        ),
        types.Tool(
            name="profile_from_markers",
            description="Compute execution time statistics from GPIO toggle pairs in a capture.",
            inputSchema={
                "type": "object",
                "required": ["marker_channel"],
                "properties": {
                    "marker_channel": {"type": "integer"},
                    "capture_path": {"type": "string"},
                },
            },
        ),
        types.Tool(
            name="get_session",
            description="Return current session state (channel labels, protocol, assertions, capture paths).",
            inputSchema={"type": "object", "properties": {}},
        ),
        types.Tool(
            name="reset_session",
            description="Clear all session state (channel labels, assertions, captures).",
            inputSchema={"type": "object", "properties": {}},
        ),
    ]


# ---------------------------------------------------------------------------
# Tool handlers
# ---------------------------------------------------------------------------

@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
    try:
        return await _dispatch(name, arguments)
    except Logic2Error as e:
        return _err(str(e))
    except BuildFlashError as e:
        return _err(str(e))
    except Exception as e:
        return _err(f"Unexpected error in {name}: {e}")


async def _dispatch(name: str, args: dict) -> list[types.TextContent]:
    client = _get_client()
    state = sess.load()

    # --- Device ---
    if name == "list_devices":
        devices = client.list_devices()
        caps = client.get_device_capabilities()
        return _ok({"devices": devices, "capabilities": caps})

    if name == "configure_capture":
        # Just echo back the config — actual params passed at start_capture time
        return _ok({"status": "configured", "params": args})

    if name == "get_session":
        return _ok(sess.summary())

    if name == "reset_session":
        sess.reset()
        return _ok({"status": "session cleared"})

    if name == "annotate_channels":
        labels = args["labels"]
        for ch, label in labels.items():
            sess.annotate_channel(ch, label)
        return _ok({"status": "annotated", "channels": sess.load()["channels"]})

    # --- Capture ---
    if name in ("start_capture", "configure_capture"):
        result = client.start_capture(
            duration_seconds=args.get("duration_seconds", 2.0),
            digital_channels=args.get("channels"),
            sample_rate_hz=args.get("sample_rate_hz", 10_000_000),
            trigger_channel=args.get("trigger_channel"),
            trigger_type=args.get("trigger_type", "rising"),
            capture_name=args.get("capture_name"),
        )
        save_as = args.get("save_as", "latest")
        sess.set_capture(save_as, result["path"])
        return _ok(result)

    if name == "observe_all_channels":
        caps = client.get_device_capabilities()
        n_ch = args.get("num_channels", caps.get("digital_channels", 8))
        channels = list(range(n_ch))
        result = client.start_capture(
            duration_seconds=args.get("duration_seconds", 2.0),
            digital_channels=channels,
            sample_rate_hz=args.get("sample_rate_hz", 10_000_000),
            capture_name="observe_all",
        )
        sess.set_capture("latest", result["path"])
        edges = client.get_raw_edges(result["path"], channels)
        from protocol_fingerprints import compute_channel_stats, score_hypotheses
        stats_list = [compute_channel_stats(edges, ch, result["duration_seconds"]) for ch in channels]
        hyps = score_hypotheses(stats_list)
        activity = {
            f"CH{s.channel}": {
                "active": s.active,
                "edge_count": s.edge_count,
                "frequency_hz": s.frequency_hz,
                "duty_cycle_pct": s.duty_cycle,
            }
            for s in stats_list
        }
        return _ok({
            "capture": result,
            "channel_activity": activity,
            "hypotheses_preview": format_hypotheses(hyps),
        })

    if name == "score_pin_hypotheses":
        capture_path = args.get("capture_path") or state["captures"].get("latest")
        if not capture_path:
            return _err("No capture path specified and no latest capture in session.")
        channels = args.get("channels", list(range(8)))
        duration = args.get("capture_duration", 2.0)
        edges = client.get_raw_edges(capture_path, channels)
        _, hyps, _ = analyse_capture(edges, channels, duration)
        return _ok({"hypotheses": [vars(h) for h in hyps], "formatted": format_hypotheses(hyps)})

    if name == "fingerprint_protocol":
        capture_path = args.get("capture_path") or state["captures"].get("latest")
        if not capture_path:
            return _err("No capture path specified and no latest capture in session.")
        channels = args.get("channels", list(range(8)))
        duration = args.get("capture_duration", 2.0)
        edges = client.get_raw_edges(capture_path, channels)
        _, _, protocols = analyse_capture(edges, channels, duration)
        return _ok({
            "candidates": [vars(c) for c in protocols],
            "formatted": format_protocol_candidates(protocols),
        })

    if name in ("run_analyzer", "get_analyzer_frames"):
        capture_path = args.get("capture_path") or state["captures"].get("latest")
        if not capture_path:
            return _err("No capture path. Run start_capture first.")
        analyzer = args["analyzer"]
        settings = args.get("settings", {})
        frames = client.run_protocol_analyzer(capture_path, analyzer, settings)
        max_f = args.get("max_frames", 100)
        return _ok({"analyzer": analyzer, "frame_count": len(frames), "frames": frames[:max_f]})

    if name == "measure_timing":
        capture_path = args.get("capture_path") or state["captures"].get("latest")
        if not capture_path:
            return _err("No capture path. Run start_capture first.")
        return _ok(client.measure_timing(capture_path, args["channel"]))

    if name == "compare_captures":
        path_a = args.get("capture_a") or state["captures"].get("baseline")
        path_b = args.get("capture_b") or state["captures"].get("latest")
        if not path_a or not path_b:
            return _err("Need two captures to compare. Set baseline and latest first.")
        channels = args.get("channels", list(range(8)))

        edges_a = client.get_raw_edges(path_a, channels)
        edges_b = client.get_raw_edges(path_b, channels)

        from protocol_fingerprints import compute_channel_stats
        diff_lines = [f"Comparing:\n  A: {path_a}\n  B: {path_b}\n"]
        for ch in channels:
            sa = compute_channel_stats(edges_a, ch, 2.0)
            sb = compute_channel_stats(edges_b, ch, 2.0)
            if sa.edge_count == sb.edge_count and sa.frequency_hz == sb.frequency_hz:
                continue
            diff_lines.append(f"CH{ch}: edges {sa.edge_count}→{sb.edge_count}, freq {sa.frequency_hz}→{sb.frequency_hz} Hz")

        return _ok("\n".join(diff_lines) if len(diff_lines) > 1 else "No significant differences detected.")

    # --- Assertions ---
    if name == "define_assertion":
        sess.add_assertion(args["id"], args["description"], args["type"], args["params"])
        return _ok({"status": "assertion defined", "id": args["id"]})

    if name == "run_assertions":
        capture_path = args.get("capture_path") or state["captures"].get("latest")
        if not capture_path:
            return _err("No capture to assert against. Run start_capture first.")
        assertions = state["assertions"]
        if not assertions:
            return _ok({"status": "no assertions defined"})
        results = run_assertions(assertions, capture_path, client)
        passed = sum(1 for r in results if r["result"] == "PASS")
        return _ok({
            "total": len(results),
            "passed": passed,
            "failed": len(results) - passed,
            "results": results,
        })

    # --- Build / Flash ---
    if name == "build_firmware":
        ok, output = build(
            args["project_path"],
            platform=args.get("platform"),
            fqbn=args.get("fqbn", "arduino:avr:uno"),
            board=args.get("board", "pico"),
        )
        return _ok({"status": "build_ok", "output": output})

    if name == "flash_firmware":
        ok, output = flash(
            args["project_path"],
            platform=args.get("platform"),
            fqbn=args.get("fqbn", "arduino:avr:uno"),
            port=args.get("port", "/dev/ttyACM0"),
            binary_name=args.get("binary_name"),
        )
        return _ok({"status": "flash_ok", "output": output})

    if name == "build_and_flash":
        ok, bf_output = build_and_flash(
            args["project_path"],
            platform=args.get("platform"),
            fqbn=args.get("fqbn", "arduino:avr:uno"),
            port=args.get("port", "/dev/ttyACM0"),
            board=args.get("board", "pico"),
            binary_name=args.get("binary_name"),
        )
        # Start capture immediately after flash
        capture_result = client.start_capture(
            duration_seconds=args.get("capture_duration_seconds", 5.0),
            digital_channels=args.get("capture_channels"),
            sample_rate_hz=args.get("sample_rate_hz", 10_000_000),
            trigger_channel=args.get("trigger_channel"),
            trigger_type=args.get("trigger_type", "rising"),
            capture_name="post_flash",
        )
        sess.set_capture("latest", capture_result["path"])
        return _ok({
            "build_flash": "ok",
            "build_output": bf_output,
            "capture": capture_result,
        })

    # --- Profiling ---
    if name == "inject_gpio_markers":
        result = inject_gpio_markers(
            args["source_file"],
            args["function_name"],
            args["gpio_pin"],
            args["platform"],
        )
        return _ok(result)

    if name == "profile_from_markers":
        capture_path = args.get("capture_path") or state["captures"].get("latest")
        if not capture_path:
            return _err("No capture path. Run build_and_flash first.")
        result = profile_from_markers(capture_path, args["marker_channel"], client)
        return _ok(result)

    return _err(f"Unknown tool: {name}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def main() -> None:
    parser = argparse.ArgumentParser(description="Logic2 MCP Server")
    parser.add_argument("--logic2-port", type=int, default=10430,
                        help="Logic 2 automation server port (default: 10430)")
    args, _ = parser.parse_known_args()

    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


if __name__ == "__main__":
    asyncio.run(main())
