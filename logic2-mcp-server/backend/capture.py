"""
Logic 2 automation API wrapper.
Connects to the running Logic 2 application via automation socket (default port 10430).
Captures appear live in the Logic 2 UI window as they run.

Requires: pip install logic2-automation
Logic 2 must be running with automation server enabled:
  Preferences → Developer → Enable automation server
"""

from __future__ import annotations
import csv
import statistics
import time
from pathlib import Path
from typing import Any

try:
    from saleae import automation
    from saleae.automation import (
        CaptureConfiguration,
        DataTableExportConfiguration,
        DigitalTriggerCaptureMode,
        DigitalTriggerType,
        LogicDeviceConfiguration,
        RadixType,
        TimedCaptureMode,
    )
    SALEAE_AVAILABLE = True
except ImportError:
    SALEAE_AVAILABLE = False


CAPTURES_DIR = Path.home() / ".claude" / "logic_analyser_captures"
CAPTURES_DIR.mkdir(parents=True, exist_ok=True)


class Logic2Error(Exception):
    pass


def _require_saleae() -> None:
    if not SALEAE_AVAILABLE:
        raise Logic2Error(
            "logic2-automation package not installed.\n"
            "Run: pip install logic2-automation\n"
            "Also ensure Logic 2 is running with automation server enabled:\n"
            "  Preferences → Developer → Enable automation server"
        )


# ---------------------------------------------------------------------------
# Capability map by DeviceType
# ---------------------------------------------------------------------------

_CAPS: dict[str, dict] = {
    "LOGIC_PRO_16": {"digital_channels": 16, "analog_channels": 16, "max_digital_msa": 500},
    "LOGIC_PRO_8":  {"digital_channels": 8,  "analog_channels": 8,  "max_digital_msa": 500},
    "LOGIC_8":      {"digital_channels": 8,  "analog_channels": 8,  "max_digital_msa": 100},
    "LOGIC_4":      {"digital_channels": 4,  "analog_channels": 4,  "max_digital_msa": 12},
    "LOGIC_16":     {"digital_channels": 16, "analog_channels": 0,  "max_digital_msa": 100},
    "LOGIC":        {"digital_channels": 8,  "analog_channels": 0,  "max_digital_msa": 24},
}


