#include "chassis.h"

#include <math.h>
#include <Preferences.h>

#include "motor.h"

#define WHEEL_CIRCUMFERENCE_M (6.2831853f * WHEEL_RADIUS)

static float target_speed[3] = {0.0f, 0.0f, 0.0f};
static float command_vx = 0.0f;
static float command_vy = 0.0f;
static float command_w = 0.0f;
static float integral_error[3] = {0.0f, 0.0f, 0.0f};
static float previous_error[3] = {0.0f, 0.0f, 0.0f};
static float measured_speed_cache[3] = {0.0f, 0.0f, 0.0f};
static int pwm_cache[3] = {0, 0, 0};
static long previous_encoder[3] = {0, 0, 0};
static long encoder_cache[3] = {0, 0, 0};
static uint32_t previous_pid_ms = 0;
static bool pid_initialized = false;
static bool pure_rotation_command = false;
static bool manual_pwm_mode = false;
static int manual_pwm[3] = {0, 0, 0};
static bool feedback_fault_latched = false;
static int8_t feedback_fault_wheel = -1;
static uint32_t feedback_active_since_ms[3] = {0, 0, 0};
static uint32_t feedback_bad_since_ms[3] = {0, 0, 0};
static uint32_t fault_zero_start_ms = 0;
static uint32_t fault_last_zero_command_ms = 0;

typedef enum {
    WHEEL_START_IDLE = 0,
    WHEEL_START_STARTING,
    WHEEL_START_RUNNING,
} wheel_start_state_t;

static wheel_start_state_t wheel_start_state[3] = {
    WHEEL_START_IDLE,
    WHEEL_START_IDLE,
    WHEEL_START_IDLE,
};
static int8_t wheel_start_direction[3] = {0, 0, 0};
static uint32_t wheel_start_ms[3] = {0, 0, 0};
static uint32_t wheel_start_motion_ms[3] = {0, 0, 0};

#define CHASSIS_TUNING_MAGIC 0x4D4F4631UL

typedef struct {
    uint32_t magic;
    float start_pwm_positive[3];
    float start_pwm_negative[3];
    float running_pwm_positive[3];
    float running_pwm_negative[3];
    float feedforward_positive[3];
    float feedforward_negative[3];
    float pid_normal[3];
    float pid_rotation[3];
} chassis_tuning_t;

static chassis_tuning_t tuning = {};

static motor_id_t motor_id_from_index(uint8_t index) {
    if (index == 0) {
        return MOTOR_1;
    }
    if (index == 1) {
        return MOTOR_2;
    }
    return MOTOR_3;
}

static int round_pwm(float pwm) {
    return (int)(pwm + (pwm > 0 ? 0.5f : -0.5f));
}

static int wheel_speed_to_feedforward_pwm(uint8_t index, float speed) {
    float gain = speed >= 0.0f
               ? tuning.feedforward_positive[index]
               : tuning.feedforward_negative[index];
    return round_pwm(speed / MAX_WHEEL_SPEED * PWM_MAX * gain);
}

static int constrain_pwm(int speed) {
    return constrain(speed, -PWM_MAX, PWM_MAX);
}

static bool target_is_zero(uint8_t index) {
    return fabsf(target_speed[index]) < CHASSIS_TARGET_DEADBAND;
}

static int startup_pwm_for_direction(uint8_t index, bool positive) {
    int configured_start = round_pwm(positive
                                   ? tuning.start_pwm_positive[index]
                                   : tuning.start_pwm_negative[index]);
    return max(START_EFFECTIVE_PWM, configured_start);
}

