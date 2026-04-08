"""
Sigrok capture backend.
Wraps sigrok-cli for all supported devices and TerminalCapture for the
Raspberry Pi Pico LogicAnalyzer (https://github.com/gusmanb/logicanalyzer).

Requirements:
  apt install sigrok-cli libsigrokdecode-dev
  (for Pico LogicAnalyzer) TerminalCapture in PATH or TERMINAL_CAPTURE_PATH env var
"""

from __future__ import annotations
import csv
import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

CAPTURES_DIR = Path.home() / ".claude" / "logic_analyser_captures"
CAPTURES_DIR.mkdir(parents=True, exist_ok=True)

# TerminalCapture binary location (can override with env var)
TERMINAL_CAPTURE_BIN = os.environ.get("TERMINAL_CAPTURE_PATH", "TerminalCapture")


class SigrokError(Exception):
    pass


def _run(cmd: list[str], timeout: int = 60) -> tuple[int, str, str]:
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return result.returncode, result.stdout, result.stderr


def _require_sigrok() -> None:
    if not shutil.which("sigrok-cli"):
        raise SigrokError(
            "sigrok-cli not found. Install with:\n"
            "  sudo apt install sigrok-cli\n"
            "or run sigrok-mcp-server/install.sh"
        )


# ---------------------------------------------------------------------------
# Device discovery
# ---------------------------------------------------------------------------

def list_devices() -> list[dict]:
    """Enumerate all sigrok-supported devices and the Pico LogicAnalyzer."""
    _require_sigrok()
    devices = []

    code, out, err = _run(["sigrok-cli", "--scan"])
    if code == 0:
        for line in out.splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                devices.append({"type": "sigrok", "description": line})

    # Check for Pico LogicAnalyzer
    pico_port = _find_pico_port()
    if pico_port:
        devices.append({
            "type": "pico_logicanalyzer",
            "description": f"Pico LogicAnalyzer on {pico_port}",
            "port": pico_port,
            "channels": 24,
            "max_sample_rate_msa": 100,
            "max_samples": 131072,
        })

    return devices


def _find_pico_port() -> str | None:
    """Try to find the Pico LogicAnalyzer serial port."""
    import glob
    candidates = (
        glob.glob("/dev/ttyACM*")
        + glob.glob("/dev/ttyUSB*")
        + glob.glob("/dev/serial/by-id/*Pico*")
        + glob.glob("/dev/serial/by-id/*LogicAnalyzer*")
    )
    # Return first found; user can override via environment variable
    env_port = os.environ.get("PICO_ANALYZER_PORT")
    if env_port:
        return env_port
    return candidates[0] if candidates else None


def list_supported_decoders() -> list[str]:
    """Return list of all available sigrok protocol decoders."""
    _require_sigrok()
    code, out, _ = _run(["sigrok-cli", "--list-supported-pd"])
    decoders = []
    for line in out.splitlines():
        line = line.strip()
        if line and not line.startswith("ID") and not line.startswith("-"):
            parts = line.split()
            if parts:
                decoders.append(parts[0])
    return decoders


# ---------------------------------------------------------------------------
# Sigrok-cli capture
# ---------------------------------------------------------------------------

def sigrok_capture(
    driver: str,
    channels: list[int],
    sample_rate_hz: int = 10_000_000,
    num_samples: int | None = None,
    duration_seconds: float | None = 2.0,
    output_format: str = "csv",
    capture_name: str | None = None,
) -> dict:
    """
    Capture using sigrok-cli.
    Returns dict with capture path and metadata.
    """
    _require_sigrok()
    name = capture_name or f"capture_{int(time.time())}"
    output_path = str(CAPTURES_DIR / f"{name}.{output_format}")
    sr_path = str(CAPTURES_DIR / f"{name}.sr")

    ch_str = ",".join(str(c) for c in channels)

    if num_samples is None:
        num_samples = int(sample_rate_hz * (duration_seconds or 2.0))

    cmd = [
        "sigrok-cli",
        "--driver", driver,
        "--config", f"samplerate={sample_rate_hz}",
        "--channels", ch_str,
        "--samples", str(num_samples),
        "--output-file", sr_path,
        "--output-format", "srzip",
    ]

    code, out, err = _run(cmd, timeout=int((duration_seconds or 2.0) + 30))
    if code != 0:
        raise SigrokError(f"sigrok-cli capture failed:\n{err}")

    return {
        "status": "complete",
        "path": sr_path,
        "name": name,
        "channels": channels,
        "sample_rate_hz": sample_rate_hz,
        "num_samples": num_samples,
        "duration_seconds": num_samples / sample_rate_hz,
    }


