// lidar.cpp
#include "lidar.h"

#include <string.h>

#define MS200_HEAD_1 0xAA
#define MS200_HEAD_2 0x55
#define MS200_DATA_START 0x54
#define MS200_POINT_PER_PACK 12
#define MS200_BUF_MAX 100
#define LIDAR_FRAME_SIZE (5 + LIDAR_FRAME_PAYLOAD_SIZE + 1 + 2)
#define LIDAR_MAX_IGNORE_SECTORS 8

typedef struct __attribute__((packed)) {
    uint16_t distance;
    uint8_t intensity;
} ms200_point_t;

typedef struct __attribute__((packed)) {
    uint8_t header;
    uint8_t count;
    uint16_t speed;
    uint16_t start_angle;
    ms200_point_t points[MS200_POINT_PER_PACK];
    uint16_t end_angle;
    uint16_t time_stamp;
    uint8_t crc8;
} ms200_package_t;

static const uint8_t CRC_TABLE[256] = {
    0x00, 0x4d, 0x9a, 0xd7, 0x79, 0x34, 0xe3, 0xae, 0xf2, 0xbf, 0x68, 0x25,
    0x8b, 0xc6, 0x11, 0x5c, 0xa9, 0xe4, 0x33, 0x7e, 0xd0, 0x9d, 0x4a, 0x07,
    0x5b, 0x16, 0xc1, 0x8c, 0x22, 0x6f, 0xb8, 0xf5, 0x1f, 0x52, 0x85, 0xc8,
    0x66, 0x2b, 0xfc, 0xb1, 0xed, 0xa0, 0x77, 0x3a, 0x94, 0xd9, 0x0e, 0x43,
    0xb6, 0xfb, 0x2c, 0x61, 0xcf, 0x82, 0x55, 0x18, 0x44, 0x09, 0xde, 0x93,
    0x3d, 0x70, 0xa7, 0xea, 0x3e, 0x73, 0xa4, 0xe9, 0x47, 0x0a, 0xdd, 0x90,
    0xcc, 0x81, 0x56, 0x1b, 0xb5, 0xf8, 0x2f, 0x62, 0x97, 0xda, 0x0d, 0x40,
    0xee, 0xa3, 0x74, 0x39, 0x65, 0x28, 0xff, 0xb2, 0x1c, 0x51, 0x86, 0xcb,
    0x21, 0x6c, 0xbb, 0xf6, 0x58, 0x15, 0xc2, 0x8f, 0xd3, 0x9e, 0x49, 0x04,
    0xaa, 0xe7, 0x30, 0x7d, 0x88, 0xc5, 0x12, 0x5f, 0xf1, 0xbc, 0x6b, 0x26,
    0x7a, 0x37, 0xe0, 0xad, 0x03, 0x4e, 0x99, 0xd4, 0x7c, 0x31, 0xe6, 0xab,
    0x05, 0x48, 0x9f, 0xd2, 0x8e, 0xc3, 0x14, 0x59, 0xf7, 0xba, 0x6d, 0x20,
    0xd5, 0x98, 0x4f, 0x02, 0xac, 0xe1, 0x36, 0x7b, 0x27, 0x6a, 0xbd, 0xf0,
    0x5e, 0x13, 0xc4, 0x89, 0x63, 0x2e, 0xf9, 0xb4, 0x1a, 0x57, 0x80, 0xcd,
    0x91, 0xdc, 0x0b, 0x46, 0xe8, 0xa5, 0x72, 0x3f, 0xca, 0x87, 0x50, 0x1d,
    0xb3, 0xfe, 0x29, 0x64, 0x38, 0x75, 0xa2, 0xef, 0x41, 0x0c, 0xdb, 0x96,
    0x42, 0x0f, 0xd8, 0x95, 0x3b, 0x76, 0xa1, 0xec, 0xb0, 0xfd, 0x2a, 0x67,
    0xc9, 0x84, 0x53, 0x1e, 0xeb, 0xa6, 0x71, 0x3c, 0x92, 0xdf, 0x08, 0x45,
    0x19, 0x54, 0x83, 0xce, 0x60, 0x2d, 0xfa, 0xb7, 0x5d, 0x10, 0xc7, 0x8a,
    0x24, 0x69, 0xbe, 0xf3, 0xaf, 0xe2, 0x35, 0x78, 0xd6, 0x9b, 0x4c, 0x01,
    0xf4, 0xb9, 0x6e, 0x23, 0x8d, 0xc0, 0x17, 0x5a, 0x06, 0x4b, 0x9c, 0xd1,
    0x7f, 0x32, 0xe5, 0xa8
};