static int apply_startup_pwm(uint8_t index, int pwm, float measured_speed,
                             float target, uint32_t now) {
    if (fabsf(target) < CHASSIS_TARGET_DEADBAND) {
        wheel_start_state[index] = WHEEL_START_IDLE;
        wheel_start_direction[index] = 0;
        wheel_start_ms[index] = 0;
        wheel_start_motion_ms[index] = 0;
        return pwm;
    }

    bool positive = target > 0.0f;
    int8_t direction = positive ? 1 : -1;
    int configured_start = startup_pwm_for_direction(index, positive);

    if (!pure_rotation_command) {
        int translation_start_pwm = max(CHASSIS_TRANSLATION_START_EFFECTIVE_PWM,
                                        configured_start);
        if (wheel_start_direction[index] != 0 &&
            wheel_start_direction[index] != direction) {
            wheel_start_state[index] = WHEEL_START_IDLE;
            wheel_start_direction[index] = 0;
            wheel_start_ms[index] = 0;
            wheel_start_motion_ms[index] = 0;
        }
        if (wheel_start_state[index] == WHEEL_START_IDLE &&
            fabsf(target) < CHASSIS_TRANSLATION_START_MIN_TARGET) {
            return pwm;
        }
        if (wheel_start_state[index] == WHEEL_START_IDLE ||
            wheel_start_direction[index] != direction) {
            wheel_start_state[index] = WHEEL_START_STARTING;
            wheel_start_direction[index] = direction;
            wheel_start_ms[index] = now;
            wheel_start_motion_ms[index] = 0;
        }

        if (wheel_start_state[index] == WHEEL_START_STARTING) {
            bool motion_sample = measured_speed * target > 0.0f &&
                                 fabsf(measured_speed) > CHASSIS_STARTUP_SPEED_THRESHOLD;
            if (motion_sample) {
                if (wheel_start_motion_ms[index] == 0) {
                    wheel_start_motion_ms[index] = now;
                }
            } else {
                wheel_start_motion_ms[index] = 0;
            }
            bool encoder_started = wheel_start_motion_ms[index] != 0 &&
                                   now - wheel_start_motion_ms[index] >=
                                   CHASSIS_TRANSLATION_START_CONFIRM_MS;
            bool pulse_complete = now - wheel_start_ms[index] >=
                                  CHASSIS_TRANSLATION_STARTUP_PULSE_MS;
            if (encoder_started || pulse_complete) {
                wheel_start_state[index] = WHEEL_START_RUNNING;
            } else if (abs(pwm) < translation_start_pwm) {
                return positive ? translation_start_pwm : -translation_start_pwm;
            }
        }

        // Once a continuous translation has started, encoder quantization to zero
        // must not retrigger the pulse. Zero target or a direction change rearms it.
        return pwm;
    }

    // Preserve the existing pure-rotation startup behavior and PID selection.
    int configured_running = round_pwm(positive
                                     ? tuning.running_pwm_positive[index]
                                     : tuning.running_pwm_negative[index]);
    int running_min_pwm = max(ROTATION_MIN_RUNNING_PWM, configured_running);
    int min_pwm = fabsf(measured_speed) > CHASSIS_STARTUP_SPEED_THRESHOLD
                ? running_min_pwm
                : configured_start;

    if (abs(pwm) >= min_pwm) {
        return pwm;
    }

    return positive ? min_pwm : -min_pwm;
}

static float encoder_delta_to_speed(long delta, float dt) {
    return ((float)delta / (float)ENCODER_PPR_WHEEL) * WHEEL_CIRCUMFERENCE_M / dt;
}

static void reset_wheel_control_state(uint8_t index) {
    integral_error[index] = 0.0f;
    previous_error[index] = 0.0f;
    feedback_active_since_ms[index] = 0;
    feedback_bad_since_ms[index] = 0;
    wheel_start_state[index] = WHEEL_START_IDLE;
    wheel_start_direction[index] = 0;
    wheel_start_ms[index] = 0;
    wheel_start_motion_ms[index] = 0;
}

static void reset_pid_state(void) {
    for (uint8_t i = 0; i < 3; i++) {
        reset_wheel_control_state(i);
        previous_encoder[i] = Motor_Get_Encoder(motor_id_from_index(i));
        measured_speed_cache[i] = 0.0f;
        pwm_cache[i] = 0;
        encoder_cache[i] = previous_encoder[i];
    }
    previous_pid_ms = millis();
    pid_initialized = true;
}

