#!/usr/bin/env python3
"""
Sigrok MCP Server — sigrok-cli + Pico LogicAnalyzer backend.
Supports all sigrok-compatible devices and the Raspberry Pi Pico LogicAnalyzer
(https://github.com/gusmanb/logicanalyzer).

Usage:
  python3 server.py

Register in ~/.claude/settings.json:
  "sigrok": {
    "command": "python3",
    "args": ["/home/agge/claude/logic-analyser/sigrok-mcp-server/server.py"]
  }
"""

import asyncio
import json
import sys
from pathlib import Path

_HERE = Path(__file__).parent
sys.path.insert(0, str(_HERE / "backend"))
sys.path.insert(0, str(_HERE.parent / "shared"))
sys.path.insert(0, str(_HERE.parent / "logic2-mcp-server" / "backend"))

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp import types

import session as sess
from capture import SigrokClient, SigrokError, get_client, list_supported_decoders
from analysis import (
    analyse_capture, run_assertions, inject_gpio_markers, profile_from_markers,
)
from build_flash import build, flash, build_and_flash, BuildFlashError
from protocol_fingerprints import (
    compute_channel_stats, score_hypotheses, fingerprint_protocol,
    format_hypotheses, format_protocol_candidates,
)

server = Server("sigrok-mcp-server")


def _ok(data) -> list[types.TextContent]:
    return [types.TextContent(type="text", text=json.dumps(data, indent=2) if not isinstance(data, str) else data)]


def _err(msg: str) -> list[types.TextContent]:
    return [types.TextContent(type="text", text=f"ERROR: {msg}")]


