# Logic Analyser Test Firmware — Arduino Nano (ATmega328P)

Test firmware for validating the Claude Code logic analyser skills
([Claude_LogicAnalyser_Tools](https://github.com/Aggebitter/Claude_LogicAnalyser_Tools)).
Generates eight simultaneous signals equivalent to the ESP32-C3 test,
adapted for the Arduino Nano's hardware timers and 5 V logic.

---

## Pin Map

| CH | Pin | Signal | Description |
|----|-----|--------|-------------|
| 0 | D9 | PWM | ~1 kHz, 50% duty cycle (Timer1 OC1A hardware PWM) |
| 1 | D3 | CLOCK | ~8 kHz square wave (Timer2 OC2B CTC toggle) |
| 2 | D10 | SPI CS | Active-low, bursts every 200 ms (hardware SS) |
| 3 | D13 | SPI SCK | 125 kHz (hardware SPI) |
| 4 | D11 | SPI MOSI | Sequence: 0xA5, 0x3C, 0xFF, 0x00, 0x55, 0xAA |
| 5 | D8 | UART TX | 9600 baud (SoftwareSerial) |
| 6 | D4 | IRQ SIM | 10 ms active-high pulse every 500 ms |
| 7 | D7 | HEARTBEAT | 1 Hz toggle |

All signals are 5 V logic. Connect logic analyser ground to any GND pin on the Nano.

> D0/D1 (hardware UART) is kept free for the Serial monitor (USB).
> D8 is the dedicated logic-analyser UART test signal via SoftwareSerial.

---

## Signal Details

### Timer configuration

| Signal | Timer | Mode | Frequency |
|--------|-------|------|-----------|
| PWM (D9) | Timer1, OC1A | Fast PWM, ICR1=1999, OCR1A=999, prescaler=8 | 1 000 Hz |
| CLOCK (D3) | Timer2, OC2B | CTC toggle, OCR2B=124, prescaler=8 | 8 000 Hz |
| SPI SCK (D13) | Hardware SPI | SPI_MODE0, SPI_CLOCK_DIV128 | 125 000 Hz |

### SPI

Hardware SPI master (Mode 0, MSB first, 125 kHz). Sends 2-byte transactions
cycling through `{0xA5, 0x3C, 0xFF, 0x00, 0x55, 0xAA}` every 200 ms.

### UART

SoftwareSerial on D8 at 9600 baud. Sends one of three messages every 500 ms:
```
HELLO LA_TEST\r\n
SPI:0xA5 SPI:0x3C\r\n
ARDUINO NANO OK\r\n
```

---

## What Each Signal Tests

### Free Running Mode (`/sigrok-reverse` or `/logic2-reverse`)

Attach all 8 channels then invoke the skill. Expected identifications:

| Signal | Expected hypothesis | Expected protocol |
|--------|--------------------|--------------------|
| CH0 D9 PWM | `pwm` | — |
| CH1 D3 CLOCK | `clock` | — |
| CH2 D10 SPI CS | `chip_select` | SPI (with CH3/CH4) |
| CH3 D13 SPI SCK | `clock` | SPI |
| CH4 D11 SPI MOSI | `data` | SPI MOSI |
| CH5 D8 UART TX | `data` | UART |
| CH6 D4 IRQ SIM | `irq` | — |
| CH7 D7 HEARTBEAT | `enable` | — |

### Claude Mode (`/sigrok-debug` or `/logic2-debug`)

| Assertion | Type | Expected |
|-----------|------|----------|
| PWM frequency = 1000 Hz ±5% | timing | PASS |
| PWM duty cycle = 50% ±5% | timing | PASS |
| CLOCK frequency = 8000 Hz ±5% | timing | PASS |
| SPI CS falls before first SCK edge | logic | PASS |
| UART decodes `HELLO LA_TEST` | protocol | PASS |
| IRQ pulse width = 10 ms ±10% | timing | PASS |
| IRQ period = 500 ms ±5% | timing | PASS |

---

## Build

Uses the `dev-arduino` Docker image (arduino-cli 1.4.1).

```bash
docker run --rm \
  -v /home/agge/claude/arduino/projects:/projects \
  dev-arduino:latest \
  arduino-cli compile --fqbn arduino:avr:nano /projects/logic_analyser_test
```

Expected output:
```
Sketch uses 4498 bytes (14%) of program storage space. Maximum is 30720 bytes.
Global variables use 388 bytes (18%) of dynamic memory.
```

---

## Flash

Connect the Nano via USB, confirm port (`ls /dev/ttyUSB* /dev/ttyACM*`), then:

```bash
docker run --rm \
  --device /dev/ttyUSB0 \
  -v /home/agge/claude/arduino/projects:/projects \
  dev-arduino:latest \
  arduino-cli upload --fqbn arduino:avr:nano --port /dev/ttyUSB0 /projects/logic_analyser_test
```

> Arduino Nano typically appears as `/dev/ttyUSB0` (CH340 USB-serial chip).
> Older Nano with genuine FTDI chip may appear as `/dev/ttyACM0`.

---

## Monitor

```bash
docker run --rm \
  --device /dev/ttyUSB0 \
  dev-arduino:latest \
  arduino-cli monitor --port /dev/ttyUSB0 --config baudrate=115200
```

Expected output on Serial monitor:

```
Logic Analyser Test - Arduino Nano
Connect logic analyser:
  CH0 D9   PWM       ~1 kHz 50%
  CH1 D3   CLOCK     ~8 kHz
  CH2 D10  SPI CS    active-low burst
  CH3 D13  SPI SCK   125 kHz
  CH4 D11  SPI MOSI  0xA5/0x3C/...
  CH5 D8   UART TX   9600 baud
  CH6 D4   IRQ SIM   10ms/500ms
  CH7 D7   HEARTBEAT 1 Hz
```

---

## Recommended Sample Rates

| Capturing | Minimum sample rate |
|-----------|-------------------|
| All 8 channels overview | 1 MSa/s |
| SPI decode (CS + SCK + MOSI) | 2 MSa/s |
| UART decode (9600 baud) | 500 kSa/s |
| CLOCK accuracy (8 kHz) | 500 kSa/s |
| IRQ timing | 100 kSa/s |

---

## Differences from ESP32-C3 Version

| Property | Arduino Nano | ESP32-C3 |
|----------|-------------|---------|
| Logic level | 5 V | 3.3 V |
| Clock output | ~8 kHz (Timer2 limit) | 100 kHz |
| SPI clock | 125 kHz (hardware) | ~1 MHz (software) |
| UART baud | 9600 (SoftwareSerial limit) | 115200 (hardware UART) |
| Concurrency | Cooperative (loop + millis) | FreeRTOS tasks |