static HardwareSerial *lidar_port = &Serial1;
static ms200_point_t scan_points[LIDAR_POINT_COUNT];
static ms200_point_t send_points[LIDAR_POINT_COUNT];
static bool point_valid[LIDAR_POINT_COUNT];
static bool full_scan = false;
static bool frame_ready = false;
static uint16_t last_start_angle = 0;
static bool last_start_angle_valid = false;
static uint16_t valid_point_count = 0;
static bool ignore_sector_enabled = false;
static lidar_ignore_sector_t ignore_sectors[LIDAR_MAX_IGNORE_SECTORS];
static uint8_t ignore_sector_count = 0;
static uint8_t rx_protocol_buf[MS200_BUF_MAX];
static ms200_package_t ms200_pkg;

static void lidar_begin(HardwareSerial &lidarSerial, int rxPin, int txPin, uint32_t baud);
static void set_ignore_sectors(const lidar_ignore_sector_t *sectors, uint8_t count, bool enabled);
static bool is_degree_in_sector(uint16_t degree, uint16_t startDegree, uint16_t endDegree);
static bool is_ignored(uint16_t degree, uint16_t distanceMm);
static bool is_report_valid(uint16_t degree);
static size_t write_frame(Stream &out);
static void print_valid_range(Stream &out, uint16_t startDegree, uint16_t endDegree, bool *firstRange);

/**
 * @brief Calculate MS200 protocol CRC8.
 * @param data Input buffer.
 * @param len Number of bytes to include.
 * @return CRC8 value.
 */
static uint8_t crc8(const uint8_t *data, uint8_t len) {
    uint8_t crc = 0x00;
    for (uint8_t i = 0; i < len; i++) {
        crc = CRC_TABLE[(crc ^ data[i]) & 0xFF];
    }
    return crc;
}

/**
 * @brief Parse one MS200 point-cloud packet from the receive buffer.
 * @param buf Raw MS200 packet bytes.
 * @param out_pkg Parsed packet output.
 * @return true if CRC and packet fields are valid.
 */
static bool parse_package(const uint8_t *buf, ms200_package_t *out_pkg) {
    uint8_t buf_len = (buf[1] & 0x1F) * 3 + 11;
    uint8_t check_num = buf[buf_len - 1];
    if (crc8(buf, buf_len - 1) != check_num) {
        return false;
    }

    out_pkg->header = buf[0];
    out_pkg->count = buf[1] & 0x1F;
    out_pkg->speed = (buf[3] << 8) | buf[2];
    out_pkg->start_angle = (buf[5] << 8) | buf[4];
    out_pkg->end_angle = (buf[buf_len - 4] << 8) | buf[buf_len - 5];
    out_pkg->time_stamp = (buf[buf_len - 2] << 8) | buf[buf_len - 3];
    out_pkg->crc8 = check_num;

    for (uint8_t i = 0; i < out_pkg->count && i < MS200_POINT_PER_PACK; i++) {
        out_pkg->points[i].distance = (buf[3 * i + 7] << 8) | buf[3 * i + 6];
        out_pkg->points[i].intensity = buf[3 * i + 8];
    }
    return true;
}

/**
 * @brief Update the 360-degree scan cache with one parsed MS200 packet.
 * @param pkg Parsed MS200 point-cloud packet.
 */
static void update_scan_points(const ms200_package_t *pkg) {
    if (pkg->count < 2) {
        return;
    }

    bool new_revolution = false;

    if (last_start_angle_valid) {
        new_revolution = (pkg->start_angle < last_start_angle) &&
                         (last_start_angle - pkg->start_angle > 18000);
    }
    last_start_angle = pkg->start_angle;
    last_start_angle_valid = true;

    if (new_revolution && full_scan) {
        memcpy(send_points, scan_points, sizeof(scan_points));
        frame_ready = true;
    }

    uint16_t step_angle = 0;
    if (pkg->end_angle > pkg->start_angle) {
        step_angle = (pkg->end_angle - pkg->start_angle) / (pkg->count - 1);
    } else {
        step_angle = (36000 + pkg->end_angle - pkg->start_angle) / (pkg->count - 1);
    }

    for (uint8_t i = 0; i < pkg->count && i < MS200_POINT_PER_PACK; i++) {
        uint16_t angle = ((pkg->start_angle + i * step_angle + 50) / 100) % LIDAR_POINT_COUNT;
        scan_points[angle] = pkg->points[i];
        if (!point_valid[angle]) {
            point_valid[angle] = true;
            valid_point_count++;
            if (valid_point_count >= LIDAR_POINT_COUNT) {
                full_scan = true;
            }
        }
    }
}