static int calculate_pid_pwm(uint8_t index, float measured_speed, float dt,
                             uint32_t now) {
    if (target_is_zero(index)) {
        integral_error[index] = 0.0f;
        previous_error[index] = 0.0f;
        return 0;
    }

    float error = target_speed[index] - measured_speed;
    float derivative = (error - previous_error[index]) / dt;
    previous_error[index] = error;

    float kp = tuning.pid_normal[0];
    float ki = tuning.pid_normal[1];
    float kd = tuning.pid_normal[2];
    if (pure_rotation_command &&
        fabsf(target_speed[index]) < CHASSIS_LOW_SPEED_TARGET) {
        kp = tuning.pid_rotation[0];
        ki = tuning.pid_rotation[1];
        kd = tuning.pid_rotation[2];
    }
    float feedforward = (float)wheel_speed_to_feedforward_pwm(index, target_speed[index]);
    float candidate_integral = constrain(
        integral_error[index] + error * dt,
        -CHASSIS_PID_INTEGRAL_LIMIT,
        CHASSIS_PID_INTEGRAL_LIMIT
    );
    float candidate_output = feedforward
                           + kp * error
                           + ki * candidate_integral
                           + kd * derivative;
    bool pushes_positive_saturation = candidate_output > PWM_MAX && error > 0.0f;
    bool pushes_negative_saturation = candidate_output < -PWM_MAX && error < 0.0f;
    if (!pushes_positive_saturation && !pushes_negative_saturation) {
        integral_error[index] = candidate_integral;
    }

    float correction = kp * error
                     + ki * integral_error[index]
                     + kd * derivative;

    int pwm = round_pwm(feedforward + correction);
    pwm = apply_startup_pwm(index, pwm, measured_speed, target_speed[index], now);
    return constrain_pwm(pwm);
}

static void set_wheel_targets(float v1, float v2, float v3) {
    float next_target[3] = {v1, v2, v3};

    float max_speed = max(fabsf(next_target[0]),
                          max(fabsf(next_target[1]), fabsf(next_target[2])));
    if (max_speed > MAX_WHEEL_SPEED) {
        float scale = MAX_WHEEL_SPEED / max_speed;
        next_target[0] *= scale;
        next_target[1] *= scale;
        next_target[2] *= scale;
    }

    for (uint8_t i = 0; i < 3; i++) {
        bool direction_changed =
            fabsf(target_speed[i]) >= CHASSIS_TARGET_DEADBAND &&
            fabsf(next_target[i]) >= CHASSIS_TARGET_DEADBAND &&
            target_speed[i] * next_target[i] < 0.0f;
        target_speed[i] = next_target[i];
        if (direction_changed) {
            reset_wheel_control_state(i);
        }
    }
}

static void update_wheel_targets_from_command(void) {
    float vx = command_vx;
    float vy = command_vy;
    float w = command_w;

    pure_rotation_command = fabsf(vx) <= PURE_ROTATION_V_THRESHOLD &&
                            fabsf(vy) <= PURE_ROTATION_V_THRESHOLD &&
                            fabsf(w) > CHASSIS_TARGET_DEADBAND;

    set_wheel_targets(-vy - w * WHEEL_BASE_RADIUS,
                      SQRT3_OVER_2 * vx + 0.5f * vy - w * WHEEL_BASE_RADIUS,
                      -SQRT3_OVER_2 * vx + 0.5f * vy - w * WHEEL_BASE_RADIUS);
}

