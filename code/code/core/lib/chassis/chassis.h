#ifndef CHASSIS_H
#define CHASSIS_H

#include <Arduino.h>

#define WHEEL_BASE_RADIUS 0.138f
#define MAX_WHEEL_SPEED 0.65f
#define START_EFFECTIVE_PWM 80
#define MIN_RUNNING_PWM 50
#define ROTATION_MIN_RUNNING_PWM 70
#define CHASSIS_STARTUP_SPEED_THRESHOLD 0.02f
#define CHASSIS_TRANSLATION_START_MIN_TARGET 0.04f
#define CHASSIS_TRANSLATION_START_EFFECTIVE_PWM 125
#define CHASSIS_TRANSLATION_STARTUP_PULSE_MS 300U
#define CHASSIS_TRANSLATION_START_CONFIRM_MS 80U

#define CHASSIS_PID_ENABLED 1
#define CHASSIS_PID_INTERVAL_MS 20
#define CHASSIS_PID_KP 900.0f
#define CHASSIS_PID_KI 80.0f
#define CHASSIS_PID_KD 0.0f
// Pure rotation uses the dedicated high-gain PID only below about 0.5 rad/s
// chassis yaw rate (0.07 / WHEEL_BASE_RADIUS). Medium rotation remains on the
// normal PID so ultra-low-speed friction compensation cannot cause overshoot.
#define CHASSIS_LOW_SPEED_TARGET 0.07f
#define CHASSIS_LOW_SPEED_PID_KP 300.0f
#define CHASSIS_LOW_SPEED_PID_KI 40.0f
#define CHASSIS_PID_INTEGRAL_LIMIT 2.0f
#define CHASSIS_TARGET_DEADBAND 0.001f

// A wheel is allowed the full translation startup pulse before feedback
// monitoring begins.  A persistent saturated/under-speed wheel then latches a
// whole-chassis stop.  The latch can only clear after fresh zero commands keep
// arriving continuously; a stale link must never clear it by itself.
#define CHASSIS_FEEDBACK_TARGET_MIN 0.04f
#define CHASSIS_FEEDBACK_PWM_SATURATION 245
#define CHASSIS_FEEDBACK_UNDERSPEED_RATIO 0.50f
#define CHASSIS_FEEDBACK_STARTUP_GRACE_MS 300U
#define CHASSIS_FEEDBACK_FAULT_CONFIRM_MS 300U
#define CHASSIS_FAULT_CLEAR_ZERO_MS 500U
#define CHASSIS_FAULT_ZERO_MAX_GAP_MS 100U

#define PURE_ROTATION_V_THRESHOLD 0.02f
#define SQRT3_OVER_2 0.86602540f

typedef enum {
    CHASSIS_TUNE_START_PWM_POSITIVE = 2,
    CHASSIS_TUNE_START_PWM_NEGATIVE = 3,
    CHASSIS_TUNE_RUNNING_PWM_POSITIVE = 4,
    CHASSIS_TUNE_RUNNING_PWM_NEGATIVE = 5,
    CHASSIS_TUNE_FEEDFORWARD_POSITIVE = 6,
    CHASSIS_TUNE_FEEDFORWARD_NEGATIVE = 7,
    CHASSIS_TUNE_PID_NORMAL = 8,
    CHASSIS_TUNE_PID_ROTATION = 9,
} chassis_tuning_group_t;

void Chassis_SetVelocity(float vx, float vy, float w);
void Chassis_SetManualPwm(int pwm1, int pwm2, int pwm3);
void Chassis_Update(void);
void Chassis_Stop(void);
void Chassis_GetDebug(float target[3], float measured[3], int pwm[3],
                      long encoder[3], float distance_m[3]);
bool Chassis_SetTuningGroup(uint8_t group, const float values[3]);
bool Chassis_GetTuningGroup(uint8_t group, float values[3]);
bool Chassis_SaveTuning(void);
bool Chassis_LoadTuning(void);
void Chassis_ResetTuningDefaults(void);
void Chassis_ResetEncoders(void);
bool Chassis_HasFeedbackFault(void);
// Returns the latched logical wheel number (1..3), or -1 when clear.
int8_t Chassis_GetFeedbackFaultWheel(void);

#endif // CHASSIS_H
