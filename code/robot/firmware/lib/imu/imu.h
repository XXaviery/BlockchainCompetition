#ifndef IMU_H
#define IMU_H

#include <Arduino.h>

#define IMU_I2C_SDA_PIN 40
#define IMU_I2C_SCL_PIN 39
#define IMU_INT_PIN 41
#define IMU_I2C_ADDRESS 0x68
#define IMU_I2C_FREQUENCY_HZ 400000
#define IMU_SAMPLE_INTERVAL_US 2500
#define IMU_GYRO_CALIBRATION_SAMPLES 800
#define IMU_MAHONY_KP 1.0f
#define IMU_MAHONY_KI 0.05f

typedef struct {
    uint32_t timestamp_us;
    uint16_t sequence;
    float accel_mps2[3];
    float gyro_rad_s[3];
    float temperature_c;
    float orientation_xyzw[4];
    float gyro_bias_rad_s[3];
} imu_sample_t;

bool Imu_Init(void);
void Imu_Update(void);
bool Imu_GetLatest(imu_sample_t *sample);
bool Imu_IsReady(void);
bool Imu_IsGyroCalibrated(void);
bool Imu_IsAttitudeValid(void);
uint8_t Imu_GetWhoAmI(void);

#endif // IMU_H
