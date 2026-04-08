# Claude Logic Analyser Tools — Project Plan

## Overview

A set of Claude Code skills backed by MCP servers that integrate logic analysers into the firmware development loop. Two hardware backends, four skills, one shared build/flash layer covering four MCU platforms.

---

## Goals

- **Free Running Mode:** Autonomously reverse-engineer an unknown chip/circuit — observe signals, form hypotheses, generate and flash MCU probe code, iterate until pins and protocols are identified.
- **Claude Mode:** Act as a signal-level test oracle during active firmware development — verify GPIO/protocol behaviour against assertions, profile execution via GPIO markers, detect regressions between code changes.

---

## Repository Structure

```
Claude_LogicAnalyser_Tools/
├── logic2-mcp-server/          # Saleae Logic 2 backend
│   ├── server.py
│   ├── backend/
│   │   ├── capture.py          # Logic 2 automation API wrapper
│   │   ├── analysis.py         # Hypothesis engine, protocol fingerprinting
│   │   ├── build_flash.py      # Calls shared build/flash layer
│   │   └── session.py          # Persistent session state
│   ├── requirements.txt
│   └── install.sh
│
├── sigrok-mcp-server/          # Sigrok + Pico LogicAnalyzer backend
│   ├── server.py
│   ├── backend/
│   │   ├── capture.py          # sigrok-cli + TerminalCapture wrapper
│   │   ├── analysis.py         # Hypothesis engine, protocol fingerprinting
│   │   ├── build_flash.py      # Calls shared build/flash layer
│   │   └── session.py          # Persistent session state
│   ├── requirements.txt
│   └── install.sh              # Installs sigrok-cli, libsigrokdecode
│
├── skills/
│   ├── logic2-reverse.md       # Saleae — Free Running Mode
│   ├── logic2-debug.md         # Saleae — Claude Mode
│   ├── sigrok-reverse.md       # Sigrok — Free Running Mode
│   └── sigrok-debug.md         # Sigrok — Claude Mode
│
├── shared/
│   ├── build_flash.py          # Build/flash for all 4 MCU platforms
│   ├── session_schema.json     # Common session state schema
│   └── protocol_fingerprints.py
│
└── PROJECT_PLAN.md
```

---

## Hardware Coverage

### Saleae Backend (`logic2-mcp-server`)

Connects to the running Logic 2 application via the Saleae automation socket API (port 10430). Captures appear live in the Logic 2 UI window as they run.

| Device | Digital CH | Analog CH | Max Sample Rate |
|--------|:---:|:---:|:---:|
| Logic 4 | 4 | 4 | 12 MSa/s |
| Logic 8 | 8 | 8 | 100 MSa/s |
| Logic Pro 8 | 8 | 8 | 500 MSa/s |
| Logic Pro 16 | 16 | 16 | 500 MSa/s |

Device capabilities queried at connect time — tools adapt to what is connected.

### Sigrok Backend (`sigrok-mcp-server`)

Wraps `sigrok-cli` for capture and protocol decoding, plus `TerminalCapture` for the Pico LogicAnalyzer.

| Device | Notes |
|--------|-------|
| Raspberry Pi Pico (LogicAnalyzer fw) | 24 ch, 100 MSa/s, 131K samples, dedicated Pico |
| Cypress FX2-based analysers (incl. Saleae clones) | via fx2lafw |
| DSLogic | via libsigrok |
| OpenLogic Sniffer | via libsigrok |
| All other sigrok-supported devices | 200+ supported devices |

Protocol decoders: 200+ via libsigrokdecode (SPI, I2C, UART, CAN, 1-Wire, JTAG, SWD, USB, I2S, MDIO, NEC IR, HDMI CEC, and more).

---

## The Four Skills

| Skill file | Invocation | Backend | Mode |
|-----------|-----------|---------|------|
| `logic2-reverse.md` | `/logic2-reverse` | logic2-mcp-server | Free Running |
| `logic2-debug.md` | `/logic2-debug` | logic2-mcp-server | Claude Mode |
| `sigrok-reverse.md` | `/sigrok-reverse` | sigrok-mcp-server | Free Running |
| `sigrok-debug.md` | `/sigrok-debug` | sigrok-mcp-server | Claude Mode |

---

## MCP Tool API

Both servers expose an identical tool API. Skills are backend-agnostic.

### Capture & Control

| Tool | Description |
|------|-------------|
| `list_devices` | Enumerate connected analysers, channel count, sample rate caps |
| `configure_capture` | Set channels, sample rate, duration, trigger |
| `start_capture` | Start capture (appears in Logic 2 UI if Saleae) |
| `stop_capture` | Halt active capture |
| `set_trigger` | Edge / pattern / pulse-width trigger |
| `wait_for_trigger` | Block until trigger fires |

### Analysis

| Tool | Description |
|------|-------------|
| `observe_all_channels` | Passive multi-channel capture + signal statistics |
| `score_pin_hypotheses` | Ranked function hypothesis per channel (clock/data/CS/IRQ/power) |
| `fingerprint_protocol` | Identify protocol on a channel group |
| `run_analyzer` | Decode protocol frames |
| `get_analyzer_frames` | Return decoded frames as structured data |
| `measure_timing` | Pulse width, period, frequency, duty cycle |
| `compare_captures` | Diff two named captures, highlight what changed |
| `annotate_channels` | Name/label channels, persist to session state |

