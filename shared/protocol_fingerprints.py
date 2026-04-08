"""
Signal statistics analysis and protocol fingerprinting.
Works on raw edge data: list of (timestamp_seconds, channel, level) tuples.
Both backends convert their native capture format to this before calling here.
"""

from __future__ import annotations
import statistics
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ChannelStats:
    channel: int
    edge_count: int = 0
    total_duration: float = 0.0
    # Pulse widths (time high, time low)
    high_widths: list[float] = field(default_factory=list)
    low_widths: list[float] = field(default_factory=list)
    burst_gaps: list[float] = field(default_factory=list)  # gaps between bursts

    @property
    def active(self) -> bool:
        return self.edge_count > 0

    @property
    def frequency_hz(self) -> float | None:
        if not self.high_widths or not self.low_widths:
            return None
        periods = [h + l for h, l in zip(self.high_widths, self.low_widths)]
        if not periods:
            return None
        return 1.0 / statistics.mean(periods)

    @property
    def duty_cycle(self) -> float | None:
        if not self.high_widths or not self.low_widths:
            return None
        periods = [h + l for h, l in zip(self.high_widths, self.low_widths)]
        if not periods:
            return None
        return statistics.mean(self.high_widths) / statistics.mean(periods) * 100

    @property
    def is_periodic(self) -> bool:
        """True if frequency is stable (low coefficient of variation)."""
        if len(self.high_widths) < 4:
            return False
        periods = [h + l for h, l in zip(self.high_widths, self.low_widths)]
        if not periods or statistics.mean(periods) == 0:
            return False
        cv = statistics.stdev(periods) / statistics.mean(periods)
        return cv < 0.05  # <5% variation = periodic

    @property
    def is_bursty(self) -> bool:
        """True if activity is grouped into bursts with idle gaps."""
        if len(self.burst_gaps) < 2:
            return False
        # Bursts: short inter-edge gaps punctuated by long idle gaps
        if not self.high_widths:
            return False
        mean_pulse = statistics.mean(self.high_widths + self.low_widths) if self.low_widths else statistics.mean(self.high_widths)
        return statistics.mean(self.burst_gaps) > mean_pulse * 10

    @property
    def pulse_width_us(self) -> float | None:
        if not self.high_widths:
            return None
        return statistics.mean(self.high_widths) * 1e6


@dataclass
class Hypothesis:
    channel: int
    function: str          # "clock" | "data" | "chip_select" | "irq" | "enable" | "pwm" | "inactive"
    confidence: float      # 0.0 – 1.0
    notes: str = ""


def compute_channel_stats(
    edges: list[tuple[float, int, int]],
    channel: int,
    capture_duration: float,
) -> ChannelStats:
    """
    edges: list of (timestamp, channel, level) sorted by timestamp.
    level: 0 = low, 1 = high.
    """
    ch_edges = [(t, lvl) for t, ch, lvl in edges if ch == channel]
    stats = ChannelStats(channel=channel, total_duration=capture_duration)

    if len(ch_edges) < 2:
        stats.edge_count = len(ch_edges)
        return stats

    stats.edge_count = len(ch_edges)
    last_t, last_lvl = ch_edges[0]

    for t, lvl in ch_edges[1:]:
        width = t - last_t
        if last_lvl == 1:
            stats.high_widths.append(width)
        else:
            stats.low_widths.append(width)
        last_t, last_lvl = t, lvl

    # Detect burst gaps: gaps > 10× median pulse width
    all_widths = stats.high_widths + stats.low_widths
    if all_widths:
        med = statistics.median(all_widths)
        threshold = med * 10
        stats.burst_gaps = [w for w in all_widths if w > threshold]

    return stats


def score_hypotheses(stats_list: list[ChannelStats]) -> list[Hypothesis]:
    """Return ranked hypotheses for each channel."""
    hypotheses: list[Hypothesis] = []

    for s in stats_list:
        if not s.active:
            hypotheses.append(Hypothesis(s.channel, "inactive", 0.95, "No transitions observed"))
            continue

        candidates: list[tuple[float, str, str]] = []  # (confidence, function, notes)

        freq = s.frequency_hz
        freq_str = f"{freq/1e6:.2f} MHz" if freq and freq >= 1e6 else (f"{freq/1e3:.1f} kHz" if freq and freq >= 1e3 else (f"{freq:.0f} Hz" if freq else "?"))

        # Clock: periodic, ~50% duty cycle
        if s.is_periodic:
            dc = s.duty_cycle or 0
            dc_score = 1.0 - abs(dc - 50) / 50
            candidates.append((0.6 + 0.4 * dc_score, "clock", f"Periodic at {freq_str}, duty={dc:.0f}%"))

        # PWM: periodic but not 50% duty cycle
        if s.is_periodic and s.duty_cycle is not None and abs(s.duty_cycle - 50) > 10:
            candidates.append((0.55, "pwm", f"PWM at {freq_str}, duty={s.duty_cycle:.0f}%"))

        # Chip select: bursty, usually active-low (more time high than low)
        if s.is_bursty and s.high_widths and s.low_widths:
            ratio = statistics.mean(s.high_widths) / max(statistics.mean(s.low_widths), 1e-12)
            if ratio > 5:
                candidates.append((0.70, "chip_select", f"Bursty, active-low (high/low ratio={ratio:.1f})"))

        # Data: bursty, roughly equal high/low widths
        if s.is_bursty:
            if s.high_widths and s.low_widths:
                ratio = statistics.mean(s.high_widths) / max(statistics.mean(s.low_widths), 1e-12)
                if 0.2 < ratio < 5:
                    candidates.append((0.55, "data", f"Bursty data pattern, edge_count={s.edge_count}"))

        # IRQ: very few short pulses
        if s.edge_count < 20 and s.pulse_width_us is not None and s.pulse_width_us < 100:
            candidates.append((0.65, "irq", f"Sparse short pulses ({s.edge_count} edges, ~{s.pulse_width_us:.1f} µs wide)"))

        # Enable/reset: very few transitions, long stable periods
        if s.edge_count <= 4:
            candidates.append((0.60, "enable", f"Rare transitions ({s.edge_count} edges) — enable, reset, or power-good"))

        if not candidates:
            candidates.append((0.30, "data", f"Activity detected ({s.edge_count} edges) — function unclear"))

        # Pick highest confidence
        candidates.sort(reverse=True)
        conf, func, notes = candidates[0]
        hypotheses.append(Hypothesis(s.channel, func, conf, notes))

    return hypotheses


