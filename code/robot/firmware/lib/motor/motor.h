// motor.h
#ifndef MOTOR_H
#define MOTOR_H

#ifdef __cplusplus
extern "C" {
#endif

#include <stdint.h>

// 电机引脚
#define M1_A  5
#define M1_B  4
#define M2_A  16
#define M2_B  15
#define M3_A  9
#define M3_B  10

// 编码器引脚
#define H1_A  6
#define H1_B  7
#define H2_A  47
#define H2_B  48
#define H3_A  11
#define H3_B  12

// PWM通道
#define CH_M1_A  0
#define CH_M1_B  1
#define CH_M2_A  2
#define CH_M2_B  3
#define CH_M3_A  4
#define CH_M3_B  5

// PWM参数
#define PWM_FREQ  1000
#define PWM_BITS  8
#define PWM_MAX   255

// Encoder parameters for later wheel-speed/PID calculation.
#define ENCODER_PPR_MOTOR  11
#define GEAR_RATIO         30
#define WHEEL_RADIUS       0.031f
#define ENCODER_PPR_WHEEL  (ENCODER_PPR_MOTOR * GEAR_RATIO * 2)

// 电机ID
typedef enum {
    MOTOR_1 = 0,
    MOTOR_2 = 1,
    MOTOR_3 = 2,
    MOTOR_ALL = 3
} motor_id_t;

void Motor(motor_id_t id, int speed);
void Motor_All(int s1, int s2, int s3);
void Motor_Stop(motor_id_t id);
long Motor_Get_Encoder(motor_id_t id);
void Motor_Reset_Encoder(motor_id_t id);

#ifdef __cplusplus
}
#endif

#endif // MOTOR_H
    