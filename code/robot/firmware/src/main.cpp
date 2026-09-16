#include <Arduino.h>
#include <string.h>

#include "lidar.h"
#include "chassis.h"
#include "imu.h"
#include "motor.h"

/**
 * @brief Host serial baud rate for the Raspberry Pi or PC.
 */
#if LIDAR_VALID_ANGLE_REPORT_MODE
#define HOST_BAUD 115200
#else
#define HOST_BAUD 921600
#endif

#define HOST_CMD_HEAD_1 0xAA
#define HOST_CMD_HEAD_2 0x66
#define HOST_CMD_PAYLOAD_LEN 12
#define HOST_CMD_FRAME_LEN 16
#define HOST_CMD_TIMEOUT_MS 500

#define TUNING_CMD_HEAD_1 0xAA
#define TUNING_CMD_HEAD_2 0x67
#define TUNING_CMD_PAYLOAD_LEN 13
#define TUNING_CMD_FRAME_LEN 17
#define TUNING_RESPONSE_HEAD_1 0xAA
#define TUNING_RESPONSE_HEAD_2 0x78
#define TUNING_RESPONSE_PAYLOAD_LEN 14

#define CHASSIS_TELEMETRY_HEAD_1 0xAA
#define CHASSIS_TELEMETRY_HEAD_2 0x77
#define CHASSIS_TELEMETRY_PAYLOAD_LEN 54
#define CHASSIS_TELEMETRY_INTERVAL_MS 50

#define IMU_TELEMETRY_HEAD_1 0xAA
#define IMU_TELEMETRY_HEAD_2 0x88
#define IMU_TELEMETRY_PAYLOAD_LEN 64
#define IMU_TELEMETRY_INTERVAL_MS 10

static uint8_t host_cmd_buf[HOST_CMD_FRAME_LEN];
static uint8_t host_cmd_index = 0;
static uint8_t tuning_cmd_buf[TUNING_CMD_FRAME_LEN];
static uint8_t tuning_cmd_index = 0;
static uint32_t last_cmd_ms = 0;
static uint32_t last_chassis_telemetry_ms = 0;
static uint32_t last_imu_telemetry_ms = 0;

static uint8_t payload_checksum(const uint8_t *payload, uint8_t len) {
    uint8_t sum = 0;
    for (uint8_t i = 0; i < len; i++) {
        sum += payload[i];
    }
    return sum;
}

static float read_float_le(const uint8_t *data) {
    uint32_t raw = (uint32_t)data[0]
                 | ((uint32_t)data[1] << 8)
                 | ((uint32_t)data[2] << 16)
                 | ((uint32_t)data[3] << 24);
    float value = 0.0f;
    memcpy(&value, &raw, sizeof(value));
    return value;
}

static void write_tuning_response(uint8_t command, bool success,
                                  const float values[3], Stream &out = Serial) {
    uint8_t payload[TUNING_RESPONSE_PAYLOAD_LEN];
    payload[0] = command;
    payload[1] = success ? 1 : 0;
    memcpy(&payload[2], values, 3 * sizeof(float));
    out.write((uint8_t)TUNING_RESPONSE_HEAD_1);
    out.write((uint8_t)TUNING_RESPONSE_HEAD_2);
    out.write((uint8_t)TUNING_RESPONSE_PAYLOAD_LEN);
    out.write(payload, TUNING_RESPONSE_PAYLOAD_LEN);
    out.write(payload_checksum(payload, TUNING_RESPONSE_PAYLOAD_LEN));
}

static void handle_tuning_command(const uint8_t *frame) {
    const uint8_t *payload = &frame[3];
    if (payload_checksum(payload, TUNING_CMD_PAYLOAD_LEN) != frame[16]) {
        return;
    }

    uint8_t command = payload[0];
    float values[3] = {
        read_float_le(&payload[1]),
        read_float_le(&payload[5]),
        read_float_le(&payload[9]),
    };
    bool success = true;
    switch (command) {
        case 0:
            Chassis_Stop();
            last_cmd_ms = 0;
            values[0] = values[1] = values[2] = 0.0f;
            break;
        case 1:
            Chassis_SetManualPwm((int)values[0], (int)values[1], (int)values[2]);
            last_cmd_ms = millis();
            break;
        case CHASSIS_TUNE_START_PWM_POSITIVE:
        case CHASSIS_TUNE_START_PWM_NEGATIVE:
        case CHASSIS_TUNE_RUNNING_PWM_POSITIVE:
        case CHASSIS_TUNE_RUNNING_PWM_NEGATIVE:
        case CHASSIS_TUNE_FEEDFORWARD_POSITIVE:
        case CHASSIS_TUNE_FEEDFORWARD_NEGATIVE:
        case CHASSIS_TUNE_PID_NORMAL:
        case CHASSIS_TUNE_PID_ROTATION:
            success = Chassis_SetTuningGroup(command, values);
            if (success) {
                Chassis_GetTuningGroup(command, values);
            }
            break;
        case 10:
            success = Chassis_SaveTuning();
            break;
        case 11:
            success = Chassis_LoadTuning();
            break;
        case 12:
            Chassis_ResetTuningDefaults();
            break;
        case 13:
            Chassis_ResetEncoders();
            break;
        case 14: {
            uint8_t group = (uint8_t)values[0];
            success = Chassis_GetTuningGroup(group, values);
            break;
        }
        case 15:
            values[0] = (float)ENCODER_PPR_WHEEL;
            values[1] = WHEEL_RADIUS;
            values[2] = 6.2831853f * WHEEL_RADIUS;
            break;
        default:
            success = false;
            break;
    }
    write_tuning_response(command, success, values);
}