### Build & Flash

Shared across both backends. Selects tool chain by platform.

| Platform | Build | Flash |
|----------|-------|-------|
| Arduino | `arduino-cli compile --fqbn` | `arduino-cli upload` |
| ESP32 | `idf.py build` | `idf.py flash` |
| Pico | `cmake --build` | `picotool load` |
| Teensy | `pio run` | `pio run --target upload` |

| Tool | Description |
|------|-------------|
| `build_firmware` | Build for specified platform + project path |
| `flash_firmware` | Flash to target |
| `build_and_flash` | Build + flash + arm capture in one step |
| `generate_probe_code` | Generate MCU stimulus code to probe unknown chip |

### Claude Mode Only

| Tool | Description |
|------|-------------|
| `define_assertion` | Declare a timing or logic assertion |
| `run_assertions` | Run all assertions against latest capture |
| `inject_gpio_markers` | Insert profiling GPIO toggles into source at named functions |
| `profile_from_markers` | Compute execution times from GPIO toggle pairs |
| `watch_loop` | Continuous capture-assert loop until assertion fails |

---

## Session State

Persists across mode switches. Free Running Mode writes, Claude Mode reads.

```json
{
  "device": "Logic Pro 16",
  "channels": {
    "0": "SPI_CLK",
    "1": "MOSI",
    "2": "MISO",
    "3": "CS"
  },
  "protocol": "SPI",
  "sample_rate": 10000000,
  "assertions": [],
  "captures": {
    "baseline": null,
    "latest": null
  }
}
```

---

## Mode Behaviour

### Free Running Mode — Discovery Loop

```
OBSERVE ──▶ HYPOTHESIZE ──▶ GENERATE PROBE CODE
   ▲                               │
   │                               ▼
REFINE ◀── INTERPRET ◀──── FLASH TO MCU ──▶ CAPTURE RESPONSE
```

1. Passive capture all channels → score pin function hypotheses
2. Fingerprint protocols on candidate channel groups
3. Generate targeted MCU stimulus code for the target platform
4. Build + flash + capture response
5. Update session state with discovered labels
6. Repeat until all channels are explained

**Pin hypothesis signals:**

| Pattern | Inference |
|---------|-----------|
| Periodic, fixed frequency | Clock |
| Active only during bursts | Chip Select / Enable |
| Transitions correlated with clock | Data (MOSI/MISO/SDA) |
| Long stable + short pulses | IRQ / DRDY / Alert |
| Simultaneous multi-line transitions | Parallel bus |

**Protocol fingerprints:**

| Pattern | Candidate |
|---------|-----------|
| 2-line periodic burst, idle high between | I2C |
| 4-line burst, one line active-low before burst | SPI |
| Single line, start/stop bits, 8–10 bit frames | UART |
| Fixed frame length, differential pair | CAN / RS-485 |
| PWM, single line | Motor / LED / servo |
| 1-wire long reset + short pulses | 1-Wire / DHT |

### Claude Mode — Dev Loop

```
Write/Edit Code ──▶ Build ──▶ Flash ──▶ Capture
       ▲                                   │
       │                                   ▼
  Claude suggests fix ◀── Assert + Interpret signal
```

- Reads session state channel labels (from Free Running or user-provided)
- Arms assertions before flash
- After capture: runs assertions, measures timings, decodes protocols
- Compares against baseline capture if available
- Injects GPIO markers for execution profiling
- Correlates signal anomalies with code locations

---

## Implementation Phases

| Phase | Deliverable |
|-------|-------------|
| 1 | Project scaffold, `shared/` layer, session state schema |
| 2 | `logic2-mcp-server` — capture + analysis tools |
| 3 | `sigrok-mcp-server` — sigrok-cli + TerminalCapture tools |
| 4 | Shared build/flash layer (all 4 platforms) |
| 5 | Hypothesis engine + protocol fingerprinting |
| 6 | Assertion framework + watch loop |
| 7 | GPIO marker injection + profiling |
| 8 | Four skill files |
| 9 | `install.sh` for both servers |
| 10 | Demo projects |

---

## Installation

All project files, MCP servers, and dependencies install under:
```
/home/agge/claude/logic-analyser/
```

Sigrok dependencies (`sigrok-cli`, `libsigrokdecode`, `pulseview`) are installed by `sigrok-mcp-server/install.sh`.

MCP servers are registered in `~/.claude/settings.json`:

```json
{
  "mcpServers": {
    "logic2": {
      "command": "python3",
      "args": ["/home/agge/claude/logic-analyser/logic2-mcp-server/server.py"]
    },
    "sigrok": {
      "command": "python3",
      "args": ["/home/agge/claude/logic-analyser/sigrok-mcp-server/server.py"]
    }
  }
}
```

Skills are installed to `~/.claude/skills/` by the respective `install.sh`.

---

## References

- Saleae Logic 2 Automation API: https://saleae.github.io/logic2-automation/
- Saleae Python package: https://pypi.org/project/saleae/
- Sigrok: https://sigrok.org
- Pico LogicAnalyzer: https://github.com/gusmanb/logicanalyzer
- MCP Python SDK: https://github.com/modelcontextprotocol/python-sdk