@server.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="list_devices",
            description="List all connected sigrok-compatible devices and the Pico LogicAnalyzer.",
            inputSchema={"type": "object", "properties": {}},
        ),
        types.Tool(
            name="list_decoders",
            description="List all available sigrok protocol decoders.",
            inputSchema={"type": "object", "properties": {}},
        ),
        types.Tool(
            name="start_capture",
            description="Start a capture. Auto-selects Pico LogicAnalyzer if connected, otherwise uses sigrok-cli.",
            inputSchema={
                "type": "object",
                "properties": {
                    "channels": {"type": "array", "items": {"type": "integer"}},
                    "sample_rate_hz": {"type": "integer", "default": 10000000},
                    "duration_seconds": {"type": "number", "default": 2.0},
                    "trigger_channel": {"type": "integer"},
                    "trigger_type": {"type": "string", "enum": ["rising", "falling", "high", "low"]},
                    "capture_name": {"type": "string"},
                    "driver": {"type": "string", "description": "sigrok driver name (auto-detected if omitted)"},
                    "force_pico": {"type": "boolean", "description": "Force use of Pico LogicAnalyzer"},
                    "save_as": {"type": "string", "enum": ["latest", "baseline"]},
                },
            },
        ),
        types.Tool(
            name="observe_all_channels",
            description="Passive capture across all channels. Returns signal statistics and activity.",
            inputSchema={
                "type": "object",
                "properties": {
                    "duration_seconds": {"type": "number", "default": 2.0},
                    "sample_rate_hz": {"type": "integer", "default": 10000000},
                    "num_channels": {"type": "integer", "default": 8},
                    "force_pico": {"type": "boolean"},
                },
            },
        ),
        types.Tool(
            name="score_pin_hypotheses",
            description="Analyse a capture and return ranked pin function hypotheses for each channel.",
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
            description=(
                "Run a sigrok protocol decoder on a capture. "
                "Decoder IDs: spi, i2c, uart, can, onewire_link, jtag, swd, i2s, pwm, sdcard_spi, nec_ir, dht11, "
                "ds1307, lm75, usb_signalling, manchester, parallel, and 200+ more. "
                "Use list_decoders to see all available."
            ),
            inputSchema={
                "type": "object",
                "required": ["decoder"],
                "properties": {
                    "decoder": {"type": "string", "description": "sigrok decoder ID (e.g. 'spi', 'i2c', 'uart')"},
                    "channel_map": {
                        "type": "object",
                        "description": "Map decoder pin names to channel indices, e.g. {\"clk\": 0, \"data\": 1}",
                        "additionalProperties": {"type": "integer"},
                    },
                    "decoder_options": {
                        "type": "object",
                        "description": "Decoder-specific options, e.g. {\"baudrate\": \"115200\"}",
                        "additionalProperties": {"type": "string"},
                    },
                    "capture_path": {"type": "string"},
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
            description="Label channels and save to session state.",
            inputSchema={
                "type": "object",
                "required": ["labels"],
                "properties": {
                    "labels": {
                        "type": "object",
                        "additionalProperties": {"type": "string"},
                    },
                },
            },
        ),
        types.Tool(
            name="compare_captures",
            description="Diff two captures and highlight signal/timing changes.",
            inputSchema={
                "type": "object",
                "properties": {
                    "capture_a": {"type": "string"},
                    "capture_b": {"type": "string"},
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
            description="Run all defined assertions against a capture.",
            inputSchema={
                "type": "object",
                "properties": {"capture_path": {"type": "string"}},
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
                    "fqbn": {"type": "string"},
                    "board": {"type": "string"},
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
                    "port": {"type": "string"},
                    "fqbn": {"type": "string"},
                    "binary_name": {"type": "string"},
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
                    "force_pico": {"type": "boolean"},
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
            description="Compute execution time statistics from GPIO toggle pairs.",
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
            description="Return current session state.",
            inputSchema={"type": "object", "properties": {}},
        ),
        types.Tool(
            name="reset_session",
            description="Clear all session state.",
            inputSchema={"type": "object", "properties": {}},
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
    try:
        return await _dispatch(name, arguments)
    except SigrokError as e:
        return _err(str(e))
    except BuildFlashError as e:
        return _err(str(e))
    except Exception as e:
        return _err(f"Unexpected error in {name}: {e}")


async def _dispatch(name: str, args: dict) -> list[types.TextContent]:
    client = get_client()
    state = sess.load()

    if name == "list_devices":
        devices = client.list_devices()
        return _ok({"devices": devices, "count": len(devices)})

    if name == "list_decoders":
        decoders = list_supported_decoders()
        return _ok({"decoders": decoders, "count": len(decoders)})

    if name == "get_session":
        return _ok(sess.summary())

    if name == "reset_session":
        sess.reset()
        return _ok({"status": "session cleared"})

    if name == "annotate_channels":
        for ch, label in args["labels"].items():
            sess.annotate_channel(ch, label)
        return _ok({"status": "annotated", "channels": sess.load()["channels"]})

    if name == "start_capture":
        result = client.start_capture(
            channels=args.get("channels"),
            sample_rate_hz=args.get("sample_rate_hz", 10_000_000),
            duration_seconds=args.get("duration_seconds", 2.0),
            trigger_channel=args.get("trigger_channel"),
            trigger_type=args.get("trigger_type", "rising"),
            capture_name=args.get("capture_name"),
            driver=args.get("driver"),
            force_pico=args.get("force_pico", False),
        )
        save_as = args.get("save_as", "latest")
        sess.set_capture(save_as, result["path"])
        return _ok(result)

    if name == "observe_all_channels":
        n_ch = args.get("num_channels", 8)
        channels = list(range(n_ch))
        result = client.start_capture(
            channels=channels,
            sample_rate_hz=args.get("sample_rate_hz", 10_000_000),
            duration_seconds=args.get("duration_seconds", 2.0),
            capture_name="observe_all",
            force_pico=args.get("force_pico", False),
        )
        sess.set_capture("latest", result["path"])
        edges = client.get_raw_edges(result["path"], channels)
        duration = result.get("duration_seconds", 2.0)
        stats_list = [compute_channel_stats(edges, ch, duration) for ch in channels]
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
            return _err("No capture path and no latest capture in session.")
        channels = args.get("channels", list(range(8)))
        duration = args.get("capture_duration", 2.0)
        edges = client.get_raw_edges(capture_path, channels)
        _, hyps, _ = analyse_capture(edges, channels, duration)
        return _ok({"hypotheses": [vars(h) for h in hyps], "formatted": format_hypotheses(hyps)})

    if name == "fingerprint_protocol":
        capture_path = args.get("capture_path") or state["captures"].get("latest")
        if not capture_path:
            return _err("No capture path and no latest capture in session.")
        channels = args.get("channels", list(range(8)))
        duration = args.get("capture_duration", 2.0)
        edges = client.get_raw_edges(capture_path, channels)
        _, _, protocols = analyse_capture(edges, channels, duration)
        return _ok({
            "candidates": [vars(c) for c in protocols],
            "formatted": format_protocol_candidates(protocols),
        })

    if name == "run_analyzer":
        capture_path = args.get("capture_path") or state["captures"].get("latest")
        if not capture_path:
            return _err("No capture path. Run start_capture first.")
        frames = client.run_decoder(
            capture_path,
            args["decoder"],
            channel_map=args.get("channel_map"),
            options=args.get("decoder_options"),
        )
        max_f = args.get("max_frames", 100)
        return _ok({"decoder": args["decoder"], "frame_count": len(frames), "frames": frames[:max_f]})

    if name == "measure_timing":
        capture_path = args.get("capture_path") or state["captures"].get("latest")
        if not capture_path:
            return _err("No capture path. Run start_capture first.")
        return _ok(client.measure_timing(capture_path, args["channel"]))

    if name == "compare_captures":
        path_a = args.get("capture_a") or state["captures"].get("baseline")
        path_b = args.get("capture_b") or state["captures"].get("latest")
        if not path_a or not path_b:
            return _err("Need two captures. Set baseline and latest first.")
        channels = args.get("channels", list(range(8)))
        edges_a = client.get_raw_edges(path_a, channels)
        edges_b = client.get_raw_edges(path_b, channels)
        diff_lines = [f"Comparing:\n  A: {path_a}\n  B: {path_b}\n"]
        for ch in channels:
            sa = compute_channel_stats(edges_a, ch, 2.0)
            sb = compute_channel_stats(edges_b, ch, 2.0)
            if sa.edge_count == sb.edge_count and sa.frequency_hz == sb.frequency_hz:
                continue
            diff_lines.append(f"CH{ch}: edges {sa.edge_count}→{sb.edge_count}, freq {sa.frequency_hz}→{sb.frequency_hz} Hz")
        return _ok("\n".join(diff_lines) if len(diff_lines) > 1 else "No significant differences detected.")

    if name == "define_assertion":
        sess.add_assertion(args["id"], args["description"], args["type"], args["params"])
        return _ok({"status": "assertion defined", "id": args["id"]})

    if name == "run_assertions":
        capture_path = args.get("capture_path") or state["captures"].get("latest")
        if not capture_path:
            return _err("No capture to assert against.")
        assertions = state["assertions"]
        if not assertions:
            return _ok({"status": "no assertions defined"})

        # Build a thin shim so sigrok edge data works with the assertion runner
        class _SigrokAssertClient:
            def __init__(self, c, cp, chs):
                self._c, self._cp, self._chs = c, cp, chs
            def measure_timing(self, path, ch):
                return self._c.measure_timing(path, ch)
            def get_raw_edges(self, path, chs):
                return self._c.get_raw_edges(path, chs)
            def run_protocol_analyzer(self, path, analyzer, settings):
                return self._c.run_decoder(path, analyzer.lower().replace(" ", "_"), options=settings)

        shim = _SigrokAssertClient(client, capture_path, list(range(8)))
        results = run_assertions(assertions, capture_path, shim)
        passed = sum(1 for r in results if r["result"] == "PASS")
        return _ok({"total": len(results), "passed": passed, "failed": len(results) - passed, "results": results})

    if name == "build_firmware":
        ok, output = build(args["project_path"], platform=args.get("platform"),
                           fqbn=args.get("fqbn", "arduino:avr:uno"), board=args.get("board", "pico"))
        return _ok({"status": "build_ok", "output": output})

    if name == "flash_firmware":
        ok, output = flash(args["project_path"], platform=args.get("platform"),
                           fqbn=args.get("fqbn", "arduino:avr:uno"), port=args.get("port", "/dev/ttyACM0"),
                           binary_name=args.get("binary_name"))
        return _ok({"status": "flash_ok", "output": output})

    if name == "build_and_flash":
        ok, bf_output = build_and_flash(
            args["project_path"], platform=args.get("platform"),
            fqbn=args.get("fqbn", "arduino:avr:uno"), port=args.get("port", "/dev/ttyACM0"),
            board=args.get("board", "pico"), binary_name=args.get("binary_name"),
        )
        capture_result = client.start_capture(
            channels=args.get("capture_channels"),
            sample_rate_hz=args.get("sample_rate_hz", 10_000_000),
            duration_seconds=args.get("capture_duration_seconds", 5.0),
            trigger_channel=args.get("trigger_channel"),
            trigger_type=args.get("trigger_type", "rising"),
            capture_name="post_flash",
            force_pico=args.get("force_pico", False),
        )
        sess.set_capture("latest", capture_result["path"])
        return _ok({"build_flash": "ok", "build_output": bf_output, "capture": capture_result})

    if name == "inject_gpio_markers":
        result = inject_gpio_markers(
            args["source_file"], args["function_name"], args["gpio_pin"], args["platform"],
        )
        return _ok(result)

    if name == "profile_from_markers":
        capture_path = args.get("capture_path") or state["captures"].get("latest")
        if not capture_path:
            return _err("No capture path. Run build_and_flash first.")

        class _ShimClient:
            def get_raw_edges(self, path, chs):
                return client.get_raw_edges(path, chs)

        result = profile_from_markers(capture_path, args["marker_channel"], _ShimClient())
        return _ok(result)

    return _err(f"Unknown tool: {name}")


async def main() -> None:
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


if __name__ == "__main__":
    asyncio.run(main())