static void handle_host_command(const uint8_t *frame) {
    const uint8_t *payload = &frame[3];
    uint8_t checksum = frame[15];
    if (payload_checksum(payload, HOST_CMD_PAYLOAD_LEN) != checksum) {
        return;
    }

    float vx = read_float_le(&payload[0]);
    float vy = read_float_le(&payload[4]);
    float w = read_float_le(&payload[8]);

    Chassis_SetVelocity(vx, vy, w);
    last_cmd_ms = millis();
}

static void receive_host_byte(uint8_t data) {
    switch (host_cmd_index) {
        case 0:
            if (data == HOST_CMD_HEAD_1) {
                host_cmd_buf[host_cmd_index++] = data;
            }
            break;

        case 1:
            if (data == HOST_CMD_HEAD_2) {
                host_cmd_buf[host_cmd_index++] = data;
            } else if (data == HOST_CMD_HEAD_1) {
                host_cmd_buf[0] = data;
                host_cmd_index = 1;
            } else {
                host_cmd_index = 0;
            }
            break;

        case 2:
            if (data == HOST_CMD_PAYLOAD_LEN) {
                host_cmd_buf[host_cmd_index++] = data;
            } else if (data == HOST_CMD_HEAD_1) {
                host_cmd_buf[0] = data;
                host_cmd_index = 1;
            } else {
                host_cmd_index = 0;
            }
            break;

        default:
            host_cmd_buf[host_cmd_index++] = data;
            if (host_cmd_index >= HOST_CMD_FRAME_LEN) {
                handle_host_command(host_cmd_buf);
                host_cmd_index = 0;
            }
            break;
    }
}

static void receive_tuning_byte(uint8_t data) {
    switch (tuning_cmd_index) {
        case 0:
            if (data == TUNING_CMD_HEAD_1) {
                tuning_cmd_buf[tuning_cmd_index++] = data;
            }
            break;
        case 1:
            if (data == TUNING_CMD_HEAD_2) {
                tuning_cmd_buf[tuning_cmd_index++] = data;
            } else if (data == TUNING_CMD_HEAD_1) {
                tuning_cmd_buf[0] = data;
                tuning_cmd_index = 1;
            } else {
                tuning_cmd_index = 0;
            }
            break;
        case 2:
            if (data == TUNING_CMD_PAYLOAD_LEN) {
                tuning_cmd_buf[tuning_cmd_index++] = data;
            } else if (data == TUNING_CMD_HEAD_1) {
                tuning_cmd_buf[0] = data;
                tuning_cmd_index = 1;
            } else {
                tuning_cmd_index = 0;
            }
            break;
        default:
            tuning_cmd_buf[tuning_cmd_index++] = data;
            if (tuning_cmd_index >= TUNING_CMD_FRAME_LEN) {
                handle_tuning_command(tuning_cmd_buf);
                tuning_cmd_index = 0;
            }
            break;
    }
}

static void HostCommand_Update(void) {
    while (Serial.available() > 0) {
        uint8_t data = (uint8_t)Serial.read();
        receive_host_byte(data);
        receive_tuning_byte(data);
    }

    if (last_cmd_ms != 0 && millis() - last_cmd_ms > HOST_CMD_TIMEOUT_MS) {
        Chassis_Stop();
        last_cmd_ms = 0;
    }
}

