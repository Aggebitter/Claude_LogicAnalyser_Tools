/*
 * Logic Analyser Test Firmware — ESP32-C3
 *
 * Generates eight simultaneous signals for testing the logic analyser skills.
 * Supports ESP32-C3 DevKit and Seeed Studio XIAO ESP32-C3 via board_config.h.
 *
 * Build for DevKit (default):
 *   idf.py set-target esp32c3 && idf.py build
 *
 * Build for XIAO ESP32-C3:
 *   idf.py set-target esp32c3
 *   idf.py -DEXTRA_CFLAGS="-DBOARD_XIAO" build
 *
 * See board_config.h for pin assignments and README.md for full wiring guide.
 */

#include <stdio.h>
#include <string.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "driver/gpio.h"
#include "driver/uart.h"
#include "driver/ledc.h"
#include "esp_log.h"
#include "board_config.h"

static const char *TAG = "LA_TEST";

/* ── LEDC (PWM) ──────────────────────────────────────────── */
static void pwm_init(void)
{
    ledc_timer_config_t timer = {
        .speed_mode      = LEDC_LOW_SPEED_MODE,
        .timer_num       = LEDC_TIMER_0,
        .duty_resolution = LEDC_TIMER_10_BIT,
        .freq_hz         = 1000,
        .clk_cfg         = LEDC_AUTO_CLK,
    };
    ledc_timer_config(&timer);

    ledc_channel_config_t ch = {
        .gpio_num   = PIN_PWM,
        .speed_mode = LEDC_LOW_SPEED_MODE,
        .channel    = LEDC_CHANNEL_0,
        .timer_sel  = LEDC_TIMER_0,
        .duty       = 512,
        .hpoint     = 0,
    };
    ledc_channel_config(&ch);
    ESP_LOGI(TAG, "PWM: GPIO%d  1 kHz  50%%", PIN_PWM);
}

/* ── 100 kHz clock task ──────────────────────────────────── */
static void task_clock(void *arg)
{
    gpio_config_t io = {
        .pin_bit_mask = 1ULL << PIN_CLOCK,
        .mode         = GPIO_MODE_OUTPUT,
    };
    gpio_config(&io);
    ESP_LOGI(TAG, "CLOCK: GPIO%d  100 kHz", PIN_CLOCK);

    for (;;) {
        gpio_set_level(PIN_CLOCK, 1);
        esp_rom_delay_us(5);
        gpio_set_level(PIN_CLOCK, 0);
        esp_rom_delay_us(5);
    }
}

/* ── Software SPI task ───────────────────────────────────── */
static void spi_send_byte(uint8_t byte)
{
    for (int i = 7; i >= 0; i--) {
        gpio_set_level(PIN_SPI_MOSI, (byte >> i) & 1);
        esp_rom_delay_us(1);
        gpio_set_level(PIN_SPI_CLK, 1);
        esp_rom_delay_us(1);
        gpio_set_level(PIN_SPI_CLK, 0);
        esp_rom_delay_us(1);
    }
}

static void task_spi(void *arg)
{
    gpio_config_t io = {
        .pin_bit_mask = (1ULL << PIN_SPI_CS)
                      | (1ULL << PIN_SPI_CLK)
                      | (1ULL << PIN_SPI_MOSI),
        .mode         = GPIO_MODE_OUTPUT,
    };
    gpio_config(&io);
    gpio_set_level(PIN_SPI_CS,   1);
    gpio_set_level(PIN_SPI_CLK,  0);
    gpio_set_level(PIN_SPI_MOSI, 0);

    ESP_LOGI(TAG, "SPI: CS=GPIO%d CLK=GPIO%d MOSI=GPIO%d",
             PIN_SPI_CS, PIN_SPI_CLK, PIN_SPI_MOSI);

    const uint8_t seq[] = { 0xA5, 0x3C, 0xFF, 0x00, 0x55, 0xAA };
    int idx = 0;

    for (;;) {
        gpio_set_level(PIN_SPI_CS, 0);
        esp_rom_delay_us(2);
        spi_send_byte(seq[idx % 6]);
        spi_send_byte(seq[(idx + 1) % 6]);
        idx += 2;
        esp_rom_delay_us(2);
        gpio_set_level(PIN_SPI_CS, 1);
        vTaskDelay(pdMS_TO_TICKS(200));
    }
}

/* ── UART TX task ────────────────────────────────────────── */
#define UART_PORT  UART_NUM_1
#define UART_BAUD  115200

