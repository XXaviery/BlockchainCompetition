#include <cassert>
#include <cmath>
#include <cstdint>
#include <iostream>

static uint32_t fake_ms = 1;
uint32_t millis(void) { return fake_ms; }

// Compile the production implementation itself against host-only Arduino and
// Preferences stubs.  This keeps the timing/latch assertions tied to the exact
// code that PlatformIO builds for the ESP32.
#include "../firmware/lib/chassis/chassis.cpp"

static long fake_encoder[3] = {0, 0, 0};
static int applied_pwm[3] = {0, 0, 0};

void Motor(motor_id_t id, int speed) {
    if (id == MOTOR_ALL) {
        applied_pwm[0] = applied_pwm[1] = applied_pwm[2] = speed;
    } else {
        applied_pwm[id] = speed;
    }
}

void Motor_All(int s1, int s2, int s3) {
    applied_pwm[0] = s1;
    applied_pwm[1] = s2;
    applied_pwm[2] = s3;
}

void Motor_Stop(motor_id_t id) { Motor(id, 0); }

long Motor_Get_Encoder(motor_id_t id) {
    return id == MOTOR_ALL ? 0 : fake_encoder[id];
}

void Motor_Reset_Encoder(motor_id_t id) {
    if (id == MOTOR_ALL) {
        fake_encoder[0] = fake_encoder[1] = fake_encoder[2] = 0;
    } else {
        fake_encoder[id] = 0;
    }
}

static void advance(uint32_t milliseconds, long d1, long d2, long d3) {
    fake_ms += milliseconds;
    fake_encoder[0] += d1;
    fake_encoder[1] += d2;
    fake_encoder[2] += d3;
    Chassis_Update();
}

static void set_frozen_tuning(void) {
    float normal[3] = {900.0f, 80.0f, 0.0f};
    float rotation[3] = {1500.0f, 800.0f, 0.0f};
    float all_110[3] = {110.0f, 110.0f, 110.0f};
    float all_83[3] = {83.0f, 83.0f, 83.0f};
    float all_2[3] = {2.0f, 2.0f, 2.0f};
    assert(Chassis_SetTuningGroup(CHASSIS_TUNE_PID_NORMAL, normal));
    assert(Chassis_SetTuningGroup(CHASSIS_TUNE_PID_ROTATION, rotation));
    assert(Chassis_SetTuningGroup(CHASSIS_TUNE_START_PWM_POSITIVE, all_110));
    assert(Chassis_SetTuningGroup(CHASSIS_TUNE_START_PWM_NEGATIVE, all_110));
    assert(Chassis_SetTuningGroup(CHASSIS_TUNE_RUNNING_PWM_POSITIVE, all_83));
    assert(Chassis_SetTuningGroup(CHASSIS_TUNE_RUNNING_PWM_NEGATIVE, all_83));
    assert(Chassis_SetTuningGroup(CHASSIS_TUNE_FEEDFORWARD_POSITIVE, all_2));
    assert(Chassis_SetTuningGroup(CHASSIS_TUNE_FEEDFORWARD_NEGATIVE, all_2));
}

int main() {
    set_frozen_tuning();
    Chassis_Stop();
    advance(20, 0, 0, 0);

    // A saturated, stationary translation wheel must not accumulate integral.
    Chassis_SetVelocity(0.20f, 0.0f, 0.0f);
    for (int i = 0; i < 10; ++i) {
        advance(20, 0, 0, 0);
    }
    assert(std::fabs(integral_error[1]) < 1e-7f);
    assert(std::fabs(integral_error[2]) < 1e-7f);

    // A direction change clears every affected wheel's PID history immediately.
    Chassis_SetVelocity(-0.20f, 0.0f, 0.0f);
    assert(std::fabs(integral_error[1]) < 1e-7f);
    assert(std::fabs(integral_error[2]) < 1e-7f);

    Chassis_Stop();
    Chassis_SetVelocity(0.20f, 0.0f, 0.0f);
    // M2 is frozen.  M3 advances about 0.177 m/s (12 counts / 20 ms), so only
    // logical M2 satisfies saturated severe under-speed after the grace window.
    for (int i = 0; i < 45 && !Chassis_HasFeedbackFault(); ++i) {
        advance(20, 0, 0, -12);
    }
    assert(Chassis_HasFeedbackFault());
    assert(Chassis_GetFeedbackFaultWheel() == 2);
    assert(applied_pwm[0] == 0 && applied_pwm[1] == 0 && applied_pwm[2] == 0);
    assert(std::fabs(integral_error[0]) < 1e-7f);
    assert(std::fabs(integral_error[1]) < 1e-7f);
    assert(std::fabs(integral_error[2]) < 1e-7f);

    // Non-zero traffic cannot clear the latch.
    for (int i = 0; i < 30; ++i) {
        Chassis_SetVelocity(0.20f, 0.0f, 0.0f);
        advance(20, 0, 0, 0);
    }
    assert(Chassis_HasFeedbackFault());

    // One zero message followed by silence is not a continuous zero stream.
    Chassis_SetVelocity(0.0f, 0.0f, 0.0f);
    for (int i = 0; i < 30; ++i) {
        advance(20, 0, 0, 0);
    }
    assert(Chassis_HasFeedbackFault());

    // Fresh zero commands at 20 Hz for at least 500 ms clear the latch.
    for (int i = 0; i < 12; ++i) {
        Chassis_SetVelocity(0.0f, 0.0f, 0.0f);
        advance(50, 0, 0, 0);
    }
    assert(!Chassis_HasFeedbackFault());
    assert(Chassis_GetFeedbackFaultWheel() == -1);
    assert(applied_pwm[0] == 0 && applied_pwm[1] == 0 && applied_pwm[2] == 0);

    std::cout << "CHASSIS_FEEDBACK_SAFETY_HOST_TEST=PASS\n";
    return 0;
}