/**
 * @brief Feed one byte into the MS200 receive state machine.
 * @param rx_data One byte read from the lidar UART.
 */
static void receive_byte(uint8_t rx_data) {
    static uint8_t rx_flag = 0;
    static uint8_t rx_buf_len = 0;
    static uint8_t rx_buf_index = 0;

    switch (rx_flag) {
        case 0:
            if (rx_data == MS200_HEAD_1) {
                rx_flag = 1;
                rx_protocol_buf[0] = MS200_HEAD_1;
            } else if (rx_data == MS200_DATA_START) {
                rx_flag = 5;
                rx_protocol_buf[0] = MS200_DATA_START;
            }
            break;

        case 1:
            rx_flag = (rx_data == MS200_HEAD_2) ? 2 : 0;
            if (rx_flag == 2) {
                rx_protocol_buf[1] = MS200_HEAD_2;
            }
            break;

        case 2:
            rx_protocol_buf[2] = rx_data;
            rx_flag = 3;
            break;

        case 3:
            rx_protocol_buf[3] = rx_data;
            rx_flag = 4;
            rx_buf_len = rx_data + 3;
            rx_buf_index = 0;
            break;

        case 4:
            rx_protocol_buf[rx_flag + rx_buf_index] = rx_data;
            rx_buf_index++;
            if (rx_buf_index >= rx_buf_len || rx_flag + rx_buf_index >= MS200_BUF_MAX) {
                rx_flag = 0;
                rx_buf_len = 0;
                rx_buf_index = 0;
                memset(rx_protocol_buf, 0, sizeof(rx_protocol_buf));
            }
            break;

        case 5:
            rx_protocol_buf[1] = rx_data;
            rx_flag = 6;
            rx_buf_index = 2;
            rx_buf_len = (rx_protocol_buf[1] & 0x1F) * 3 + 11;
            if (rx_buf_len > MS200_BUF_MAX) {
                rx_flag = 0;
                rx_buf_len = 0;
                rx_buf_index = 0;
            }
            break;

        case 6:
            rx_protocol_buf[rx_buf_index] = rx_data;
            rx_buf_index++;
            if (rx_buf_index >= rx_buf_len) {
                rx_flag = 0;
                rx_buf_len = 0;
                rx_buf_index = 0;
                if (parse_package(rx_protocol_buf, &ms200_pkg)) {
                    update_scan_points(&ms200_pkg);
                }
                memset(rx_protocol_buf, 0, sizeof(rx_protocol_buf));
            } else if (rx_buf_index >= MS200_BUF_MAX) {
                rx_flag = 0;
                rx_buf_len = 0;
                rx_buf_index = 0;
                memset(rx_protocol_buf, 0, sizeof(rx_protocol_buf));
            }
            break;

        default:
            rx_flag = 0;
            rx_buf_len = 0;
            rx_buf_index = 0;
            break;
    }
}

/**
 * @brief Return the distance value that should be emitted in the host frame.
 * @param degree Angle in degrees.
 * @return Distance in millimeters, or 0 when the angle is ignored.
 */
static uint16_t frame_distance(uint16_t degree) {
    const ms200_point_t *pt = &send_points[degree];
    if (pt->intensity < LIDAR_MIN_INTENSITY) {
        return LIDAR_INVALID_DISTANCE_MM;
    }

    uint16_t distance = pt->distance;
    if (is_ignored(degree, distance)) {
        return LIDAR_INVALID_DISTANCE_MM;
    }
    return distance;
}