# ---------------------------------------------------------------------------
# Protocol fingerprinting
# ---------------------------------------------------------------------------

@dataclass
class ProtocolCandidate:
    protocol: str
    confidence: float
    channel_roles: dict[int, str]   # channel → role name
    notes: str = ""


def fingerprint_protocol(
    stats_map: dict[int, ChannelStats],
    hypotheses: list[Hypothesis],
) -> list[ProtocolCandidate]:
    """
    Given per-channel stats and hypotheses, return ranked protocol candidates
    for the active channel group.
    """
    hyp_by_ch = {h.channel: h for h in hypotheses}
    active = [ch for ch, s in stats_map.items() if s.active]

    clocks   = [ch for ch in active if hyp_by_ch[ch].function == "clock"]
    datas    = [ch for ch in active if hyp_by_ch[ch].function == "data"]
    cs_lines = [ch for ch in active if hyp_by_ch[ch].function == "chip_select"]
    irqs     = [ch for ch in active if hyp_by_ch[ch].function == "irq"]
    pwms     = [ch for ch in active if hyp_by_ch[ch].function == "pwm"]

    candidates: list[ProtocolCandidate] = []

    # SPI: 1 clock + ≥1 data + optional CS
    if len(clocks) >= 1 and len(datas) >= 1:
        roles: dict[int, str] = {clocks[0]: "CLK"}
        conf = 0.65
        if len(datas) >= 2:
            roles[datas[0]] = "MOSI"
            roles[datas[1]] = "MISO"
            conf = 0.80
        else:
            roles[datas[0]] = "MOSI/MISO"
        if cs_lines:
            roles[cs_lines[0]] = "CS"
            conf = min(conf + 0.10, 0.95)
        candidates.append(ProtocolCandidate("SPI", conf, roles, "Clock + data lines detected"))

    # I2C: 2 lines, both bursty, one more periodic (SCL), one less (SDA)
    if len(active) >= 2 and len(clocks) == 1 and len(datas) == 1:
        scl = clocks[0]
        sda = datas[0]
        # I2C clock stretches — SCL not perfectly periodic
        if not stats_map[scl].is_periodic:
            candidates.append(ProtocolCandidate(
                "I2C", 0.75,
                {scl: "SCL", sda: "SDA"},
                "Two bursty lines, SCL with stretching pattern",
            ))
        else:
            candidates.append(ProtocolCandidate(
                "I2C", 0.55,
                {scl: "SCL", sda: "SDA"},
                "Two-wire bursty pattern — I2C or SPI half-duplex",
            ))

    # UART: single bursty line, no clock
    if not clocks and len(datas) == 1:
        candidates.append(ProtocolCandidate(
            "UART", 0.70,
            {datas[0]: "TX/RX"},
            "Single data line, no clock — async serial",
        ))

    # UART two-wire (TX + RX)
    if not clocks and len(datas) == 2:
        candidates.append(ProtocolCandidate(
            "UART", 0.65,
            {datas[0]: "TX", datas[1]: "RX"},
            "Two data lines, no clock — full-duplex UART",
        ))

    # 1-Wire / DHT: single line, very sparse long reset pulses
    for ch in datas:
        s = stats_map[ch]
        if s.pulse_width_us and s.pulse_width_us > 400:
            candidates.append(ProtocolCandidate(
                "1-Wire/DHT", 0.70,
                {ch: "DATA"},
                f"Long reset pulse ~{s.pulse_width_us:.0f} µs",
            ))

    # PWM / motor control
    for ch in pwms:
        candidates.append(ProtocolCandidate(
            "PWM", 0.85,
            {ch: "PWM"},
            f"PWM signal at {stats_map[ch].frequency_hz or 0:.0f} Hz",
        ))

    # CAN: two lines correlated
    if len(datas) >= 2 and not clocks:
        candidates.append(ProtocolCandidate(
            "CAN/RS-485", 0.45,
            {datas[0]: "H/TX", datas[1]: "L/RX"},
            "Differential pair candidate — requires physical layer check",
        ))

    candidates.sort(key=lambda c: c.confidence, reverse=True)
    return candidates


def format_hypotheses(hypotheses: list[Hypothesis]) -> str:
    lines = ["Channel hypotheses (sorted by confidence):"]
    for h in sorted(hypotheses, key=lambda x: x.confidence, reverse=True):
        bar = "█" * int(h.confidence * 10)
        lines.append(f"  CH{h.channel:>2}  {h.function:<14} {h.confidence:.0%}  {bar}  {h.notes}")
    return "\n".join(lines)


def format_protocol_candidates(candidates: list[ProtocolCandidate]) -> str:
    if not candidates:
        return "No protocol candidates identified."
    lines = ["Protocol candidates:"]
    for c in candidates:
        roles_str = ", ".join(f"CH{ch}={role}" for ch, role in c.channel_roles.items())
        lines.append(f"  {c.protocol:<12} {c.confidence:.0%}  [{roles_str}]  {c.notes}")
    return "\n".join(lines)
