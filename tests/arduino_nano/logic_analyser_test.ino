/*
 * Logic Analyser Test Firmware — Arduino Nano (ATmega328P)
 *
 * Generates eight simultaneous signals for testing the logic analyser skills.
 * Equivalent signal set to the ESP32-C3 test, adapted for Nano hardware.
 *
 * Pin map (all signals 5 V logic, 5 V tolerant):
 *
 *   D9   — PWM         ~1 kHz, 50% duty cycle  (Timer1 OC1A hardware PWM)  CH0
 *   D3   — CLOCK       ~8 kHz square wave       (Timer2 OC2B CTC toggle)   CH1
 *   D10  — SPI CS      active-low, burst/200 ms (SS hardware SPI)           CH2
 *   D13  — SPI SCK     125 kHz                  (hardware SPI)              CH3
 *   D11  — SPI MOSI    0xA5, 0x3C, 0xFF, ...    (hardware SPI)              CH4
 *   D8   — UART TX     9600 baud                (SoftwareSerial)            CH5
 *   D4   — IRQ SIM     10 ms pulse / 500 ms                                 CH6
 *   D7   — HEARTBEAT   1 Hz toggle                                          CH7
 *
 * Note: D0/D1 (hardware UART) is left free for Serial monitor.
 *       D8 (SoftwareSerial TX) is the logic-analyser UART test signal.
 */

#include <SPI.h>
#include <SoftwareSerial.h>

/* ── Pin assignments ─────────────────────────────────────── */
#define PIN_PWM        9   /* CH0  OC1A */
#define PIN_CLOCK      3   /* CH1  OC2B */
#define PIN_SPI_CS    10   /* CH2  SS   */
#define PIN_SPI_SCK   13   /* CH3  SCK  */
#define PIN_SPI_MOSI  11   /* CH4  MOSI */
#define PIN_UART_TX    8   /* CH5  SoftwareSerial TX */
#define PIN_IRQ_SIM    4   /* CH6 */
#define PIN_HEARTBEAT  7   /* CH7 */

/* ── SoftwareSerial ──────────────────────────────────────── */
// RX pin unused (2) — only TX connected to logic analyser
SoftwareSerial swSerial(2, PIN_UART_TX);

/* ── Timing state ────────────────────────────────────────── */
static unsigned long lastSpi       = 0;
static unsigned long lastUart      = 0;
static unsigned long lastIrqStart  = 0;
static unsigned long lastHeartbeat = 0;
static bool          irqActive     = false;
static bool          heartState    = false;

static const uint8_t spiSeq[] = { 0xA5, 0x3C, 0xFF, 0x00, 0x55, 0xAA };
static uint8_t       spiIdx   = 0;

static const char *uartMsgs[] = {
    "HELLO LA_TEST\r\n",
    "SPI:0xA5 SPI:0x3C\r\n",
    "ARDUINO NANO OK\r\n",
};
static uint8_t uartIdx = 0;

/* ── Timer1 — 1 kHz PWM on D9 (OC1A) ────────────────────
 * Fast PWM, ICR1=TOP, prescaler=8
 * F = 16MHz / (8 * (ICR1+1))
 * For 1 kHz: ICR1 = 16000000/(8*1000) - 1 = 1999
 * 50% duty:  OCR1A = 999
 */
static void timer1_pwm_init(void)
{
    TCCR1A = (1 << COM1A1)   /* Clear OC1A on compare match, set at BOTTOM */
           | (1 << WGM11);   /* Fast PWM, TOP=ICR1 (mode 14) */
    TCCR1B = (1 << WGM13)
           | (1 << WGM12)
           | (1 << CS11);    /* Prescaler = 8 */
    ICR1   = 1999;
    OCR1A  = 999;             /* 50% duty */
    pinMode(PIN_PWM, OUTPUT);
}

/* ── Timer2 — ~8 kHz clock on D3 (OC2B) ─────────────────
 * CTC mode, toggle OC2B on compare match, prescaler=8
 * F_toggle = 16MHz / (2 * 8 * (OCR2B+1))
 * For 8 kHz: OCR2B = 16000000/(2*8*8000) - 1 = 124
 */
static void timer2_clock_init(void)
{
    TCCR2A = (1 << COM2B0)   /* Toggle OC2B on compare match */
           | (1 << WGM21);   /* CTC mode */
    TCCR2B = (1 << CS21);    /* Prescaler = 8 */
    OCR2B  = 124;
    pinMode(PIN_CLOCK, OUTPUT);
}

/* ── Setup ───────────────────────────────────────────────── */
void setup()
{
    Serial.begin(115200);
    Serial.println(F("Logic Analyser Test - Arduino Nano"));
    Serial.println(F("Connect logic analyser:"));
    Serial.println(F("  CH0 D9   PWM       ~1 kHz 50%"));
    Serial.println(F("  CH1 D3   CLOCK     ~8 kHz"));
    Serial.println(F("  CH2 D10  SPI CS    active-low burst"));
    Serial.println(F("  CH3 D13  SPI SCK   125 kHz"));
    Serial.println(F("  CH4 D11  SPI MOSI  0xA5/0x3C/..."));
    Serial.println(F("  CH5 D8   UART TX   9600 baud"));
    Serial.println(F("  CH6 D4   IRQ SIM   10ms/500ms"));
    Serial.println(F("  CH7 D7   HEARTBEAT 1 Hz"));

    /* Hardware PWM and clock via timers */
    timer1_pwm_init();
    timer2_clock_init();

    /* SPI */
    pinMode(PIN_SPI_CS, OUTPUT);
    digitalWrite(PIN_SPI_CS, HIGH);          /* CS idle high */
    SPI.begin();
    SPI.beginTransaction(SPISettings(125000, MSBFIRST, SPI_MODE0));

    /* SoftwareSerial UART */
    swSerial.begin(9600);

    /* GPIO outputs */
    pinMode(PIN_IRQ_SIM,   OUTPUT);
    pinMode(PIN_HEARTBEAT, OUTPUT);
    digitalWrite(PIN_IRQ_SIM,   LOW);
    digitalWrite(PIN_HEARTBEAT, LOW);

    lastSpi       = millis();
    lastUart      = millis();
    lastIrqStart  = millis();
    lastHeartbeat = millis();
}

/* ── Loop ────────────────────────────────────────────────── */
void loop()
{
    unsigned long now = millis();

    /* ── SPI burst every 200 ms ── */
    if (now - lastSpi >= 200) {
        lastSpi = now;
        digitalWrite(PIN_SPI_CS, LOW);
        SPI.transfer(spiSeq[spiIdx % 6]);
        SPI.transfer(spiSeq[(spiIdx + 1) % 6]);
        spiIdx += 2;
        digitalWrite(PIN_SPI_CS, HIGH);
    }

    /* ── UART message every 500 ms ── */
    if (now - lastUart >= 500) {
        lastUart = now;
        swSerial.print(uartMsgs[uartIdx++ % 3]);
    }

    /* ── IRQ simulation: 10 ms pulse every 500 ms ── */
    if (!irqActive && (now - lastIrqStart >= 500)) {
        lastIrqStart = now;
        irqActive    = true;
        digitalWrite(PIN_IRQ_SIM, HIGH);
    }
    if (irqActive && (now - lastIrqStart >= 10)) {
        irqActive = false;
        digitalWrite(PIN_IRQ_SIM, LOW);
    }

    /* ── Heartbeat 1 Hz ── */
    if (now - lastHeartbeat >= 500) {
        lastHeartbeat = now;
        heartState    = !heartState;
        digitalWrite(PIN_HEARTBEAT, heartState ? HIGH : LOW);
    }
}