static void write_chassis_telemetry_frame(Stream &out = Serial) {
    float target[3];
    float measured[3];
    int pwm[3];
    long encoder[3];
    float distance_m[3];
    uint8_t payload[CHASSIS_TELEMETRY_PAYLOAD_LEN];
    uint8_t checksum = 0;
    uint8_t index = 0;

    Chassis_GetDebug(target, measured, pwm, encoder, distance_m);

    for (uint8_t i = 0; i < 3; i++) {
        memcpy(&payload[index], &target[i], sizeof(float));
        index += sizeof(float);
    }
    for (uint8_t i = 0; i < 3; i++) {
        memcpy(&payload[index], &measured[i], sizeof(float));
        index += sizeof(float);
    }
    for (uint8_t i = 0; i < 3; i++) {
        int16_t value = (int16_t)pwm[i];
        payload[index++] = (uint8_t)(value & 0xFF);
        payload[index++] = (uint8_t)((uint16_t)value >> 8);
    }
    for (uint8_t i = 0; i < 3; i++) {
        int32_t value = (int32_t)encoder[i];
        payload[index++] = (uint8_t)(value & 0xFF);
        payload[index++] = (uint8_t)((uint32_t)value >> 8);
        payload[index++] = (uint8_t)((uint32_t)value >> 16);
        payload[index++] = (uint8_t)((uint32_t)value >> 24);
    }
    for (uint8_t i = 0; i < 3; i++) {
        memcpy(&payload[index], &distance_m[i], sizeof(float));
        index += sizeof(float);
    }

    for (uint8_t i = 0; i < CHASSIS_TELEMETRY_PAYLOAD_LEN; i++) {
        checksum += payload[i];
    }

    out.write((uint8_t)CHASSIS_TELEMETRY_HEAD_1);
    out.write((uint8_t)CHASSIS_TELEMETRY_HEAD_2);
    out.write((uint8_t)CHASSIS_TELEMETRY_PAYLOAD_LEN);
    out.write(payload, CHASSIS_TELEMETRY_PAYLOAD_LEN);
    out.write(checksum);
}

static void ChassisTelemetry_Update(void) {
    uint32_t now = millis();
    if (now - last_chassis_telemetry_ms < CHASSIS_TELEMETRY_INTERVAL_MS) {
        return;
    }
    last_chassis_telemetry_ms = now;
    write_chassis_telemetry_frame();
}

static void write_imu_telemetry_frame(Stream &out = Serial) {
    imu_sample_t sample = {};
    bool sample_valid = Imu_GetLatest(&sample);
    uint8_t status = (Imu_IsReady() ? 0x01 : 0x00) |
                     (sample_valid ? 0x02 : 0x00) |
                     (Imu_IsGyroCalibrated() ? 0x04 : 0x00) |
                     (Imu_IsAttitudeValid() ? 0x08 : 0x00);

    uint8_t payload[IMU_TELEMETRY_PAYLOAD_LEN];
    uint8_t checksum = 0;
    uint8_t index = 0;

    payload[index++] = status;
    payload[index++] = Imu_GetWhoAmI();
    payload[index++] = (uint8_t)(sample.timestamp_us & 0xFF);
    payload[index++] = (uint8_t)(sample.timestamp_us >> 8);
    payload[index++] = (uint8_t)(sample.timestamp_us >> 16);
    payload[index++] = (uint8_t)(sample.timestamp_us >> 24);
    payload[index++] = (uint8_t)(sample.sequence & 0xFF);
    payload[index++] = (uint8_t)(sample.sequence >> 8);
    for (uint8_t i = 0; i < 3; i++) {
        memcpy(&payload[index], &sample.accel_mps2[i], sizeof(float));
        index += sizeof(float);
    }
    for (uint8_t i = 0; i < 3; i++) {
        memcpy(&payload[index], &sample.gyro_rad_s[i], sizeof(float));
        index += sizeof(float);
    }
    memcpy(&payload[index], &sample.temperature_c, sizeof(float));
    index += sizeof(float);
    for (uint8_t i = 0; i < 4; i++) {
        memcpy(&payload[index], &sample.orientation_xyzw[i], sizeof(float));
        index += sizeof(float);
    }
    for (uint8_t i = 0; i < 3; i++) {
        memcpy(&payload[index], &sample.gyro_bias_rad_s[i], sizeof(float));
        index += sizeof(float);
    }

    for (uint8_t i = 0; i < IMU_TELEMETRY_PAYLOAD_LEN; i++) {
        checksum += payload[i];
    }

    out.write((uint8_t)IMU_TELEMETRY_HEAD_1);
    out.write((uint8_t)IMU_TELEMETRY_HEAD_2);
    out.write((uint8_t)IMU_TELEMETRY_PAYLOAD_LEN);
    out.write(payload, IMU_TELEMETRY_PAYLOAD_LEN);
    out.write(checksum);
}

static void ImuTelemetry_Update(void) {
    Imu_Update();
    uint32_t now = millis();
    if (now - last_imu_telemetry_ms < IMU_TELEMETRY_INTERVAL_MS) {
        return;
    }
    last_imu_telemetry_ms = now;
    write_imu_telemetry_frame();
}

void setup() {
    Serial.setTxBufferSize(2048);
    Serial.begin(HOST_BAUD);
    Chassis_LoadTuning();
    Chassis_Stop();
    Imu_Init();

    // Defaults: Serial1, RX=18, TX=17, lidar baud=230400.
    // Mechanical blind sector is ignored in lidar.h.
    Lidar_Init();

#if LIDAR_VALID_ANGLE_REPORT_MODE
    Serial.println("LIDAR_ANGLE_REPORT_MODE");
#endif
}

void loop() {
#if LIDAR_VALID_ANGLE_REPORT_MODE
    Lidar_ReportValidAngleRanges();
#else
    HostCommand_Update();
    Chassis_Update();
    ChassisTelemetry_Update();
    ImuTelemetry_Update();
    Lidar_Send();
#endif
}
