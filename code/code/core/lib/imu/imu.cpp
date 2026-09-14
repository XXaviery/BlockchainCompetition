#include "imu.h"

#include <Wire.h>
#include <math.h>

#define ICM42670P_WHO_AM_I_REG 0x75
#define ICM42670P_WHO_AM_I_VALUE 0x67
#define ICM42670P_SIGNAL_PATH_RESET_REG 0x02
#define ICM42670P_TEMP_DATA1_REG 0x09
#define ICM42670P_PWR_MGMT0_REG 0x1F
#define ICM42670P_GYRO_CONFIG0_REG 0x20
#define ICM42670P_ACCEL_CONFIG0_REG 0x21

#define ICM42670P_SOFT_RESET 0x10
#define ICM42670P_ACCEL_FS_4G_ODR_400HZ 0x47
#define ICM42670P_GYRO_FS_2000DPS_ODR_400HZ 0x07
#define ICM42670P_ACCEL_GYRO_LOW_NOISE 0x0F

#define STANDARD_GRAVITY_MPS2 9.80665f
#define DEG_TO_RAD_F 0.01745329251994329577f
#define IMU_ACCEL_SCALE_MPS2 (4.0f * STANDARD_GRAVITY_MPS2 / 32768.0f)
#define IMU_GYRO_SCALE_RAD_S (2000.0f * DEG_TO_RAD_F / 32768.0f)
#define IMU_CALIBRATION_ACCEL_MIN_MPS2 (0.85f * STANDARD_GRAVITY_MPS2)
#define IMU_CALIBRATION_ACCEL_MAX_MPS2 (1.15f * STANDARD_GRAVITY_MPS2)
#define IMU_CALIBRATION_GYRO_MAX_RAD_S 0.15f
#define IMU_MAHONY_ACCEL_MIN_MPS2 (0.80f * STANDARD_GRAVITY_MPS2)
#define IMU_MAHONY_ACCEL_MAX_MPS2 (1.20f * STANDARD_GRAVITY_MPS2)

static volatile bool data_ready_interrupt = false;
static bool imu_ready = false;
static uint8_t who_am_i = 0;
static uint32_t last_sample_us = 0;
static uint32_t last_attitude_update_us = 0;
static imu_sample_t latest_sample = {};
static bool gyro_calibrated = false;
static bool attitude_valid = false;
static uint16_t calibration_sample_count = 0;
static float calibration_gyro_sum[3] = {0.0f, 0.0f, 0.0f};
static float calibration_accel_sum[3] = {0.0f, 0.0f, 0.0f};
static float gyro_bias_rad_s[3] = {0.0f, 0.0f, 0.0f};
static float mahony_integral[3] = {0.0f, 0.0f, 0.0f};
static float quaternion_wxyz[4] = {1.0f, 0.0f, 0.0f, 0.0f};

static void IRAM_ATTR imu_data_ready_isr(void) {
    data_ready_interrupt = true;
}

static bool write_register(uint8_t reg, uint8_t value) {
    Wire.beginTransmission(IMU_I2C_ADDRESS);
    Wire.write(reg);
    Wire.write(value);
    return Wire.endTransmission(true) == 0;
}

static bool read_registers(uint8_t reg, uint8_t *data, size_t length) {
    Wire.beginTransmission(IMU_I2C_ADDRESS);
    Wire.write(reg);
    if (Wire.endTransmission(false) != 0) {
        return false;
    }

    size_t received = Wire.requestFrom((uint8_t)IMU_I2C_ADDRESS, length, true);
    if (received != length) {
        while (Wire.available() > 0) {
            Wire.read();
        }
        return false;
    }

    for (size_t i = 0; i < length; i++) {
        data[i] = (uint8_t)Wire.read();
    }
    return true;
}

static int16_t read_i16_be(const uint8_t *data) {
    return (int16_t)(((uint16_t)data[0] << 8) | data[1]);
}

static void reset_gyro_calibration(void) {
    calibration_sample_count = 0;
    for (uint8_t i = 0; i < 3; i++) {
        calibration_gyro_sum[i] = 0.0f;
        calibration_accel_sum[i] = 0.0f;
    }
}

static void normalize_quaternion(void) {
    float norm = sqrtf(quaternion_wxyz[0] * quaternion_wxyz[0] +
                       quaternion_wxyz[1] * quaternion_wxyz[1] +
                       quaternion_wxyz[2] * quaternion_wxyz[2] +
                       quaternion_wxyz[3] * quaternion_wxyz[3]);
    if (norm <= 0.0f) {
        quaternion_wxyz[0] = 1.0f;
        quaternion_wxyz[1] = 0.0f;
        quaternion_wxyz[2] = 0.0f;
        quaternion_wxyz[3] = 0.0f;
        return;
    }
    float reciprocal = 1.0f / norm;
    for (uint8_t i = 0; i < 4; i++) {
        quaternion_wxyz[i] *= reciprocal;
    }
}

