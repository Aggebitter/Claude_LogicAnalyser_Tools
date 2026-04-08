"""
Logic 2 automation API wrapper.
Connects to the running Logic 2 application via the automation socket (default port 10430).
Captures appear live in the Logic 2 UI window as they run.

Requires: pip install saleae
Logic 2 must be running with automation server enabled:
  Preferences → Developer → Enable automation server
"""

from __future__ import annotations
import time
from pathlib import Path
from typing import Any

try:
    from saleae import automation
    from saleae.automation import (
        CaptureConfiguration,
        DigitalTriggerType,
        DigitalTriggerCaptureMode,
        LogicDeviceConfiguration,
        DeviceType,
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
            "saleae package not installed. Run: pip install saleae\n"
            "Also ensure Logic 2 is running with automation server enabled."
        )


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
                "Make sure Logic 2 is running and automation server is enabled:\n"
                "  Preferences → Developer → Enable automation server"
            )
        return {"status": "connected", "port": self.port}

    def _mgr(self) -> Any:
        if self._manager is None:
            self.connect()
        return self._manager

    def list_devices(self) -> list[dict]:
        devices = self._mgr().get_devices()
        result = []
        for d in devices:
            result.append({
                "device_id": d.device_id,
                "device_type": str(d.device_type),
                "serial": getattr(d, "serial_number", "unknown"),
            })
        return result

    def get_device_capabilities(self, device_id: str | None = None) -> dict:
        devices = self._mgr().get_devices()
        if not devices:
            raise Logic2Error("No Saleae devices found. Connect a device and try again.")

        device = next((d for d in devices if str(d.device_id) == str(device_id)), devices[0]) if device_id else devices[0]
        dt = str(device.device_type)

        caps = {
            "device_id": device.device_id,
            "device_type": dt,
        }

        # Map known device types to capabilities
        capability_map = {
            "LOGIC_PRO_16": {"digital_channels": 16, "analog_channels": 16, "max_digital_msa": 500, "max_analog_msa": 50},
            "LOGIC_PRO_8":  {"digital_channels": 8,  "analog_channels": 8,  "max_digital_msa": 500, "max_analog_msa": 50},
            "LOGIC_8":      {"digital_channels": 8,  "analog_channels": 8,  "max_digital_msa": 100, "max_analog_msa": 6.25},
            "LOGIC_4":      {"digital_channels": 4,  "analog_channels": 4,  "max_digital_msa": 12,  "max_analog_msa": 1},
        }
        for key, vals in capability_map.items():
            if key in dt.upper():
                caps.update(vals)
                break
        else:
            caps.update({"digital_channels": 8, "analog_channels": 0, "max_digital_msa": 100})

        return caps

    def start_capture(
        self,
        duration_seconds: float = 2.0,
        digital_channels: list[int] | None = None,
        sample_rate_hz: int = 10_000_000,
        trigger_channel: int | None = None,
        trigger_type: str = "rising",   # "rising" | "falling" | "high" | "low"
        capture_name: str | None = None,
    ) -> dict:
        """Start a capture. Returns capture metadata including save path."""
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
                "rising":  DigitalTriggerType.RISING,
                "falling": DigitalTriggerType.FALLING,
                "high":    DigitalTriggerType.HIGH,
                "low":     DigitalTriggerType.LOW,
            }
            ttype = ttype_map.get(trigger_type.lower(), DigitalTriggerType.RISING)
            cap_cfg = CaptureConfiguration(
                capture_mode=DigitalTriggerCaptureMode(
                    trigger_channel_index=trigger_channel,
                    trigger_type=ttype,
                    after_trigger_seconds=duration_seconds,
                )
            )
        else:
            cap_cfg = CaptureConfiguration(
                capture_mode=automation.TimedCaptureMode(duration_seconds=duration_seconds)
            )

        capture = mgr.start_capture(
            device_id=device.device_id,
            device_configuration=device_cfg,
            capture_configuration=cap_cfg,
        )
        capture.wait()

        # Save to file
        name = capture_name or f"capture_{int(time.time())}"
        save_path = str(CAPTURES_DIR / f"{name}.sal")
        capture.save_capture(filepath=save_path)

        return {
            "status": "complete",
            "path": save_path,
            "name": name,
            "duration_seconds": duration_seconds,
            "channels": channels,
            "sample_rate_hz": sample_rate_hz,
        }

    def export_raw_data(self, capture_path: str, output_path: str | None = None) -> str:
        """Export digital data from a .sal capture to CSV."""
        output_path = output_path or capture_path.replace(".sal", "_raw.csv")
        mgr = self._mgr()
        capture = mgr.load_capture(capture_path)

        exporter = automation.DataTableExporter(
            iso8601_timestamp=False,
            export_transport_layer=False,
        )
        capture.export_data_table(
            filepath=output_path,
            analyzers=[],
        )
        return output_path

    def run_protocol_analyzer(
        self,
        capture_path: str,
        analyzer: str,
        settings: dict,
    ) -> list[dict]:
        """
        Run a Logic 2 protocol analyzer on a saved capture.
        analyzer: "SPI" | "I2C" | "Async Serial" | "1-Wire" | "CAN" | "I2S" | ...
        settings: dict of analyzer settings (channel assignments, baud rate, etc.)
        Returns list of decoded frames.
        """
        mgr = self._mgr()
        capture = mgr.load_capture(capture_path)

        analyzer_cfg = automation.AnalyzerConfiguration(
            name=analyzer,
            settings=settings,
        )
        loaded = capture.add_analyzer(analyzer_cfg)
        frames = []
        for frame in loaded.get_frames():
            frames.append({
                "start_time": frame.start_time,
                "end_time": frame.end_time,
                "type": frame.type,
                "data": frame.data,
            })
        return frames

    def measure_timing(self, capture_path: str, channel: int) -> dict:
        """Measure pulse width, period, frequency, duty cycle on a channel."""
        mgr = self._mgr()
        capture = mgr.load_capture(capture_path)

        # Export digital data for the channel and compute statistics
        import csv, io
        buf = io.StringIO()
        # Logic 2 automation doesn't expose per-channel timing directly via API,
        # so we export and compute from edge data.
        output_path = capture_path.replace(".sal", f"_ch{channel}_timing.csv")
        capture.export_data_table(filepath=output_path, analyzers=[])

        edges = []
        try:
            with open(output_path) as f:
                reader = csv.DictReader(f)
                for row in reader:
                    try:
                        t = float(row.get("Time [s]", row.get("time", 0)))
                        val = int(float(row.get(f"Channel {channel}", row.get(f"ch{channel}", 0))))
                        edges.append((t, val))
                    except (ValueError, KeyError):
                        continue
        except FileNotFoundError:
            return {"error": "Could not export capture data for timing measurement"}

        if len(edges) < 4:
            return {"error": f"Insufficient edges on CH{channel} for timing measurement"}

        high_widths = []
        low_widths = []
        last_t, last_lvl = edges[0]
        for t, lvl in edges[1:]:
            w = t - last_t
            if last_lvl == 1:
                high_widths.append(w)
            else:
                low_widths.append(w)
            last_t, last_lvl = t, lvl

        import statistics as stats
        result: dict[str, Any] = {"channel": channel}
        if high_widths:
            result["pulse_width_high_us"] = stats.mean(high_widths) * 1e6
        if low_widths:
            result["pulse_width_low_us"] = stats.mean(low_widths) * 1e6
        if high_widths and low_widths:
            periods = [h + l for h, l in zip(high_widths, low_widths)]
            if periods:
                mean_period = stats.mean(periods)
                result["frequency_hz"] = 1.0 / mean_period
                result["period_us"] = mean_period * 1e6
                result["duty_cycle_pct"] = stats.mean(high_widths) / mean_period * 100
        return result

    def get_raw_edges(self, capture_path: str, channels: list[int]) -> list[tuple[float, int, int]]:
        """
        Return raw edge data as list of (timestamp, channel, level).
        Used by the hypothesis engine.
        """
        import csv
        mgr = self._mgr()
        capture = mgr.load_capture(capture_path)
        output_path = capture_path.replace(".sal", "_edges.csv")
        capture.export_data_table(filepath=output_path, analyzers=[])

        edges: list[tuple[float, int, int]] = []
        try:
            with open(output_path) as f:
                reader = csv.DictReader(f)
                for row in reader:
                    try:
                        t = float(row.get("Time [s]", 0))
                        for ch in channels:
                            col = f"Channel {ch}"
                            if col in row:
                                lvl = int(float(row[col]))
                                edges.append((t, ch, lvl))
                    except (ValueError, KeyError):
                        continue
        except FileNotFoundError:
            pass

        return sorted(edges)

    def close(self) -> None:
        if self._manager:
            try:
                self._manager.close()
            except Exception:
                pass
            self._manager = None


# Module-level singleton
_client: Logic2Client | None = None


def get_client(port: int = 10430) -> Logic2Client:
    global _client
    if _client is None:
        _client = Logic2Client(port=port)
        _client.connect()
    return _client