void Chassis_SetVelocity(float vx, float vy, float w) {
    manual_pwm_mode = false;
    uint32_t now = millis();
    bool zero_command = fabsf(vx) < CHASSIS_TARGET_DEADBAND &&
                        fabsf(vy) < CHASSIS_TARGET_DEADBAND &&
                        fabsf(w) < CHASSIS_TARGET_DEADBAND;
    if (feedback_fault_latched && zero_command) {
        bool zero_stream_broken = fault_last_zero_command_ms == 0 ||
                                  now - fault_last_zero_command_ms >
                                  CHASSIS_FAULT_ZERO_MAX_GAP_MS;
        if (zero_stream_broken) {
            fault_zero_start_ms = now;
        }
        fault_last_zero_command_ms = now;
        if (now - fault_zero_start_ms >= CHASSIS_FAULT_CLEAR_ZERO_MS) {
            feedback_fault_latched = false;
            feedback_fault_wheel = -1;
            fault_zero_start_ms = 0;
            fault_last_zero_command_ms = 0;
            reset_pid_state();
        }
    } else if (!zero_command) {
        fault_zero_start_ms = 0;
        fault_last_zero_command_ms = 0;
    }
    command_vx = vx;
    command_vy = vy;
    command_w = w;
    update_wheel_targets_from_command();

#if !CHASSIS_PID_ENABLED
    int s1 = constrain_pwm(wheel_speed_to_feedforward_pwm(0, target_speed[0]));
    int s2 = constrain_pwm(wheel_speed_to_feedforward_pwm(1, target_speed[1]));
    int s3 = constrain_pwm(wheel_speed_to_feedforward_pwm(2, target_speed[2]));
    Motor_All(s1, s2, s3);
#endif
}

void Chassis_SetManualPwm(int pwm1, int pwm2, int pwm3) {
    manual_pwm_mode = true;
    command_vx = 0.0f;
    command_vy = 0.0f;
    command_w = 0.0f;
    set_wheel_targets(0.0f, 0.0f, 0.0f);
    manual_pwm[0] = constrain_pwm(pwm1);
    manual_pwm[1] = constrain_pwm(pwm2);
    manual_pwm[2] = constrain_pwm(pwm3);
    reset_pid_state();
}

void Chassis_Update(void) {
#if CHASSIS_PID_ENABLED
    uint32_t now = millis();
    if (!pid_initialized) {
        reset_pid_state();
        return;
    }

    uint32_t elapsed_ms = now - previous_pid_ms;
    if (elapsed_ms < CHASSIS_PID_INTERVAL_MS) {
        return;
    }

    update_wheel_targets_from_command();

    float dt = elapsed_ms / 1000.0f;
    previous_pid_ms = now;

    long current_encoder[3];
    float measured_speed[3];
    int pwm[3];

    for (uint8_t i = 0; i < 3; i++) {
        current_encoder[i] = Motor_Get_Encoder(motor_id_from_index(i));
        long delta = current_encoder[i] - previous_encoder[i];
        previous_encoder[i] = current_encoder[i];
        measured_speed[i] = encoder_delta_to_speed(delta, dt);
        if (feedback_fault_latched) {
            pwm[i] = 0;
        } else if (manual_pwm_mode) {
            pwm[i] = manual_pwm[i];
        } else {
            pwm[i] = calculate_pid_pwm(i, measured_speed[i], dt, now);
        }
        measured_speed_cache[i] = measured_speed[i];
        pwm_cache[i] = pwm[i];
        encoder_cache[i] = current_encoder[i];
    }

    if (!feedback_fault_latched && !manual_pwm_mode) {
        for (uint8_t i = 0; i < 3; i++) {
            bool active = fabsf(target_speed[i]) >= CHASSIS_FEEDBACK_TARGET_MIN;
            if (!active) {
                feedback_active_since_ms[i] = 0;
                feedback_bad_since_ms[i] = 0;
                continue;
            }
            if (feedback_active_since_ms[i] == 0) {
                feedback_active_since_ms[i] = now;
            }
            bool grace_complete = now - feedback_active_since_ms[i] >=
                                  CHASSIS_FEEDBACK_STARTUP_GRACE_MS;
            bool saturated_under_speed =
                abs(pwm[i]) >= CHASSIS_FEEDBACK_PWM_SATURATION &&
                fabsf(measured_speed[i]) <
                    CHASSIS_FEEDBACK_UNDERSPEED_RATIO * fabsf(target_speed[i]);
            if (grace_complete && saturated_under_speed) {
                if (feedback_bad_since_ms[i] == 0) {
                    feedback_bad_since_ms[i] = now;
                } else if (now - feedback_bad_since_ms[i] >=
                           CHASSIS_FEEDBACK_FAULT_CONFIRM_MS) {
                    feedback_fault_latched = true;
                    feedback_fault_wheel = (int8_t)(i + 1);
                    reset_pid_state();
                    break;
                }
            } else {
                feedback_bad_since_ms[i] = 0;
            }
        }
    }

    if (feedback_fault_latched) {
        for (uint8_t i = 0; i < 3; i++) {
            pwm[i] = 0;
            pwm_cache[i] = 0;
        }
    }

    Motor_All(pwm[0], pwm[1], pwm[2]);
#endif
}