def export_to_csv(sr_path: str) -> str:
    """Export .sr capture to CSV for analysis."""
    csv_path = sr_path.replace(".sr", ".csv")
    cmd = [
        "sigrok-cli",
        "--input-file", sr_path,
        "--output-format", "csv",
        "--output-file", csv_path,
    ]
    code, out, err = _run(cmd)
    if code != 0:
        raise SigrokError(f"CSV export failed:\n{err}")
    return csv_path


def get_raw_edges_from_csv(csv_path: str, channels: list[int]) -> list[tuple[float, int, int]]:
    """Parse sigrok CSV output into (timestamp, channel, level) tuples."""
    edges: list[tuple[float, int, int]] = []
    try:
        with open(csv_path) as f:
            # Skip comment lines
            lines = [l for l in f if not l.startswith(";")]
        reader = csv.reader(lines)
        header = next(reader, None)
        for row in reader:
            if not row:
                continue
            try:
                t = float(row[0])
                for i, ch in enumerate(channels):
                    col_idx = ch + 1  # first col is timestamp
                    if col_idx < len(row):
                        lvl = int(row[col_idx].strip())
                        edges.append((t, ch, lvl))
            except (ValueError, IndexError):
                continue
    except FileNotFoundError:
        pass
    return sorted(edges)


# ---------------------------------------------------------------------------
# Pico LogicAnalyzer capture (TerminalCapture)
# ---------------------------------------------------------------------------

def pico_capture(
    channels: list[int],
    sample_rate_hz: int = 10_000_000,
    num_samples: int = 131072,
    trigger_channel: int | None = None,
    trigger_type: str = "rising",
    capture_name: str | None = None,
    port: str | None = None,
) -> dict:
    """
    Capture using the Pico LogicAnalyzer TerminalCapture CLI.
    """
    if not shutil.which(TERMINAL_CAPTURE_BIN):
        raise SigrokError(
            f"TerminalCapture not found ('{TERMINAL_CAPTURE_BIN}').\n"
            "Download from https://github.com/gusmanb/logicanalyzer and place in PATH,\n"
            "or set TERMINAL_CAPTURE_PATH environment variable."
        )

    pico_port = port or _find_pico_port()
    if not pico_port:
        raise SigrokError(
            "Pico LogicAnalyzer not found. Check USB connection.\n"
            "Set PICO_ANALYZER_PORT env var to override port detection."
        )

    name = capture_name or f"pico_capture_{int(time.time())}"
    output_path = str(CAPTURES_DIR / f"{name}.csv")

    ch_str = ",".join(str(c) for c in channels)

    cmd = [
        TERMINAL_CAPTURE_BIN,
        "--port", pico_port,
        "--freq", str(sample_rate_hz),
        "--samples", str(num_samples),
        "--channels", ch_str,
        "--output", output_path,
    ]

    if trigger_channel is not None:
        trigger_map = {"rising": "R", "falling": "F", "high": "H", "low": "L"}
        ttype = trigger_map.get(trigger_type.lower(), "R")
        cmd += ["--trigger", str(trigger_channel), "--trigger-type", ttype]

    code, out, err = _run(cmd, timeout=120)
    if code != 0:
        raise SigrokError(f"TerminalCapture failed:\n{out}\n{err}")

    return {
        "status": "complete",
        "path": output_path,
        "name": name,
        "type": "pico",
        "port": pico_port,
        "channels": channels,
        "sample_rate_hz": sample_rate_hz,
        "num_samples": num_samples,
        "duration_seconds": num_samples / sample_rate_hz,
    }