void Lidar_Init(HardwareSerial &lidarSerial,
                int rxPin,
                int txPin,
                uint32_t baud,
                const lidar_ignore_sector_t *ignoreSectors,
                uint8_t ignoreSectorCount,
                bool ignoreEnabled) {
    static const lidar_ignore_sector_t default_ignore_sectors[] = LIDAR_DEFAULT_IGNORE_SECTORS;

    lidar_begin(lidarSerial, rxPin, txPin, baud);
    if (ignoreSectors == nullptr || ignoreSectorCount == 0) {
        ignoreSectors = default_ignore_sectors;
        ignoreSectorCount = LIDAR_IGNORE_SECTOR_COUNT;
    }
    set_ignore_sectors(ignoreSectors, ignoreSectorCount, ignoreEnabled);
}

/**
 * @brief Configure the hardware UART connected to the MS200 lidar.
 * @param lidarSerial Hardware serial port used by the lidar.
 * @param rxPin ESP32 RX pin connected to lidar TX.
 * @param txPin ESP32 TX pin connected to lidar RX.
 * @param baud Lidar UART baud rate.
 */
static void lidar_begin(HardwareSerial &lidarSerial, int rxPin, int txPin, uint32_t baud) {
    lidar_port = &lidarSerial;
    memset(scan_points, 0, sizeof(scan_points));
    memset(send_points, 0, sizeof(send_points));
    memset(point_valid, 0, sizeof(point_valid));
    memset(rx_protocol_buf, 0, sizeof(rx_protocol_buf));
    full_scan = false;
    frame_ready = false;
    last_start_angle = 0;
    last_start_angle_valid = false;
    valid_point_count = 0;
    ignore_sector_enabled = false;
    ignore_sector_count = 0;
    memset(ignore_sectors, 0, sizeof(ignore_sectors));
    lidar_port->begin(baud, SERIAL_8N1, rxPin, txPin);
}

void Lidar_Update(void) {
    while (lidar_port && lidar_port->available() > 0) {
        receive_byte((uint8_t)lidar_port->read());
    }
}

bool Lidar_Send(Stream &out, uint32_t intervalMs) {
    static uint32_t last_send_ms = 0;
    uint32_t now = millis();

    Lidar_Update();
    if (!frame_ready || now - last_send_ms < intervalMs) {
        return false;
    }

    write_frame(out);
    frame_ready = false;
    last_send_ms = now;
    return true;
}

bool Lidar_ReportValidAngleRanges(Stream &out, uint32_t intervalMs) {
    static uint32_t last_report_ms = 0;
    static bool waiting_reported = false;
    uint32_t now = millis();

    Lidar_Update();
    if (now - last_report_ms < intervalMs) {
        return false;
    }

    if (!frame_ready) {
        if (!waiting_reported) {
            out.print("waiting_scan\r\n");
            waiting_reported = true;
            last_report_ms = now;
            return true;
        }
        last_report_ms = now;
        return false;
    }

    bool first_range = true;
    int16_t first_invalid = -1;
    for (uint16_t i = 0; i < LIDAR_POINT_COUNT; i++) {
        if (!is_report_valid(i)) {
            first_invalid = (int16_t)i;
            break;
        }
    }

    if (first_invalid < 0) {
        print_valid_range(out, 0, LIDAR_POINT_COUNT - 1, &first_range);
    } else {
        bool in_range = false;
        uint16_t range_start = 0;
        uint16_t start = ((uint16_t)first_invalid + 1) % LIDAR_POINT_COUNT;

        for (uint16_t offset = 0; offset < LIDAR_POINT_COUNT; offset++) {
            uint16_t degree = (start + offset) % LIDAR_POINT_COUNT;
            if (is_report_valid(degree)) {
                if (!in_range) {
                    range_start = degree;
                    in_range = true;
                }
            } else if (in_range) {
                uint16_t range_end = (degree + LIDAR_POINT_COUNT - 1) % LIDAR_POINT_COUNT;
                print_valid_range(out, range_start, range_end, &first_range);
                in_range = false;
            }
        }

        if (in_range) {
            uint16_t range_end = (start + LIDAR_POINT_COUNT - 1) % LIDAR_POINT_COUNT;
            print_valid_range(out, range_start, range_end, &first_range);
        }
    }

    if (first_range) {
        out.print("none");
    }
    out.print("\r\n");

    frame_ready = false;
    waiting_reported = false;
    last_report_ms = now;
    return true;
}

/**
 * @brief Configure sectors that should be sent as invalid distance.
 * @param sectors Sector list, inclusive.
 * @param count Number of sectors in the list.
 * @param enabled Whether these sectors should be applied.
 */