void Chassis_Stop(void) {
    manual_pwm_mode = false;
    command_vx = 0.0f;
    command_vy = 0.0f;
    command_w = 0.0f;
    set_wheel_targets(0.0f, 0.0f, 0.0f);
    reset_pid_state();
    Motor_All(0, 0, 0);
}

void Chassis_GetDebug(float target[3], float measured[3], int pwm[3],
                      long encoder[3], float distance_m[3]) {
    for (uint8_t i = 0; i < 3; i++) {
        target[i] = target_speed[i];
        measured[i] = measured_speed_cache[i];
        pwm[i] = pwm_cache[i];
        encoder[i] = Motor_Get_Encoder(motor_id_from_index(i));
        distance_m[i] = ((float)encoder[i] / (float)ENCODER_PPR_WHEEL) * WHEEL_CIRCUMFERENCE_M;
    }
}

static bool valid_pwm_values(const float values[3]) {
    for (uint8_t i = 0; i < 3; i++) {
        if (!isfinite(values[i]) || values[i] < 0.0f || values[i] > PWM_MAX) {
            return false;
        }
    }
    return true;
}

static bool valid_gain_values(const float values[3]) {
    for (uint8_t i = 0; i < 3; i++) {
        if (!isfinite(values[i]) || values[i] < 0.1f || values[i] > 5.0f) {
            return false;
        }
    }
    return true;
}

static bool valid_pid_values(const float values[3]) {
    return isfinite(values[0]) && values[0] >= 0.0f && values[0] <= 5000.0f &&
           isfinite(values[1]) && values[1] >= 0.0f && values[1] <= 1000.0f &&
           isfinite(values[2]) && values[2] >= 0.0f && values[2] <= 1000.0f;
}

bool Chassis_SetTuningGroup(uint8_t group, const float values[3]) {
    float *destination = nullptr;
    bool valid = false;
    switch (group) {
        case CHASSIS_TUNE_START_PWM_POSITIVE:
            destination = tuning.start_pwm_positive; valid = valid_pwm_values(values); break;
        case CHASSIS_TUNE_START_PWM_NEGATIVE:
            destination = tuning.start_pwm_negative; valid = valid_pwm_values(values); break;
        case CHASSIS_TUNE_RUNNING_PWM_POSITIVE:
            destination = tuning.running_pwm_positive; valid = valid_pwm_values(values); break;
        case CHASSIS_TUNE_RUNNING_PWM_NEGATIVE:
            destination = tuning.running_pwm_negative; valid = valid_pwm_values(values); break;
        case CHASSIS_TUNE_FEEDFORWARD_POSITIVE:
            destination = tuning.feedforward_positive; valid = valid_gain_values(values); break;
        case CHASSIS_TUNE_FEEDFORWARD_NEGATIVE:
            destination = tuning.feedforward_negative; valid = valid_gain_values(values); break;
        case CHASSIS_TUNE_PID_NORMAL:
            destination = tuning.pid_normal; valid = valid_pid_values(values); break;
        case CHASSIS_TUNE_PID_ROTATION:
            destination = tuning.pid_rotation; valid = valid_pid_values(values); break;
        default:
            return false;
    }
    if (!valid) {
        return false;
    }
    for (uint8_t i = 0; i < 3; i++) {
        destination[i] = values[i];
    }
    return true;
}