class Logic2Client:
    """Manages a connection to the Logic 2 automation server."""

    def __init__(self, port: int = 10430):
        _require_saleae()
        self.port = port
        self._manager: Any = None

    def connect(self) -> dict:
        try:
            self._manager = automation.Manager.connect(port=self.port)
        except Exception as e:
            raise Logic2Error(
                f"Cannot connect to Logic 2 (port {self.port}): {e}\n"
                "Make sure Logic 2 is running and the automation server is enabled:\n"
                "  Preferences → Developer → Enable automation server"
            )
        return {"status": "connected", "port": self.port}

    def _mgr(self) -> Any:
        if self._manager is None:
            self.connect()
        return self._manager

    # ------------------------------------------------------------------
    # Device info
    # ------------------------------------------------------------------

    def list_devices(self) -> list[dict]:
        devices = self._mgr().get_devices()
        return [
            {
                "device_id": d.device_id,
                "device_type": d.device_type.name,
                "is_simulation": d.is_simulation,
            }
            for d in devices
        ]

    def get_device_capabilities(self, device_id: str | None = None) -> dict:
        devices = self._mgr().get_devices()
        if not devices:
            raise Logic2Error("No Saleae devices found. Connect a device and try again.")
        device = (
            next((d for d in devices if str(d.device_id) == str(device_id)), devices[0])
            if device_id else devices[0]
        )
        dt_name = device.device_type.name  # e.g. "LOGIC_PRO_16"
        caps = {"device_id": device.device_id, "device_type": dt_name}
        caps.update(_CAPS.get(dt_name, {"digital_channels": 8, "analog_channels": 0, "max_digital_msa": 100}))
        return caps

    # ------------------------------------------------------------------
    # Capture
    # ------------------------------------------------------------------

    def start_capture(
        self,
        duration_seconds: float = 2.0,
        digital_channels: list[int] | None = None,
        sample_rate_hz: int = 10_000_000,
        trigger_channel: int | None = None,
        trigger_type: str = "rising",
        capture_name: str | None = None,
    ) -> dict:
        mgr = self._mgr()
        devices = mgr.get_devices()
        if not devices:
            raise Logic2Error("No Saleae devices connected.")

        device = devices[0]
        channels = digital_channels or list(range(8))

        device_cfg = LogicDeviceConfiguration(
            enabled_digital_channels=channels,
            digital_sample_rate=sample_rate_hz,
        )

        if trigger_channel is not None:
            ttype_map = {
                "rising":     DigitalTriggerType.RISING,
                "falling":    DigitalTriggerType.FALLING,
                "pulse_high": DigitalTriggerType.PULSE_HIGH,
                "pulse_low":  DigitalTriggerType.PULSE_LOW,
                # convenience aliases
                "high":       DigitalTriggerType.PULSE_HIGH,
                "low":        DigitalTriggerType.PULSE_LOW,
            }
            ttype = ttype_map.get(trigger_type.lower(), DigitalTriggerType.RISING)
            cap_mode = DigitalTriggerCaptureMode(
                trigger_type=ttype,
                trigger_channel_index=trigger_channel,
                after_trigger_seconds=duration_seconds,
            )
        else:
            cap_mode = TimedCaptureMode(duration_seconds=duration_seconds)

        cap_cfg = CaptureConfiguration(capture_mode=cap_mode)

        capture = mgr.start_capture(
            device_id=device.device_id,
            device_configuration=device_cfg,
            capture_configuration=cap_cfg,
        )
        capture.wait()

        name = capture_name or f"capture_{int(time.time())}"
        save_path = str(CAPTURES_DIR / f"{name}.sal")
        capture.save_capture(filepath=save_path)
        capture.close()

        return {
            "status": "complete",
            "path": save_path,
            "name": name,
            "duration_seconds": duration_seconds,
            "channels": channels,
            "sample_rate_hz": sample_rate_hz,
        }

    # ------------------------------------------------------------------
    # Raw edge export
    # ------------------------------------------------------------------

    def get_raw_edges(self, capture_path: str, channels: list[int]) -> list[tuple[float, int, int]]:
        """
        Load a .sal capture, export digital CSV, return (timestamp, channel, level) tuples.
        """
        mgr = self._mgr()
        capture = mgr.load_capture(capture_path)
        export_dir = str(CAPTURES_DIR / Path(capture_path).stem)
        Path(export_dir).mkdir(exist_ok=True)

        try:
            capture.export_raw_data_csv(
                directory=export_dir,
                digital_channels=channels,
            )
        finally:
            capture.close()

        return _parse_logic2_csv_dir(export_dir, channels)

    # ------------------------------------------------------------------
    # Protocol analyzer
    # ------------------------------------------------------------------

    def run_protocol_analyzer(
        self,
        capture_path: str,
        analyzer: str,
        settings: dict,
    ) -> list[dict]:
        """
        Add a Logic 2 protocol analyzer and export its frames.
        analyzer: "SPI" | "I2C" | "Async Serial" | "CAN" | "1-Wire" | "I2S" | ...
        settings: dict of analyzer settings
        """
        mgr = self._mgr()
        capture = mgr.load_capture(capture_path)

        try:
            handle = capture.add_analyzer(analyzer, settings=settings)
            output_path = capture_path.replace(".sal", f"_{analyzer.replace(' ', '_')}_frames.csv")
            cfg = DataTableExportConfiguration(handle, RadixType.HEXADECIMAL)
            capture.export_data_table(filepath=output_path, analyzers=[cfg])
        finally:
            capture.close()

        return _parse_frames_csv(output_path)

    # ------------------------------------------------------------------
    # Timing measurement
    # ------------------------------------------------------------------

    def measure_timing(self, capture_path: str, channel: int) -> dict:
        edges = self.get_raw_edges(capture_path, [channel])
        return _compute_timing(edges, channel)

    # ------------------------------------------------------------------
    # Close
    # ------------------------------------------------------------------

    def close(self) -> None:
        if self._manager:
            try:
                self._manager.close()
            except Exception:
                pass
            self._manager = None