static void initialize_attitude_from_accel(const float accel[3]) {
    float roll = atan2f(accel[1], accel[2]);
    float pitch = atan2f(-accel[0], sqrtf(accel[1] * accel[1] + accel[2] * accel[2]));
    float half_roll = 0.5f * roll;
    float half_pitch = 0.5f * pitch;
    float cr = cosf(half_roll);
    float sr = sinf(half_roll);
    float cp = cosf(half_pitch);
    float sp = sinf(half_pitch);

    quaternion_wxyz[0] = cr * cp;
    quaternion_wxyz[1] = sr * cp;
    quaternion_wxyz[2] = cr * sp;
    quaternion_wxyz[3] = -sr * sp;
    normalize_quaternion();
    attitude_valid = true;
}

static void update_gyro_calibration(const float accel[3], const float gyro[3], uint32_t now) {
    float accel_norm = sqrtf(accel[0] * accel[0] + accel[1] * accel[1] + accel[2] * accel[2]);
    float gyro_norm = sqrtf(gyro[0] * gyro[0] + gyro[1] * gyro[1] + gyro[2] * gyro[2]);
    bool stationary = accel_norm >= IMU_CALIBRATION_ACCEL_MIN_MPS2 &&
                      accel_norm <= IMU_CALIBRATION_ACCEL_MAX_MPS2 &&
                      gyro_norm <= IMU_CALIBRATION_GYRO_MAX_RAD_S;
    if (!stationary) {
        reset_gyro_calibration();
        return;
    }

    for (uint8_t i = 0; i < 3; i++) {
        calibration_gyro_sum[i] += gyro[i];
        calibration_accel_sum[i] += accel[i];
    }
    calibration_sample_count++;
    if (calibration_sample_count < IMU_GYRO_CALIBRATION_SAMPLES) {
        return;
    }

    float mean_accel[3];
    for (uint8_t i = 0; i < 3; i++) {
        gyro_bias_rad_s[i] = calibration_gyro_sum[i] / calibration_sample_count;
        mean_accel[i] = calibration_accel_sum[i] / calibration_sample_count;
        mahony_integral[i] = 0.0f;
    }
    gyro_calibrated = true;
    initialize_attitude_from_accel(mean_accel);
    last_attitude_update_us = now;
}

static void mahony_update(const float accel[3], const float gyro[3], float dt) {
    if (!attitude_valid || dt <= 0.0f || dt > 0.1f) {
        return;
    }

    float corrected_gyro[3] = {gyro[0], gyro[1], gyro[2]};
    float accel_norm = sqrtf(accel[0] * accel[0] + accel[1] * accel[1] + accel[2] * accel[2]);
    if (accel_norm >= IMU_MAHONY_ACCEL_MIN_MPS2 && accel_norm <= IMU_MAHONY_ACCEL_MAX_MPS2) {
        float ax = accel[0] / accel_norm;
        float ay = accel[1] / accel_norm;
        float az = accel[2] / accel_norm;
        float qw = quaternion_wxyz[0];
        float qx = quaternion_wxyz[1];
        float qy = quaternion_wxyz[2];
        float qz = quaternion_wxyz[3];

        float gravity_x = 2.0f * (qx * qz - qw * qy);
        float gravity_y = 2.0f * (qw * qx + qy * qz);
        float gravity_z = qw * qw - qx * qx - qy * qy + qz * qz;
        float error[3] = {
            ay * gravity_z - az * gravity_y,
            az * gravity_x - ax * gravity_z,
            ax * gravity_y - ay * gravity_x,
        };

        for (uint8_t i = 0; i < 3; i++) {
            mahony_integral[i] += IMU_MAHONY_KI * error[i] * dt;
            corrected_gyro[i] += IMU_MAHONY_KP * error[i] + mahony_integral[i];
        }
    }

    float qw = quaternion_wxyz[0];
    float qx = quaternion_wxyz[1];
    float qy = quaternion_wxyz[2];
    float qz = quaternion_wxyz[3];
    float half_dt = 0.5f * dt;
    quaternion_wxyz[0] += (-qx * corrected_gyro[0] - qy * corrected_gyro[1] - qz * corrected_gyro[2]) * half_dt;
    quaternion_wxyz[1] += ( qw * corrected_gyro[0] + qy * corrected_gyro[2] - qz * corrected_gyro[1]) * half_dt;
    quaternion_wxyz[2] += ( qw * corrected_gyro[1] - qx * corrected_gyro[2] + qz * corrected_gyro[0]) * half_dt;
    quaternion_wxyz[3] += ( qw * corrected_gyro[2] + qx * corrected_gyro[1] - qy * corrected_gyro[0]) * half_dt;
    normalize_quaternion();
}