bool Chassis_GetTuningGroup(uint8_t group, float values[3]) {
    const float *source = nullptr;
    switch (group) {
        case CHASSIS_TUNE_START_PWM_POSITIVE: source = tuning.start_pwm_positive; break;
        case CHASSIS_TUNE_START_PWM_NEGATIVE: source = tuning.start_pwm_negative; break;
        case CHASSIS_TUNE_RUNNING_PWM_POSITIVE: source = tuning.running_pwm_positive; break;
        case CHASSIS_TUNE_RUNNING_PWM_NEGATIVE: source = tuning.running_pwm_negative; break;
        case CHASSIS_TUNE_FEEDFORWARD_POSITIVE: source = tuning.feedforward_positive; break;
        case CHASSIS_TUNE_FEEDFORWARD_NEGATIVE: source = tuning.feedforward_negative; break;
        case CHASSIS_TUNE_PID_NORMAL: source = tuning.pid_normal; break;
        case CHASSIS_TUNE_PID_ROTATION: source = tuning.pid_rotation; break;
        default: return false;
    }
    for (uint8_t i = 0; i < 3; i++) {
        values[i] = source[i];
    }
    return true;
}

void Chassis_ResetTuningDefaults(void) {
    tuning.magic = CHASSIS_TUNING_MAGIC;
    for (uint8_t i = 0; i < 3; i++) {
        tuning.start_pwm_positive[i] = START_EFFECTIVE_PWM;
        tuning.start_pwm_negative[i] = START_EFFECTIVE_PWM;
        tuning.running_pwm_positive[i] = MIN_RUNNING_PWM;
        tuning.running_pwm_negative[i] = MIN_RUNNING_PWM;
        tuning.feedforward_positive[i] = 1.0f;
        tuning.feedforward_negative[i] = 1.0f;
    }
    tuning.pid_normal[0] = CHASSIS_PID_KP;
    tuning.pid_normal[1] = CHASSIS_PID_KI;
    tuning.pid_normal[2] = CHASSIS_PID_KD;
    tuning.pid_rotation[0] = CHASSIS_LOW_SPEED_PID_KP;
    tuning.pid_rotation[1] = CHASSIS_LOW_SPEED_PID_KI;
    tuning.pid_rotation[2] = CHASSIS_PID_KD;
}

bool Chassis_SaveTuning(void) {
    Preferences preferences;
    if (!preferences.begin("mof_chassis", false)) {
        return false;
    }
    size_t written = preferences.putBytes("tuning", &tuning, sizeof(tuning));
    preferences.end();
    return written == sizeof(tuning);
}

bool Chassis_LoadTuning(void) {
    Preferences preferences;
    if (!preferences.begin("mof_chassis", true)) {
        Chassis_ResetTuningDefaults();
        return false;
    }
    size_t length = preferences.getBytesLength("tuning");
    size_t read = length == sizeof(tuning)
                ? preferences.getBytes("tuning", &tuning, sizeof(tuning))
                : 0;
    preferences.end();
    if (read != sizeof(tuning) || tuning.magic != CHASSIS_TUNING_MAGIC) {
        Chassis_ResetTuningDefaults();
        return false;
    }
    return true;
}

void Chassis_ResetEncoders(void) {
    Motor_Reset_Encoder(MOTOR_ALL);
    reset_pid_state();
}

bool Chassis_HasFeedbackFault(void) {
    return feedback_fault_latched;
}

int8_t Chassis_GetFeedbackFaultWheel(void) {
    return feedback_fault_wheel;
}