static void uart_init(void)
{
    uart_config_t cfg = {
        .baud_rate  = UART_BAUD,
        .data_bits  = UART_DATA_8_BITS,
        .parity     = UART_PARITY_DISABLE,
        .stop_bits  = UART_STOP_BITS_1,
        .flow_ctrl  = UART_HW_FLOWCTRL_DISABLE,
    };
    uart_driver_install(UART_PORT, 256, 0, 0, NULL, 0);
    uart_param_config(UART_PORT, &cfg);
    uart_set_pin(UART_PORT, PIN_UART_TX, UART_PIN_NO_CHANGE,
                 UART_PIN_NO_CHANGE, UART_PIN_NO_CHANGE);
    ESP_LOGI(TAG, "UART1 TX: GPIO%d  115200 baud", PIN_UART_TX);
}

static void task_uart(void *arg)
{
    const char *msgs[] = {
        "HELLO LA_TEST\r\n",
        "SPI:0xA5 SPI:0x3C\r\n",
        "ESP32-C3 OK\r\n",
    };
    int idx = 0;
    for (;;) {
        const char *m = msgs[idx++ % 3];
        uart_write_bytes(UART_PORT, m, strlen(m));
        vTaskDelay(pdMS_TO_TICKS(500));
    }
}

/* ── IRQ simulation task ─────────────────────────────────── */
static void task_irq_sim(void *arg)
{
    gpio_config_t io = {
        .pin_bit_mask = 1ULL << PIN_IRQ_SIM,
        .mode         = GPIO_MODE_OUTPUT,
    };
    gpio_config(&io);
    gpio_set_level(PIN_IRQ_SIM, 0);
    ESP_LOGI(TAG, "IRQ SIM: GPIO%d  10 ms pulse / 500 ms", PIN_IRQ_SIM);

    for (;;) {
        vTaskDelay(pdMS_TO_TICKS(500));
        gpio_set_level(PIN_IRQ_SIM, 1);
        vTaskDelay(pdMS_TO_TICKS(10));
        gpio_set_level(PIN_IRQ_SIM, 0);
    }
}

/* ── Heartbeat task ──────────────────────────────────────── */
static void task_heartbeat(void *arg)
{
    gpio_config_t io = {
        .pin_bit_mask = 1ULL << PIN_HEARTBEAT,
        .mode         = GPIO_MODE_OUTPUT,
    };
    gpio_config(&io);
    ESP_LOGI(TAG, "HEARTBEAT: GPIO%d  1 Hz", PIN_HEARTBEAT);

    for (;;) {
        gpio_set_level(PIN_HEARTBEAT, 1);
        vTaskDelay(pdMS_TO_TICKS(500));
        gpio_set_level(PIN_HEARTBEAT, 0);
        vTaskDelay(pdMS_TO_TICKS(500));
    }
}

/* ── Entry point ─────────────────────────────────────────── */
void app_main(void)
{
    ESP_LOGI(TAG, "Logic Analyser Test — %s", BOARD_NAME);
    ESP_LOGI(TAG, "Connect logic analyser:");
    ESP_LOGI(TAG, "  CH0 GPIO%02d  PWM       1 kHz 50%%",      PIN_PWM);
    ESP_LOGI(TAG, "  CH1 GPIO%02d  CLOCK     100 kHz",         PIN_CLOCK);
    ESP_LOGI(TAG, "  CH2 GPIO%02d  SPI CS    active-low burst", PIN_SPI_CS);
    ESP_LOGI(TAG, "  CH3 GPIO%02d  SPI CLK   ~1 MHz",          PIN_SPI_CLK);
    ESP_LOGI(TAG, "  CH4 GPIO%02d  SPI MOSI  0xA5/0x3C/...",   PIN_SPI_MOSI);
    ESP_LOGI(TAG, "  CH5 GPIO%02d  UART TX   115200 baud",      PIN_UART_TX);
    ESP_LOGI(TAG, "  CH6 GPIO%02d  IRQ SIM   10ms/500ms",       PIN_IRQ_SIM);
    ESP_LOGI(TAG, "  CH7 GPIO%02d  HEARTBEAT 1 Hz",            PIN_HEARTBEAT);

    pwm_init();
    uart_init();

    xTaskCreatePinnedToCore(task_clock,     "clock",     2048, NULL, 24, NULL, 1);
    xTaskCreate(task_spi,       "spi",       2048, NULL, 5, NULL);
    xTaskCreate(task_uart,      "uart",      2048, NULL, 4, NULL);
    xTaskCreate(task_irq_sim,   "irq",       1024, NULL, 3, NULL);
    xTaskCreate(task_heartbeat, "heartbeat", 1024, NULL, 2, NULL);
}
