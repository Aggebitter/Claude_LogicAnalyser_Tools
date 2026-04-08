/*
 * board_config.h — Logic Analyser Test pin assignments
 *
 * Select board at build time with:
 *   idf.py -DBOARD=XIAO build
 *
 * Default (no flag): ESP32-C3 DevKit (DevKitM-1 / DevKitC-02)
 */

#pragma once

/* ── ESP32-C3 DevKit (DevKitM-1, DevKitC-02) ──────────────
 * Uses GPIO 0-7 which are all exposed on the 2×15 header.
 * GPIO 0  = header pin closest to USB, left side
 */
#if !defined(BOARD_XIAO)

#define PIN_PWM       0   /* CH0 */
#define PIN_CLOCK     1   /* CH1 */
#define PIN_SPI_CS    2   /* CH2 */
#define PIN_SPI_CLK   3   /* CH3 */
#define PIN_SPI_MOSI  4   /* CH4 */
#define PIN_UART_TX   5   /* CH5 */
#define PIN_IRQ_SIM   6   /* CH6 */
#define PIN_HEARTBEAT 7   /* CH7 */

#define BOARD_NAME "ESP32-C3 DevKit"

/* ── Seeed Studio XIAO ESP32-C3 ───────────────────────────
 * GPIO 0 and 1 are not exposed on the XIAO header.
 * Uses GPIO 2-9, mapping directly to XIAO board pins D0-D9.
 *
 *   GPIO 2  = D0   CH0
 *   GPIO 3  = D1   CH1
 *   GPIO 4  = D2   CH2
 *   GPIO 5  = D3   CH3
 *   GPIO 6  = D4   CH4
 *   GPIO 7  = D5   CH5
 *   GPIO 8  = D8   CH6  (note: D6/D7 = GPIO21/20, skipped)
 *   GPIO 9  = D9   CH7
 */
#elif defined(BOARD_XIAO)

#define PIN_PWM       2   /* CH0  D0 */
#define PIN_CLOCK     3   /* CH1  D1 */
#define PIN_SPI_CS    4   /* CH2  D2 */
#define PIN_SPI_CLK   5   /* CH3  D3 */
#define PIN_SPI_MOSI  6   /* CH4  D4 */
#define PIN_UART_TX   7   /* CH5  D5 */
#define PIN_IRQ_SIM   8   /* CH6  D8 */
#define PIN_HEARTBEAT 9   /* CH7  D9 */

#define BOARD_NAME "XIAO ESP32-C3"

#endif