# ---------------------------------------------------------------------------
# CSV parsing helpers
# ---------------------------------------------------------------------------

def _parse_logic2_csv_dir(export_dir: str, channels: list[int]) -> list[tuple[float, int, int]]:
    """
    Logic 2 exports one CSV per channel named 'digital_<ch>.csv' or combined.
    Parses all and returns sorted (timestamp, channel, level) tuples.
    """
    edges: list[tuple[float, int, int]] = []
    export_path = Path(export_dir)

    # Try per-channel files first
    for ch in channels:
        for pattern in (f"digital_{ch}.csv", f"channel_{ch}.csv"):
            f = export_path / pattern
            if f.exists():
                edges.extend(_parse_single_channel_csv(str(f), ch))
                break

    # Fallback: look for any CSV and parse all columns
    if not edges:
        for csv_file in export_path.glob("*.csv"):
            edges.extend(_parse_multichannel_csv(str(csv_file), channels))
            if edges:
                break

    return sorted(edges)


def _parse_single_channel_csv(filepath: str, channel: int) -> list[tuple[float, int, int]]:
    """Parse a single-channel Logic 2 digital CSV: Time[s], Value"""
    edges: list[tuple[float, int, int]] = []
    try:
        with open(filepath) as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    t = float(row.get("Time[s]") or row.get("Time [s]") or row.get("time") or 0)
                    v = int(float(row.get("Value") or row.get("value") or 0))
                    edges.append((t, channel, v))
                except (ValueError, KeyError):
                    continue
    except FileNotFoundError:
        pass
    return edges


def _parse_multichannel_csv(filepath: str, channels: list[int]) -> list[tuple[float, int, int]]:
    """Parse a combined-channels CSV where each column is a channel."""
    edges: list[tuple[float, int, int]] = []
    try:
        with open(filepath) as f:
            reader = csv.DictReader(f)
            if reader.fieldnames is None:
                return edges
            for row in reader:
                try:
                    t_key = next((k for k in row if "time" in k.lower()), None)
                    if t_key is None:
                        continue
                    t = float(row[t_key])
                    for ch in channels:
                        for col in (f"Channel {ch}", f"channel_{ch}", str(ch)):
                            if col in row:
                                edges.append((t, ch, int(float(row[col]))))
                                break
                except (ValueError, KeyError):
                    continue
    except FileNotFoundError:
        pass
    return edges


def _parse_frames_csv(filepath: str) -> list[dict]:
    """Parse Logic 2 data table export CSV into frame dicts."""
    frames: list[dict] = []
    try:
        with open(filepath) as f:
            reader = csv.DictReader(f)
            for row in reader:
                frames.append(dict(row))
    except FileNotFoundError:
        pass
    return frames


def _compute_timing(edges: list[tuple[float, int, int]], channel: int) -> dict:
    ch_edges = [(t, lvl) for t, ch, lvl in edges if ch == channel]
    if len(ch_edges) < 4:
        return {"error": f"Insufficient edges on CH{channel} for timing measurement"}

    high_widths, low_widths = [], []
    last_t, last_lvl = ch_edges[0]
    for t, lvl in ch_edges[1:]:
        w = t - last_t
        (high_widths if last_lvl == 1 else low_widths).append(w)
        last_t, last_lvl = t, lvl

    result: dict = {"channel": channel}
    if high_widths:
        result["pulse_width_high_us"] = statistics.mean(high_widths) * 1e6
    if low_widths:
        result["pulse_width_low_us"] = statistics.mean(low_widths) * 1e6
    if high_widths and low_widths:
        periods = [h + l for h, l in zip(high_widths, low_widths)]
        if periods:
            mean_p = statistics.mean(periods)
            result["frequency_hz"] = 1.0 / mean_p
            result["period_us"] = mean_p * 1e6
            result["duty_cycle_pct"] = statistics.mean(high_widths) / mean_p * 100
    return result


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_client: Logic2Client | None = None


def get_client(port: int = 10430) -> Logic2Client:
    global _client
    if _client is None:
        _client = Logic2Client(port=port)
        _client.connect()
    return _client