bool Imu_Init(void) {
    imu_ready = false;
    who_am_i = 0;
    data_ready_interrupt = false;
    gyro_calibrated = false;
    attitude_valid = false;
    reset_gyro_calibration();
    last_attitude_update_us = 0;
    quaternion_wxyz[0] = 1.0f;
    quaternion_wxyz[1] = 0.0f;
    quaternion_wxyz[2] = 0.0f;
    quaternion_wxyz[3] = 0.0f;
    latest_sample = {};
    for (uint8_t i = 0; i < 3; i++) {
        gyro_bias_rad_s[i] = 0.0f;
        mahony_integral[i] = 0.0f;
    }

    if (!Wire.begin(IMU_I2C_SDA_PIN, IMU_I2C_SCL_PIN, IMU_I2C_FREQUENCY_HZ)) {
        return false;
    }
    Wire.setTimeOut(10);

    if (!read_registers(ICM42670P_WHO_AM_I_REG, &who_am_i, 1) ||
        who_am_i != ICM42670P_WHO_AM_I_VALUE) {
        return false;
    }

    if (!write_register(ICM42670P_SIGNAL_PATH_RESET_REG, ICM42670P_SOFT_RESET)) {
        return false;
    }
    delay(2);

    if (!read_registers(ICM42670P_WHO_AM_I_REG, &who_am_i, 1) ||
        who_am_i != ICM42670P_WHO_AM_I_VALUE) {
        return false;
    }

    if (!write_register(ICM42670P_ACCEL_CONFIG0_REG, ICM42670P_ACCEL_FS_4G_ODR_400HZ) ||
        !write_register(ICM42670P_GYRO_CONFIG0_REG, ICM42670P_GYRO_FS_2000DPS_ODR_400HZ) ||
        !write_register(ICM42670P_PWR_MGMT0_REG, ICM42670P_ACCEL_GYRO_LOW_NOISE)) {
        return false;
    }
    delay(50);

    pinMode(IMU_INT_PIN, INPUT);
    attachInterrupt(digitalPinToInterrupt(IMU_INT_PIN), imu_data_ready_isr, CHANGE);

    last_sample_us = micros() - IMU_SAMPLE_INTERVAL_US;
    imu_ready = true;
    return true;
}

void Imu_Update(void) {
    if (!imu_ready) {
        return;
    }

    uint32_t now = micros();
    bool interrupt_seen = data_ready_interrupt;
    if (!interrupt_seen && now - last_sample_us < IMU_SAMPLE_INTERVAL_US) {
        return;
    }
    data_ready_interrupt = false;
    if (now - last_sample_us < IMU_SAMPLE_INTERVAL_US) {
        return;
    }

    uint8_t raw[14];
    if (!read_registers(ICM42670P_TEMP_DATA1_REG, raw, sizeof(raw))) {
        return;
    }

    int16_t temperature_raw = read_i16_be(&raw[0]);
    int16_t accel_raw[3] = {
        read_i16_be(&raw[2]),
        read_i16_be(&raw[4]),
        read_i16_be(&raw[6]),
    };
    int16_t gyro_raw[3] = {
        read_i16_be(&raw[8]),
        read_i16_be(&raw[10]),
        read_i16_be(&raw[12]),
    };

    float accel_mps2[3];
    float raw_gyro_rad_s[3];
    for (uint8_t i = 0; i < 3; i++) {
        accel_mps2[i] = accel_raw[i] * IMU_ACCEL_SCALE_MPS2;
        raw_gyro_rad_s[i] = gyro_raw[i] * IMU_GYRO_SCALE_RAD_S;
    }

    if (!gyro_calibrated) {
        update_gyro_calibration(accel_mps2, raw_gyro_rad_s, now);
    }

    float corrected_gyro_rad_s[3];
    for (uint8_t i = 0; i < 3; i++) {
        corrected_gyro_rad_s[i] = raw_gyro_rad_s[i] - gyro_bias_rad_s[i];
    }
    if (gyro_calibrated && attitude_valid) {
        float dt = last_attitude_update_us == 0
                 ? 0.0f
                 : (now - last_attitude_update_us) / 1000000.0f;
        mahony_update(accel_mps2, corrected_gyro_rad_s, dt);
        last_attitude_update_us = now;
    }

    latest_sample.timestamp_us = now;
    latest_sample.sequence++;
    for (uint8_t i = 0; i < 3; i++) {
        latest_sample.accel_mps2[i] = accel_mps2[i];
        latest_sample.gyro_rad_s[i] = corrected_gyro_rad_s[i];
        latest_sample.gyro_bias_rad_s[i] = gyro_bias_rad_s[i];
    }
    latest_sample.temperature_c = temperature_raw / 128.0f + 25.0f;
    latest_sample.orientation_xyzw[0] = quaternion_wxyz[1];
    latest_sample.orientation_xyzw[1] = quaternion_wxyz[2];
    latest_sample.orientation_xyzw[2] = quaternion_wxyz[3];
    latest_sample.orientation_xyzw[3] = quaternion_wxyz[0];
    last_sample_us = now;
}

bool Imu_GetLatest(imu_sample_t *sample) {
    if (!imu_ready || sample == nullptr || latest_sample.sequence == 0) {
        return false;
    }
    *sample = latest_sample;
    return true;
}

bool Imu_IsReady(void) {
    return imu_ready;
}

bool Imu_IsGyroCalibrated(void) {
    return gyro_calibrated;
}

bool Imu_IsAttitudeValid(void) {
    return attitude_valid;
}

uint8_t Imu_GetWhoAmI(void) {
    return who_am_i;
}
