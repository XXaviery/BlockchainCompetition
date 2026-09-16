// lidar.h
#ifndef LIDAR_H
#define LIDAR_H

#include <Arduino.h>
#include <stdint.h>

#define LIDAR_DEFAULT_RX_PIN 18
#define LIDAR_DEFAULT_TX_PIN 17
#define LIDAR_DEFAULT_BAUD 230400
#define LIDAR_POINT_COUNT 360

#define LIDAR_FRAME_HEAD_1 0xAA
#define LIDAR_FRAME_HEAD_2 0x55
#define LIDAR_FRAME_TAIL_1 0x55
#define LIDAR_FRAME_TAIL_2 0xAA
#define LIDAR_FRAME_TYPE_SCAN 0x01
#define LIDAR_FRAME_PAYLOAD_SIZE (LIDAR_POINT_COUNT * 2)
#define LIDAR_INVALID_DISTANCE_MM 0
#define LIDAR_BLIND_SECTOR_MEASURE_MODE 0
#define LIDAR_IGNORE_SECTOR_COUNT 3
#define LIDAR_DEFAULT_IGNORE_SECTORS \
    {{55, 65, 80}, {108, 275, 80}, {282, 292, 110}}
#define LIDAR_VALID_ANGLE_REPORT_MODE 0
#define LIDAR_VALID_ANGLE_REPORT_INTERVAL_MS 200
#define LIDAR_VALID_ANGLE_REPORT_MIN_DISTANCE_MM 1
#define LIDAR_VALID_ANGLE_REPORT_MAX_DISTANCE_MM 110
#define LIDAR_MIN_INTENSITY 10
#define LIDAR_DEFAULT_IGNORE_ENABLED (!LIDAR_BLIND_SECTOR_MEASURE_MODE)
#define LIDAR_DEFAULT_SEND_INTERVAL_MS 20  // Minimum spacing between completed scan frames.

typedef struct {
    uint16_t start_degree;
    uint16_t end_degree;
    uint16_t max_distance_mm;
} lidar_ignore_sector_t;

/**
 * @brief Binary frame format sent to the host.
 *
 * AA 55 | type(1) | payload_len(2, little-endian) |
 * 360 * uint16 distance_mm | checksum(1) | 55 AA
 *
 * checksum = low 8 bits of type + payload_len bytes + payload bytes.
 */

/**
 * @brief Initialize the lidar module with the robot's default settings.
 * @param lidarSerial Hardware serial port connected to the lidar.
 * @param rxPin ESP32 RX pin connected to lidar TX.
 * @param txPin ESP32 TX pin connected to lidar RX.
 * @param baud Lidar UART baud rate. MS200 default is 230400.
 * @param ignoreSectors Mechanical blind-sector list, inclusive, with an
 *        independent maximum ignored distance for every sector.
 * @param ignoreSectorCount Number of configured blind sectors.
 * @param ignoreEnabled Whether to emit the configured blind sector as invalid.
 */
void Lidar_Init(HardwareSerial &lidarSerial = Serial1,
                int rxPin = LIDAR_DEFAULT_RX_PIN,
                int txPin = LIDAR_DEFAULT_TX_PIN,
                uint32_t baud = LIDAR_DEFAULT_BAUD,
                const lidar_ignore_sector_t *ignoreSectors = nullptr,
                uint8_t ignoreSectorCount = 0,
                bool ignoreEnabled = LIDAR_DEFAULT_IGNORE_ENABLED);

/**
 * @brief Read lidar data from UART and update the internal 360-degree cache.
 */
void Lidar_Update(void);

/**
 * @brief Send one binary scan frame to the host when the configured interval expires.
 * @param out Output stream, usually Serial for the Raspberry Pi or PC host.
 * @param intervalMs Minimum interval between frames in milliseconds.
 * @return true if a frame was written.
 */
bool Lidar_Send(Stream &out = Serial, uint32_t intervalMs = LIDAR_DEFAULT_SEND_INTERVAL_MS);

/**
 * @brief Print close valid lidar angle ranges as text, for mounting-position tests.
 * @param out Output stream, usually Serial.
 * @param intervalMs Minimum interval between reports.
 * @return true if a text line was written.
 *
 * Example output:
 * 100-150 160-200
 */
bool Lidar_ReportValidAngleRanges(Stream &out = Serial,
                                  uint32_t intervalMs = LIDAR_VALID_ANGLE_REPORT_INTERVAL_MS);

#endif // LIDAR_H