def get_raw_edges_from_pico_csv(csv_path: str, channels: list[int]) -> list[tuple[float, int, int]]:
    """Parse Pico LogicAnalyzer CSV (time,ch0,ch1,...) into edge tuples."""
    edges: list[tuple[float, int, int]] = []
    try:
        with open(csv_path) as f:
            reader = csv.DictReader(f)
            prev_row: dict | None = None
            for row in reader:
                try:
                    t = float(row.get("Time", row.get("time", 0)))
                    for ch in channels:
                        col = str(ch)
                        if col in row:
                            lvl = int(row[col])
                            # Only emit on transitions
                            if prev_row is None or int(prev_row.get(col, lvl)) != lvl:
                                edges.append((t, ch, lvl))
                    prev_row = row
                except (ValueError, KeyError):
                    continue
    except FileNotFoundError:
        pass
    return sorted(edges)


# ---------------------------------------------------------------------------
# Protocol decoding via sigrok-cli
# ---------------------------------------------------------------------------

def run_decoder(
    capture_path: str,
    decoder: str,
    decoder_options: dict | None = None,
    channel_map: dict[str, int] | None = None,
) -> list[dict]:
    """
    Run a sigrok protocol decoder on a capture file.
    decoder: sigrok decoder ID (e.g. 'spi', 'i2c', 'uart', 'can')
    channel_map: {'clk': 0, 'data': 1, ...}  (decoder pin names to channel indices)
    decoder_options: {'baudrate': '115200', ...}
    Returns list of decoded annotation frames.
    """
    _require_sigrok()

    pd_str = decoder
    if channel_map:
        ch_assigns = ":".join(f"{pin}={ch}" for pin, ch in channel_map.items())
        pd_str = f"{decoder}:{ch_assigns}"
    if decoder_options:
        opt_str = ":".join(f"{k}={v}" for k, v in decoder_options.items())
        pd_str = f"{pd_str}:{opt_str}"

    cmd = [
        "sigrok-cli",
        "--input-file", capture_path,
        "--protocol-decoder", pd_str,
        "--pd-annotations", f"{decoder}=all",
        "--output-format", "ascii",
    ]

    code, out, err = _run(cmd, timeout=60)
    if code != 0:
        raise SigrokError(f"Decoder '{decoder}' failed:\n{err}")

    frames = []
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        # sigrok annotation format: starttime-endtime decoder: "data"
        parts = line.split(" ", 2)
        if len(parts) >= 2:
            time_range = parts[0]
            data = parts[-1].strip('"') if len(parts) > 2 else ""
            start, _, end = time_range.partition("-")
            try:
                frames.append({
                    "start_time": float(start),
                    "end_time": float(end) if end else float(start),
                    "data": data,
                    "raw": line,
                })
            except ValueError:
                frames.append({"raw": line, "data": line})

    return frames


# ---------------------------------------------------------------------------
# Timing measurement
# ---------------------------------------------------------------------------

def measure_timing_from_edges(
    edges: list[tuple[float, int, int]],
    channel: int,
    capture_duration: float,
) -> dict:
    import statistics
    ch_edges = [(t, lvl) for t, ch, lvl in edges if ch == channel]
    if len(ch_edges) < 4:
        return {"error": f"Insufficient edges on CH{channel}"}

    high_widths, low_widths = [], []
    last_t, last_lvl = ch_edges[0]
    for t, lvl in ch_edges[1:]:
        w = t - last_t
        (high_widths if last_lvl == 1 else low_widths).append(w)
        last_t, last_lvl = t, lvl

    result: dict[str, Any] = {"channel": channel}
    if high_widths:
        result["pulse_width_high_us"] = statistics.mean(high_widths) * 1e6
    if low_widths:
        result["pulse_width_low_us"] = statistics.mean(low_widths) * 1e6
    if high_widths and low_widths:
        periods = [h + l for h, l in zip(high_widths, low_widths)]
        if periods:
            mean_period = statistics.mean(periods)
            result["frequency_hz"] = 1.0 / mean_period
            result["period_us"] = mean_period * 1e6
            result["duty_cycle_pct"] = statistics.mean(high_widths) / mean_period * 100
    return result