static void set_ignore_sectors(const lidar_ignore_sector_t *sectors, uint8_t count, bool enabled) {
    ignore_sector_enabled = enabled;
    ignore_sector_count = 0;
    if (sectors == nullptr) {
        return;
    }

    uint8_t limit = count;
    if (limit > LIDAR_MAX_IGNORE_SECTORS) {
        limit = LIDAR_MAX_IGNORE_SECTORS;
    }

    for (uint8_t i = 0; i < limit; i++) {
        ignore_sectors[i].start_degree = sectors[i].start_degree % LIDAR_POINT_COUNT;
        ignore_sectors[i].end_degree = sectors[i].end_degree % LIDAR_POINT_COUNT;
        ignore_sectors[i].max_distance_mm = sectors[i].max_distance_mm;
        ignore_sector_count++;
    }
}

static bool is_degree_in_sector(uint16_t degree, uint16_t startDegree, uint16_t endDegree) {
    if (startDegree <= endDegree) {
        return degree >= startDegree && degree <= endDegree;
    }
    return degree >= startDegree || degree <= endDegree;
}

/**
 * @brief Check whether an angle is in the configured invalid sector.
 * @param degree Angle in degrees.
 * @param distanceMm Measured distance in millimeters.
 * @return true if this point should be sent as 0 mm.
 */
static bool is_ignored(uint16_t degree, uint16_t distanceMm) {
    if (!ignore_sector_enabled || distanceMm == 0) {
        return false;
    }

    degree %= LIDAR_POINT_COUNT;
    for (uint8_t i = 0; i < ignore_sector_count; i++) {
        if (is_degree_in_sector(degree,
                                ignore_sectors[i].start_degree,
                                ignore_sectors[i].end_degree) &&
            distanceMm <= ignore_sectors[i].max_distance_mm) {
            return true;
        }
    }
    return false;
}

static bool is_report_valid(uint16_t degree) {
    const ms200_point_t *pt = &send_points[degree % LIDAR_POINT_COUNT];
    if (pt->intensity < LIDAR_MIN_INTENSITY) {
        return false;
    }

    return pt->distance >= LIDAR_VALID_ANGLE_REPORT_MIN_DISTANCE_MM &&
           pt->distance <= LIDAR_VALID_ANGLE_REPORT_MAX_DISTANCE_MM;
}

static void print_valid_range(Stream &out, uint16_t startDegree, uint16_t endDegree, bool *firstRange) {
    if (!*firstRange) {
        out.print(' ');
    }
    out.print(startDegree);
    out.print('-');
    out.print(endDegree);
    *firstRange = false;
}

/**
 * @brief Write one complete binary lidar frame to the host stream.
 * @param out Output stream, usually Serial.
 * @return Number of bytes written.
 */
static size_t write_frame(Stream &out) {
    const uint16_t payload_len = LIDAR_FRAME_PAYLOAD_SIZE;
    const uint8_t type = LIDAR_FRAME_TYPE_SCAN;
    static uint8_t frame_buf[LIDAR_FRAME_SIZE];
    uint8_t checksum = type;
    uint16_t index = 0;

    frame_buf[index++] = (uint8_t)LIDAR_FRAME_HEAD_1;
    frame_buf[index++] = (uint8_t)LIDAR_FRAME_HEAD_2;
    frame_buf[index++] = type;
    frame_buf[index++] = (uint8_t)(payload_len & 0xFF);
    frame_buf[index++] = (uint8_t)(payload_len >> 8);
    checksum += (uint8_t)(payload_len & 0xFF);
    checksum += (uint8_t)(payload_len >> 8);

    for (uint16_t i = 0; i < LIDAR_POINT_COUNT; i++) {
        uint16_t distance = frame_distance(i);
        uint8_t low_byte = (uint8_t)(distance & 0xFF);
        uint8_t high_byte = (uint8_t)(distance >> 8);
        frame_buf[index++] = low_byte;
        frame_buf[index++] = high_byte;
        checksum += low_byte;
        checksum += high_byte;
    }

    frame_buf[index++] = checksum;
    frame_buf[index++] = (uint8_t)LIDAR_FRAME_TAIL_1;
    frame_buf[index++] = (uint8_t)LIDAR_FRAME_TAIL_2;
    return out.write(frame_buf, index);
}
