// motor.cpp
#include "motor.h"
#include <Arduino.h>

static volatile long enc[3] = {0, 0, 0};
static bool _initialized = false;

void IRAM_ATTR enc1ISR() {
    if (digitalRead(H1_A) == digitalRead(H1_B)) enc[0]--;
    else enc[0]++;
}
void IRAM_ATTR enc2ISR() {
    if (digitalRead(H2_A) == digitalRead(H2_B)) enc[1]--;
    else enc[1]++;
}
void IRAM_ATTR enc3ISR() {
    if (digitalRead(H3_A) == digitalRead(H3_B)) enc[2]++;
    else enc[2]--;
}

static void _init(void) {
    // PWM初始化
    ledcSetup(CH_M1_A, PWM_FREQ, PWM_BITS);
    ledcSetup(CH_M1_B, PWM_FREQ, PWM_BITS);
    ledcAttachPin(M1_A, CH_M1_A);
    ledcAttachPin(M1_B, CH_M1_B);

    ledcSetup(CH_M2_A, PWM_FREQ, PWM_BITS);
    ledcSetup(CH_M2_B, PWM_FREQ, PWM_BITS);
    ledcAttachPin(M2_A, CH_M2_A);
    ledcAttachPin(M2_B, CH_M2_B);

    ledcSetup(CH_M3_A, PWM_FREQ, PWM_BITS);
    ledcSetup(CH_M3_B, PWM_FREQ, PWM_BITS);
    ledcAttachPin(M3_A, CH_M3_A);
    ledcAttachPin(M3_B, CH_M3_B);

    // 编码器初始化
    pinMode(H1_A, INPUT_PULLUP); pinMode(H1_B, INPUT_PULLUP);
    pinMode(H2_A, INPUT_PULLUP); pinMode(H2_B, INPUT_PULLUP);
    pinMode(H3_A, INPUT_PULLUP); pinMode(H3_B, INPUT_PULLUP);
    attachInterrupt(digitalPinToInterrupt(H1_A), enc1ISR, CHANGE);
    attachInterrupt(digitalPinToInterrupt(H2_A), enc2ISR, CHANGE);
    attachInterrupt(digitalPinToInterrupt(H3_A), enc3ISR, CHANGE);

    _initialized = true;
}

static void _set_speed(int ch_a, int ch_b, int speed, bool reverse) {
    speed = constrain(speed, -PWM_MAX, PWM_MAX);
    if (reverse) speed = -speed;
    if (speed > 0) {
        ledcWrite(ch_a, 0);
        ledcWrite(ch_b, speed);
    } else if (speed < 0) {
        ledcWrite(ch_a, -speed);
        ledcWrite(ch_b, 0);
    } else {
        ledcWrite(ch_a, 0);
        ledcWrite(ch_b, 0);
    }
}

// 统一入口：自动初始化，然后设置速度
void Motor(motor_id_t id, int speed) {
    if (!_initialized) _init();
    switch (id) {
        case MOTOR_1: _set_speed(CH_M1_A, CH_M1_B, speed, true);  break;
        case MOTOR_2: _set_speed(CH_M2_A, CH_M2_B, speed, true);  break;
        case MOTOR_3: _set_speed(CH_M3_A, CH_M3_B, speed, false); break;
        case MOTOR_ALL:
            _set_speed(CH_M1_A, CH_M1_B, speed, true);
            _set_speed(CH_M2_A, CH_M2_B, speed, true);
            _set_speed(CH_M3_A, CH_M3_B, speed, false);
            break;
    }
}

void Motor_All(int s1, int s2, int s3) {
    if (!_initialized) _init();
    _set_speed(CH_M1_A, CH_M1_B, s1, true);
    _set_speed(CH_M2_A, CH_M2_B, s2, true);
    _set_speed(CH_M3_A, CH_M3_B, s3, false);
}

void Motor_Stop(motor_id_t id) {
    Motor(id, 0);
}

long Motor_Get_Encoder(motor_id_t id) {
    if (id == MOTOR_1) return enc[0];
    if (id == MOTOR_2) return enc[1];
    if (id == MOTOR_3) return enc[2];
    return 0;
}

void Motor_Reset_Encoder(motor_id_t id) {
    if (id == MOTOR_ALL) { enc[0] = 0; enc[1] = 0; enc[2] = 0; }
    else enc[id] = 0;
}