# ---------------------------------------------------------------------------
# Unified client class
# ---------------------------------------------------------------------------

class SigrokClient:
    """
    Unified interface for sigrok-based capture.
    Automatically selects sigrok-cli or Pico TerminalCapture based on available hardware.
    """

    def __init__(self):
        self._devices: list[dict] | None = None
        self._last_capture_path: str | None = None
        self._last_capture_channels: list[int] = []
        self._last_capture_duration: float = 0.0

    def list_devices(self) -> list[dict]:
        self._devices = list_devices()
        return self._devices

    def get_pico_device(self) -> dict | None:
        devices = self._devices or list_devices()
        return next((d for d in devices if d.get("type") == "pico_logicanalyzer"), None)

    def start_capture(
        self,
        channels: list[int] | None = None,
        sample_rate_hz: int = 10_000_000,
        duration_seconds: float = 2.0,
        trigger_channel: int | None = None,
        trigger_type: str = "rising",
        capture_name: str | None = None,
        driver: str | None = None,
        force_pico: bool = False,
    ) -> dict:
        devices = self._devices or list_devices()
        channels = channels or list(range(8))

        pico = next((d for d in devices if d.get("type") == "pico_logicanalyzer"), None)

        if force_pico or (pico and (driver is None or driver == "pico")):
            # Use Pico LogicAnalyzer
            num_samples = int(sample_rate_hz * duration_seconds)
            result = pico_capture(
                channels=channels,
                sample_rate_hz=sample_rate_hz,
                num_samples=min(num_samples, 131072),
                trigger_channel=trigger_channel,
                trigger_type=trigger_type,
                capture_name=capture_name,
                port=pico.get("port") if pico else None,
            )
        else:
            # Use sigrok-cli with specified or first found driver
            sigrok_devices = [d for d in devices if d.get("type") == "sigrok"]
            if not sigrok_devices:
                raise SigrokError("No sigrok devices found. Connect a device and try again.")
            drv = driver or sigrok_devices[0]["description"].split(" ")[0]
            result = sigrok_capture(
                driver=drv,
                channels=channels,
                sample_rate_hz=sample_rate_hz,
                duration_seconds=duration_seconds,
                capture_name=capture_name,
            )

        self._last_capture_path = result["path"]
        self._last_capture_channels = channels
        self._last_capture_duration = result.get("duration_seconds", duration_seconds)
        return result

    def get_raw_edges(self, capture_path: str, channels: list[int]) -> list[tuple[float, int, int]]:
        if capture_path.endswith(".csv"):
            # Could be Pico CSV or sigrok CSV — try Pico format first
            edges = get_raw_edges_from_pico_csv(capture_path, channels)
            if not edges:
                edges = get_raw_edges_from_csv(capture_path, channels)
            return edges
        elif capture_path.endswith(".sr"):
            csv_path = export_to_csv(capture_path)
            return get_raw_edges_from_csv(csv_path, channels)
        return []

    def run_decoder(self, capture_path: str, decoder: str,
                    channel_map: dict | None = None, options: dict | None = None) -> list[dict]:
        return run_decoder(capture_path, decoder, options, channel_map)

    def measure_timing(self, capture_path: str, channel: int) -> dict:
        channels = self._last_capture_channels or [channel]
        edges = self.get_raw_edges(capture_path, channels)
        duration = self._last_capture_duration or 2.0
        return measure_timing_from_edges(edges, channel, duration)

    def get_last_capture(self) -> str | None:
        return self._last_capture_path


_client: SigrokClient | None = None


def get_client() -> SigrokClient:
    global _client
    if _client is None:
        _client = SigrokClient()
    return _client
