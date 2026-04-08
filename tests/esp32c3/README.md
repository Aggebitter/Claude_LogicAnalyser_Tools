# Logic Analyser Test Firmware — ESP32-C3

Test firmware for validating the Claude Code logic analyser skills
([Claude_LogicAnalyser_Tools](https://github.com/Aggebitter/Claude_LogicAnalyser_Tools)).
Generates eight simultaneous, well-known signals covering every signal type
the skills are designed to identify and decode.

Supports two ESP32-C3 board variants via `board_config.h`.

---

## Board Variants

### ESP32-C3 DevKit (DevKitM-1 / DevKitC-02) — default

| CH | GPIO | Signal | Description |
|----|------|--------|-------------|
| 0 | GPIO 0 | PWM | 1 kHz, 50% duty cycle |
| 1 | GPIO 1 | CLOCK | 100 kHz square wave |
| 2 | GPIO 2 | SPI CS | Active-low, bursts every 200 ms |
| 3 | GPIO 3 | SPI CLK | ~1 MHz during SPI burst |
| 4 | GPIO 4 | SPI MOSI | Sequence: 0xA5, 0x3C, 0xFF, 0x00, 0x55, 0xAA |
| 5 | GPIO 5 | UART TX | 115200 baud, repeating ASCII messages |
| 6 | GPIO 6 | IRQ SIM | 10 ms active-high pulse every 500 ms |
| 7 | GPIO 7 | HEARTBEAT | 1 Hz toggle |

---

### Seeed Studio XIAO ESP32-C3

GPIO 0 and 1 are not exposed on the XIAO header. Signals shift to GPIO 2–9,
mapping directly to the XIAO board pin labels.

| CH | XIAO pin | GPIO | Signal | Description |
|----|----------|------|--------|-------------|
| 0 | D0 | GPIO 2 | PWM | 1 kHz, 50% duty cycle |
| 1 | D1 | GPIO 3 | CLOCK | 100 kHz square wave |
| 2 | D2 | GPIO 4 | SPI CS | Active-low, bursts every 200 ms |
| 3 | D3 | GPIO 5 | SPI CLK | ~1 MHz during SPI burst |
| 4 | D4 | GPIO 6 | SPI MOSI | Sequence: 0xA5, 0x3C, 0xFF, 0x00, 0x55, 0xAA |
| 5 | D5 | GPIO 7 | UART TX | 115200 baud, repeating ASCII messages |
| 6 | D8 | GPIO 8 | IRQ SIM | 10 ms active-high pulse every 500 ms |
| 7 | D9 | GPIO 9 | HEARTBEAT | 1 Hz toggle |

> Note: D6 (GPIO21) and D7 (GPIO20) are skipped — CH6 and CH7 jump to D8/D9.
> GPIO 2, 8, 9 are strapping pins; they are safe as outputs after boot.

All signals share GND as common reference. Connect logic analyser ground to
any GND pin on the board.

---

## What Each Signal Tests

### Free Running Mode (`/sigrok-reverse` or `/logic2-reverse`)

Attach all 8 channels then invoke the skill. Expected identifications:

| Signal | Expected hypothesis | Expected protocol |
|--------|--------------------|--------------------|
| CH0 PWM | `pwm` | — |
| CH1 CLOCK | `clock` | — |
| CH2 SPI CS | `chip_select` | SPI (with CH3/CH4) |
| CH3 SPI CLK | `clock` | SPI |
| CH4 SPI MOSI | `data` | SPI MOSI |
| CH5 UART TX | `data` | UART |
| CH6 IRQ SIM | `irq` | — |
| CH7 HEARTBEAT | `enable` | — |

### Claude Mode (`/sigrok-debug` or `/logic2-debug`)

| Assertion | Type | Expected |
|-----------|------|----------|
| PWM frequency = 1000 Hz ±5% | timing | PASS |
| PWM duty cycle = 50% ±5% | timing | PASS |
| CLOCK frequency = 100 000 Hz ±5% | timing | PASS |
| SPI CS falls before first SPI CLK edge | logic | PASS |
| UART decodes `HELLO LA_TEST\r\n` | protocol | PASS |
| IRQ pulse width = 10 ms ±10% | timing | PASS |
| IRQ period = 500 ms ±5% | timing | PASS |

---

## Build

Uses the `dev-esp32` Docker image (ESP-IDF v5.5.4).

### DevKit (default)

```bash
docker run --rm \
  -v /home/agge/claude/esp32/projects/logic_analyser_test:/workspace \
  dev-esp32:latest \
  bash -c "source /opt/esp-idf/export.sh > /dev/null 2>&1 \
           && cd /workspace \
           && idf.py set-target esp32c3 \
           && idf.py build"
```

### XIAO ESP32-C3

```bash
docker run --rm \
  -v /home/agge/claude/esp32/projects/logic_analyser_test:/workspace \
  dev-esp32:latest \
  bash -c "source /opt/esp-idf/export.sh > /dev/null 2>&1 \
           && cd /workspace \
           && idf.py set-target esp32c3 \
           && idf.py -DEXTRA_CFLAGS=\"-DBOARD_XIAO\" build"
```

Output binary: `build/logic_analyser_test.bin`

---

## Flash

Connect the board via USB, confirm port (`ls /dev/ttyACM* /dev/ttyUSB*`), then:

```bash
docker run --rm \
  --device /dev/ttyACM0 \
  -v /home/agge/claude/esp32/projects/logic_analyser_test:/workspace \
  dev-esp32:latest \
  bash -c "source /opt/esp-idf/export.sh > /dev/null 2>&1 \
           && cd /workspace \
           && idf.py -p /dev/ttyACM0 flash"
```

---

## Monitor

```bash
docker run --rm \
  --device /dev/ttyACM0 \
  -v /home/agge/claude/esp32/projects/logic_analyser_test:/workspace \
  dev-esp32:latest \
  bash -c "source /opt/esp-idf/export.sh > /dev/null 2>&1 \
           && cd /workspace \
           && idf.py -p /dev/ttyACM0 monitor"
```

Expected boot output (DevKit example):

```
I (330) LA_TEST: Logic Analyser Test — ESP32-C3 DevKit
I (330) LA_TEST: Connect logic analyser:
I (330) LA_TEST:   CH0 GPIO00  PWM       1 kHz 50%
I (330) LA_TEST:   CH1 GPIO01  CLOCK     100 kHz
I (330) LA_TEST:   CH2 GPIO02  SPI CS    active-low burst
I (330) LA_TEST:   CH3 GPIO03  SPI CLK   ~1 MHz
I (330) LA_TEST:   CH4 GPIO04  SPI MOSI  0xA5/0x3C/...
I (330) LA_TEST:   CH5 GPIO05  UART TX   115200 baud
I (330) LA_TEST:   CH6 GPIO06  IRQ SIM   10ms/500ms
I (330) LA_TEST:   CH7 GPIO07  HEARTBEAT 1 Hz
```

---

## Recommended Sample Rates

| Capturing | Minimum sample rate |
|-----------|-------------------|
| All 8 channels overview | 4 MSa/s |
| SPI decode (CS + CLK + MOSI) | 10 MSa/s |
| UART decode | 2 MSa/s |
| CLOCK accuracy | 10 MSa/s |
| IRQ timing | 1 MSa/s |

---

## Hardware Notes

- Tested on **ESP32-C3-DevKitM-1**, **ESP32-C3-DevKitC-02**, and **XIAO ESP32-C3**
- The 100 kHz clock uses a busy-loop pinned to core 1 at priority 24 — do not assign other high-priority tasks to core 1
- SPI is software-bitbanged to avoid DMA overhead obscuring signal shape
- UART output uses UART1, not UART0 (USB-CDC console)
- On XIAO, the onboard RGB LED uses GPIO10 — not used in this firmware
