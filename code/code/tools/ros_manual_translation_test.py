#!/usr/bin/env python3
"""Run one wheel-odometry-bounded translation round trip in manual open-field mode."""

import argparse
import json
import math
import signal
import threading
import time

import rclpy
from geometry_msgs.msg import Twist
from lifecycle_msgs.srv import GetState
from nav_msgs.msg import Odometry
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node
from rclpy.signals import SignalHandlerOptions
from sensor_msgs.msg import Imu, LaserScan
from std_msgs.msg import Float64MultiArray


UPSTREAM_TOPIC = "/cmd_vel_nav"
FINAL_TOPIC = "/cmd_vel"
STOP_TAIL_SECONDS = 1.5
PUBLISH_RATE_HZ = 20.0
STREAM_MAX_AGE_S = 0.6
CONTROL_MAX_AGE_S = 0.5
CHAIN_CHECK_INTERVAL_S = 0.25
ACTIVE_LIFECYCLE_ID = 3
MAX_CROSS_TRACK_M = 0.05
MAX_YAW_DEG = 8.0
STRAIGHT_ACCEPTANCE_YAW_DEG = 3.0
MAX_OVERSHOOT_M = 0.03
MAX_PWM = 245.0
MIN_SATURATED_TRACKING_RATIO = 0.50
ZERO_TARGET_MPS = 0.001
ZERO_MEASURED_MPS = 0.005
ZERO_PWM = 0.5
NONZERO_MPS = 0.005
MIN_ENCODER_COUNTS = 1.0
SMOOTHER_DECEL_MPS2 = 1.5
STEADY_FINAL_RATIO = 0.95
STEADY_HOLD_S = 0.15
ANALYSIS_DISTANCE_M = 0.15
MAX_TIMED_TOTAL_DISTANCE_M = 0.35


class SafetyAbort(RuntimeError):
    pass


def yaw_from_quaternion(q):
    return math.atan2(
        2.0 * (q.w * q.z + q.x * q.y),
        1.0 - 2.0 * (q.y * q.y + q.z * q.z),
    )


def angle_delta(current, reference):
    return math.atan2(math.sin(current - reference), math.cos(current - reference))


def pose_dict(sample):
    return {
        "t": sample[0],
        "x": sample[1],
        "y": sample[2],
        "yaw_deg": math.degrees(sample[3]),
    }


class ManualTranslationTest(Node):
    def __init__(self):
        super().__init__("mof_manual_translation_test")
        self.callback_group = ReentrantCallbackGroup()
        self.command_pub = self.create_publisher(Twist, UPSTREAM_TOPIC, 10)
        self.create_subscription(
            Twist,
            FINAL_TOPIC,
            self.final_callback,
            50,
            callback_group=self.callback_group,
        )
        self.create_subscription(
            Odometry,
            "/wheel/odom",
            self.wheel_callback,
            50,
            callback_group=self.callback_group,
        )
        self.create_subscription(
            Odometry,
            "/odom",
            self.ekf_callback,
            100,
            callback_group=self.callback_group,
        )
        self.create_subscription(
            Imu,
            "/imu",
            self.imu_callback,
            100,
            callback_group=self.callback_group,
        )
        self.create_subscription(
            LaserScan,
            "/scan",
            self.scan_callback,
            20,
            callback_group=self.callback_group,
        )
        self.create_subscription(
            Float64MultiArray,
            "/chassis/debug",
            self.debug_callback,
            50,
            callback_group=self.callback_group,
        )
        self.lifecycle_client = self.create_client(
            GetState,
            "/velocity_smoother/get_state",
            callback_group=self.callback_group,
        )
        self.lifecycle_future = None
        self.lifecycle_request_time = 0.0
        self.lifecycle_stamp = 0.0
        self.lifecycle_id = None
        self.lifecycle_label = None
        self.final = []
        self.wheel = []
        self.ekf = []
        self.imu = []
        self.debug = []
        self.last_scan_time = None
        self.stop_signal = None
        self.last_leg_evidence = None
        self.chain_error = None
        self.chain_monitor_stop = threading.Event()
        self.chain_monitor_thread = None

    def final_callback(self, msg):
        self.final.append(
            (time.monotonic(), msg.linear.x, msg.linear.y, msg.angular.z)
        )

    def wheel_callback(self, msg):
        self.wheel.append(
            (
                time.monotonic(),
                msg.pose.pose.position.x,
                msg.pose.pose.position.y,
                yaw_from_quaternion(msg.pose.pose.orientation),
            )
        )

    def ekf_callback(self, msg):
        self.ekf.append(
            (
                time.monotonic(),
                msg.pose.pose.position.x,
                msg.pose.pose.position.y,
                yaw_from_quaternion(msg.pose.pose.orientation),
            )
        )

    def imu_callback(self, msg):
        self.imu.append(
            (time.monotonic(), yaw_from_quaternion(msg.orientation), msg.angular_velocity.z)
        )

    def scan_callback(self, _msg):
        self.last_scan_time = time.monotonic()

    def debug_callback(self, msg):
        if len(msg.data) >= 15:
            self.debug.append((time.monotonic(), tuple(msg.data[:15])))

    def publish(self, vx=0.0, vy=0.0):
        msg = Twist()
        msg.linear.x = vx
        msg.linear.y = vy
        self.command_pub.publish(msg)

    def request_stop(self, signum):
        if self.stop_signal is None:
            try:
                self.stop_signal = signal.Signals(signum).name
            except ValueError:
                self.stop_signal = str(signum)

    def raise_if_stop_requested(self):
        if self.stop_signal is not None:
            raise SafetyAbort(f"received {self.stop_signal}")

    @staticmethod
    def endpoint_name(endpoint):
        namespace = endpoint.node_namespace.rstrip("/")
        return f"{namespace}/{endpoint.node_name}" if namespace else f"/{endpoint.node_name}"

    def endpoint_summary(self, topic):
        return {
            "publishers": sorted(
                self.endpoint_name(item)
                for item in self.get_publishers_info_by_topic(topic)
            ),
            "subscriptions": sorted(
                self.endpoint_name(item)
                for item in self.get_subscriptions_info_by_topic(topic)
            ),
        }

    def check_topology(self):
        observer = self.get_fully_qualified_name()
        actual = {
            UPSTREAM_TOPIC: self.endpoint_summary(UPSTREAM_TOPIC),
            FINAL_TOPIC: self.endpoint_summary(FINAL_TOPIC),
        }
        expected = {
            UPSTREAM_TOPIC: {
                "publishers": [observer],
                "subscriptions": ["/velocity_smoother"],
            },
            FINAL_TOPIC: {
                "publishers": ["/velocity_smoother"],
                "subscriptions": sorted(["/esp32_cmd_vel_bridge", observer]),
            },
        }
        if actual != expected:
            raise SafetyAbort(
                "manual-chain topology changed: "
                + json.dumps({"actual": actual, "expected": expected}, sort_keys=True)
            )
        return actual

    def poll_lifecycle(self):
        now = time.monotonic()
        if self.lifecycle_future is not None:
            if self.lifecycle_future.done():
                response = self.lifecycle_future.result()
                self.lifecycle_id = response.current_state.id
                self.lifecycle_label = response.current_state.label
                self.lifecycle_stamp = now
                self.lifecycle_future = None
            elif now - self.lifecycle_request_time > 0.5:
                raise SafetyAbort("velocity_smoother lifecycle query timed out")
        if (
            self.lifecycle_future is None
            and now - self.lifecycle_request_time >= CHAIN_CHECK_INTERVAL_S
        ):
            if not self.lifecycle_client.service_is_ready():
                raise SafetyAbort("velocity_smoother lifecycle service unavailable")
            self.lifecycle_future = self.lifecycle_client.call_async(GetState.Request())
            self.lifecycle_request_time = now

    def check_lifecycle(self):
        deadline = time.monotonic() + 0.55
        while time.monotonic() < deadline:
            self.poll_lifecycle()
            if (
                self.lifecycle_id == ACTIVE_LIFECYCLE_ID
                and self.lifecycle_label == "active"
                and time.monotonic() - self.lifecycle_stamp <= STREAM_MAX_AGE_S
                and self.lifecycle_future is None
            ):
                break
            time.sleep(0.01)
        age = time.monotonic() - self.lifecycle_stamp
        if (
            self.lifecycle_id != ACTIVE_LIFECYCLE_ID
            or self.lifecycle_label != "active"
            or age > STREAM_MAX_AGE_S
        ):
            raise SafetyAbort(
                "velocity_smoother not confirmed active: "
                f"id={self.lifecycle_id}, label={self.lifecycle_label!r}, age={age:.3f}"
            )
        return {"id": self.lifecycle_id, "label": self.lifecycle_label, "age_s": age}

    def check_chain(self):
        return {"topics": self.check_topology(), "lifecycle": self.check_lifecycle()}

    def start_chain_monitor(self):
        if self.chain_monitor_thread is not None:
            raise SafetyAbort("chain monitor already started")

        def monitor():
            while not self.chain_monitor_stop.wait(CHAIN_CHECK_INTERVAL_S):
                try:
                    self.check_chain()
                except Exception as exc:
                    self.chain_error = f"{type(exc).__name__}: {exc}"
                    return

        self.chain_monitor_thread = threading.Thread(
            target=monitor,
            name="manual_chain_monitor",
            daemon=True,
        )
        self.chain_monitor_thread.start()

    def stop_chain_monitor(self):
        self.chain_monitor_stop.set()
        if self.chain_monitor_thread is not None:
            self.chain_monitor_thread.join(timeout=1.0)

    def assert_chain_monitor(self):
        if self.chain_error is not None:
            raise SafetyAbort("chain monitor latched: " + self.chain_error)

    def assert_fresh(self, require_final=True, phase_start=None):
        self.raise_if_stop_requested()
        self.assert_chain_monitor()
        now = time.monotonic()
        streams = {
            "wheel odom": self.wheel[-1][0] if self.wheel else 0.0,
            "EKF odom": self.ekf[-1][0] if self.ekf else 0.0,
            "IMU": self.imu[-1][0] if self.imu else 0.0,
            "chassis debug": self.debug[-1][0] if self.debug else 0.0,
            "scan": self.last_scan_time or 0.0,
        }
        stale = [name for name, stamp in streams.items() if now - stamp > STREAM_MAX_AGE_S]
        if require_final:
            final_stamp = self.final[-1][0] if self.final else 0.0
            in_grace = (
                phase_start is not None
                and final_stamp < phase_start
                and now - phase_start <= CONTROL_MAX_AGE_S
            )
            if not in_grace and now - final_stamp > CONTROL_MAX_AGE_S:
                stale.append("final cmd")
        if stale:
            ages = {
                "wheel odom": now - streams["wheel odom"],
                "EKF odom": now - streams["EKF odom"],
                "IMU": now - streams["IMU"],
                "chassis debug": now - streams["chassis debug"],
                "scan": now - streams["scan"],
                "final cmd": now - (self.final[-1][0] if self.final else 0.0),
            }
            raise SafetyAbort(
                "stale streams: " + ", ".join(stale)
                + "; ages=" + json.dumps(ages, sort_keys=True)
            )

    def assert_stopped(self):
        if not self.debug:
            raise SafetyAbort("missing chassis debug")
        values = self.debug[-1][1]
        target = max(abs(values[i]) for i in range(3))
        measured = max(abs(values[3 + i]) for i in range(3))
        pwm = max(abs(values[6 + i]) for i in range(3))
        if target > ZERO_TARGET_MPS or measured > ZERO_MEASURED_MPS or pwm > ZERO_PWM:
            raise SafetyAbort(
                f"chassis not stopped: target={target:.6f}, "
                f"measured={measured:.6f}, pwm={pwm:.1f}"
            )

    def spin_publish(self, vx, vy, duration, *, ignore_stop=False):
        phase_start = time.monotonic()
        deadline = phase_start + duration
        period = 1.0 / PUBLISH_RATE_HZ
        while time.monotonic() < deadline:
            tick = time.monotonic()
            if not ignore_stop:
                self.raise_if_stop_requested()
            self.publish(vx, vy)
            time.sleep(min(0.01, period))
            if not ignore_stop:
                self.assert_fresh(require_final=True, phase_start=phase_start)
            remaining = period - (time.monotonic() - tick)
            if remaining > 0:
                time.sleep(remaining)

    def emergency_zero(self):
        self.spin_publish(0.0, 0.0, STOP_TAIL_SECONDS, ignore_stop=True)

    def wait_ready(self, timeout=12.0):
        deadline = time.monotonic() + timeout
        last_error = "waiting for telemetry"
        while rclpy.ok() and time.monotonic() < deadline:
            self.publish()
            time.sleep(0.05)
            try:
                self.assert_fresh(require_final=True)
                chain = self.check_chain()
                self.assert_stopped()
                return chain
            except SafetyAbort as exc:
                last_error = str(exc)
        raise SafetyAbort(last_error)

    @staticmethod
    def local_displacement(start, current, reference_yaw):
        dx = current[1] - start[1]
        dy = current[2] - start[2]
        return (
            math.cos(reference_yaw) * dx + math.sin(reference_yaw) * dy,
            -math.sin(reference_yaw) * dx + math.cos(reference_yaw) * dy,
        )

    @staticmethod
    def sample_at_or_before(samples, stamp):
        candidates = [sample for sample in samples if sample[0] <= stamp]
        return candidates[-1] if candidates else None

    @staticmethod
    def sample_at_or_after(samples, stamp):
        return next((sample for sample in samples if sample[0] >= stamp), None)

    def find_steady_bounds(self, final_window, ux, uy, speed, command_end):
        candidate = None
        last_eligible = None
        intervals = []
        for sample in final_window:
            if sample[0] > command_end:
                break
            projected = sample[1] * ux + sample[2] * uy
            if projected >= STEADY_FINAL_RATIO * speed:
                candidate = sample[0] if candidate is None else candidate
                last_eligible = sample[0]
            else:
                if (candidate is not None and last_eligible is not None and
                        last_eligible - candidate >= STEADY_HOLD_S):
                    intervals.append((candidate, last_eligible))
                candidate = None
                last_eligible = None
        if (candidate is not None and last_eligible is not None and
                last_eligible - candidate >= STEADY_HOLD_S):
            intervals.append((candidate, last_eligible))
        return intervals[-1] if intervals else None

    def summarize_phase(self, name, phase_start, phase_end, reference_yaw, ux, uy):
        debug = [item for item in self.debug if phase_start <= item[0] <= phase_end]
        final = [item for item in self.final if phase_start <= item[0] <= phase_end]
        imu = [item for item in self.imu if phase_start <= item[0] <= phase_end]
        wheel_start = self.sample_at_or_before(self.wheel, phase_start)
        wheel_end = self.sample_at_or_before(self.wheel, phase_end)
        result = {
            "name": name,
            "start_t": phase_start,
            "end_t": phase_end,
            "duration_s": max(0.0, phase_end - phase_start),
            "debug_sample_count": len(debug),
            "final_sample_count": len(final),
            "imu_sample_count": len(imu),
        }
        if debug:
            target_avg = [sum(item[1][i] for item in debug) / len(debug) for i in range(3)]
            measured_avg = [
                sum(item[1][3 + i] for item in debug) / len(debug) for i in range(3)
            ]
            result.update({
                "target_avg_mps": target_avg,
                "measured_avg_mps": measured_avg,
                "tracking_ratio": [
                    (abs(measured_avg[i] / target_avg[i])
                     if abs(target_avg[i]) > 0.015 else None)
                    for i in range(3)
                ],
                "pwm_avg_abs": [
                    sum(abs(item[1][6 + i]) for item in debug) / len(debug)
                    for i in range(3)
                ],
                "pwm_peak_abs": [
                    max(abs(item[1][6 + i]) for item in debug) for i in range(3)
                ],
                "measured_speed_sum_avg_mps": sum(measured_avg),
                "inferred_wz_avg_rad_s": -sum(measured_avg) / (3.0 * 0.138),
                "encoder_delta": [
                    debug[-1][1][9 + i] - debug[0][1][9 + i] for i in range(3)
                ],
                "wheel_distance_delta_m": [
                    debug[-1][1][12 + i] - debug[0][1][12 + i] for i in range(3)
                ],
            })
        if final:
            projected = [item[1] * ux + item[2] * uy for item in final]
            result["final_projected_avg_mps"] = sum(projected) / len(projected)
            result["final_projected_peak_mps"] = max(projected)
        if imu:
            result["imu_gyro_z_avg_rad_s"] = sum(item[2] for item in imu) / len(imu)
            result["imu_yaw_delta_deg"] = math.degrees(
                angle_delta(imu[-1][1], imu[0][1])
            )
        if wheel_start is not None and wheel_end is not None:
            local_x, local_y = self.local_displacement(
                wheel_start, wheel_end, reference_yaw
            )
            projection = local_x * ux + local_y * uy
            cross_track = -local_x * uy + local_y * ux
            yaw_deg = math.degrees(angle_delta(wheel_end[3], wheel_start[3]))
            result.update({
                "wheel_projection_m": projection,
                "wheel_cross_track_m": cross_track,
                "wheel_yaw_delta_deg": yaw_deg,
                "yaw_deg_per_0_10m": (
                    yaw_deg * 0.10 / abs(projection) if abs(projection) > 0.005 else None
                ),
            })
        return result

    def build_trace_20hz(self, start_time, end_time, command_end, vx, vy):
        trace = []
        for stamp, debug_values in self.debug:
            if not start_time <= stamp <= end_time:
                continue
            final = self.sample_at_or_before(self.final, stamp)
            wheel = self.sample_at_or_before(self.wheel, stamp)
            ekf = self.sample_at_or_before(self.ekf, stamp)
            imu = self.sample_at_or_before(self.imu, stamp)
            trace.append({
                "t_s": stamp - start_time,
                "cmd_vel_nav": [vx, vy, 0.0] if stamp <= command_end else [0.0, 0.0, 0.0],
                "cmd_vel": list(final[1:4]) if final is not None else None,
                "chassis_debug": list(debug_values),
                "wheel_odom": list(wheel[1:4]) if wheel is not None else None,
                "ekf_odom": list(ekf[1:4]) if ekf is not None else None,
                "imu": [imu[1], imu[2]] if imu is not None else None,
            })
        return trace

    def run_leg(
        self, vx, vy, distance, reference_yaw, label,
        command_seconds=None, max_total_distance=MAX_TIMED_TOTAL_DISTANCE_M,
    ):
        self.check_chain()
        start_time = time.monotonic()
        start_wheel = self.wheel[-1]
        start_ekf = self.ekf[-1]
        start_imu = self.imu[-1]
        imu_start = len(self.imu) - 1
        debug_start = len(self.debug)
        final_start = len(self.final)
        speed = math.hypot(vx, vy)
        ux, uy = vx / speed, vy / speed
        braking_distance = speed * speed / (2.0 * SMOOTHER_DECEL_MPS2) + 0.005
        command_stop_projection = max(0.02, distance - braking_distance)
        timed_mode = command_seconds is not None
        duration_limit = (
            command_seconds + 0.5 if timed_mode else distance / speed + 1.8
        )
        stopped_since = [None, None, None]
        saturated_since = [None, None, None]
        final_without_target_since = None
        period = 1.0 / PUBLISH_RATE_HZ

        while rclpy.ok():
            tick = time.monotonic()
            self.raise_if_stop_requested()
            self.publish(vx, vy)
            time.sleep(min(0.01, period))
            now = time.monotonic()
            self.assert_fresh(require_final=True, phase_start=start_time)

            current = self.wheel[-1]
            local_x, local_y = self.local_displacement(start_wheel, current, reference_yaw)
            projection = local_x * ux + local_y * uy
            cross_track = -local_x * uy + local_y * ux
            planar = math.hypot(local_x, local_y)
            yaw_change = abs(math.degrees(angle_delta(current[3], reference_yaw)))

            elapsed = now - start_time
            final_window = self.final[final_start:]
            debug_window = [values for _, values in self.debug[debug_start:]]
            final_peak = max(
                (math.hypot(item[1], item[2]) for item in final_window), default=0.0
            )
            target_peaks = [
                max((abs(item[i]) for item in debug_window), default=0.0)
                for i in range(3)
            ]
            measured_peaks = [
                max((abs(item[3 + i]) for item in debug_window), default=0.0)
                for i in range(3)
            ]
            pwm_peaks = [
                max((abs(item[6 + i]) for item in debug_window), default=0.0)
                for i in range(3)
            ]
            values = self.debug[-1][1]
            encoder_delta = [
                values[9 + i] - debug_window[0][9 + i]
                for i in range(3)
            ] if debug_window else [0.0, 0.0, 0.0]
            self.last_leg_evidence = {
                "label": label,
                "elapsed_s": elapsed,
                "wheel_projection_m": projection,
                "wheel_cross_track_m": cross_track,
                "wheel_yaw_deg": yaw_change,
                "final_peak_mps": final_peak,
                "target_peak_mps": target_peaks,
                "measured_peak_mps": measured_peaks,
                "pwm_peak": pwm_peaks,
                "encoder_delta": encoder_delta,
                "latest_target_mps": list(values[:3]),
                "latest_measured_mps": list(values[3:6]),
                "latest_pwm": list(values[6:9]),
            }
            if yaw_change > MAX_YAW_DEG:
                raise SafetyAbort(f"{label}: wheel-odom yaw {yaw_change:.2f} deg")
            if abs(cross_track) > MAX_CROSS_TRACK_M:
                raise SafetyAbort(f"{label}: cross-track {cross_track:.4f} m")
            if planar > 0.005 and projection < -0.005:
                raise SafetyAbort(f"{label}: odometry direction reversed")
            if timed_mode:
                if planar > max_total_distance:
                    raise SafetyAbort(f"{label}: total displacement {planar:.4f} m")
            elif projection > distance + MAX_OVERSHOOT_M:
                raise SafetyAbort(f"{label}: overshoot {projection:.4f} m")
            if elapsed > 0.45 and final_peak <= NONZERO_MPS:
                raise SafetyAbort(f"{label}: final /cmd_vel never became non-zero")
            if elapsed > 0.45 and max(target_peaks) <= NONZERO_MPS:
                raise SafetyAbort(f"{label}: ESP32 target never became non-zero")

            final_now = math.hypot(self.final[-1][1], self.final[-1][2])
            target_now = max(abs(values[i]) for i in range(3))
            if elapsed > 0.4 and final_now > NONZERO_MPS and target_now <= NONZERO_MPS:
                final_without_target_since = final_without_target_since or now
                if now - final_without_target_since > 0.3:
                    raise SafetyAbort(f"{label}: final non-zero but ESP32 target zero")
            else:
                final_without_target_since = None

            for i in range(3):
                target = abs(values[i])
                measured = abs(values[3 + i])
                pwm = abs(values[6 + i])
                if elapsed > 0.5 and target > 0.015 and measured < ZERO_MEASURED_MPS:
                    stopped_since[i] = stopped_since[i] or now
                    if now - stopped_since[i] > 0.30:
                        raise SafetyAbort(f"{label}: commanded wheel {i + 1} stopped")
                else:
                    stopped_since[i] = None
                if (pwm > MAX_PWM and target > 0.015 and
                        measured < MIN_SATURATED_TRACKING_RATIO * target):
                    saturated_since[i] = saturated_since[i] or now
                    if now - saturated_since[i] > 0.20:
                        raise SafetyAbort(
                            f"{label}: wheel {i + 1} PWM>{MAX_PWM:.0f} "
                            f"with tracking ratio {measured / target:.2f}"
                        )
                else:
                    saturated_since[i] = None

            if elapsed > 0.75 and debug_window:
                encoder_delta = [
                    values[9 + i] - debug_window[0][9 + i] for i in range(3)
                ]
                for i in range(3):
                    if target_peaks[i] > 0.015 and abs(encoder_delta[i]) < MIN_ENCODER_COUNTS:
                        raise SafetyAbort(f"{label}: wheel {i + 1} encoder unchanged")

            if timed_mode and elapsed >= command_seconds:
                command_end = time.monotonic()
                break
            if not timed_mode and projection >= command_stop_projection:
                command_end = time.monotonic()
                break
            if elapsed >= duration_limit:
                raise SafetyAbort(
                    f"{label}: duration limit at projection {projection:.4f} m"
                )
            remaining = period - (time.monotonic() - tick)
            if remaining > 0:
                time.sleep(remaining)

        self.spin_publish(0.0, 0.0, STOP_TAIL_SECONDS)
        self.assert_stopped()
        self.check_chain()
        end_time = time.monotonic()
        end_wheel = self.wheel[-1]
        end_ekf = self.ekf[-1]
        end_imu = self.imu[-1]
        local_x, local_y = self.local_displacement(start_wheel, end_wheel, reference_yaw)
        projection = local_x * ux + local_y * uy
        cross_track = -local_x * uy + local_y * ux
        yaw_change = math.degrees(angle_delta(end_wheel[3], reference_yaw))
        stamped_debug_window = [
            item for item in self.debug[debug_start:] if item[0] <= end_time
        ]
        debug_window = [item[1] for item in stamped_debug_window]
        final_window = [item for item in self.final[final_start:] if item[0] <= end_time]
        target_peak_mps = [
            max(abs(item[i]) for item in debug_window) for i in range(3)
        ]
        imu_window = [sample for sample in self.imu[imu_start:] if sample[0] <= end_time]
        gyro_z_integral = sum(
            0.5 * (imu_window[i - 1][2] + imu_window[i][2]) *
            (imu_window[i][0] - imu_window[i - 1][0])
            for i in range(1, len(imu_window))
        )
        encoder_delta = [
            debug_window[-1][9 + i] - debug_window[0][9 + i] for i in range(3)
        ]
        wheel_distance_delta = [
            debug_window[-1][12 + i] - debug_window[0][12 + i] for i in range(3)
        ]
        ekf_dx = end_ekf[1] - start_ekf[1]
        ekf_dy = end_ekf[2] - start_ekf[2]
        steady_bounds = self.find_steady_bounds(
            final_window, ux, uy, speed, command_end
        )
        acceleration_end = steady_bounds[0] if steady_bounds else command_end
        phases = {
            "acceleration": self.summarize_phase(
                "acceleration", start_time, acceleration_end, reference_yaw, ux, uy
            ),
            "steady": (
                self.summarize_phase(
                    "steady", steady_bounds[0], steady_bounds[1], reference_yaw, ux, uy
                ) if steady_bounds else None
            ),
            "deceleration_stop": self.summarize_phase(
                "deceleration_stop", command_end, end_time, reference_yaw, ux, uy
            ),
        }

        analysis_crossing = None
        for sample in self.wheel:
            if not start_time <= sample[0] <= end_time:
                continue
            crossing_x, crossing_y = self.local_displacement(
                start_wheel, sample, reference_yaw
            )
            crossing_projection = crossing_x * ux + crossing_y * uy
            if crossing_projection >= ANALYSIS_DISTANCE_M:
                crossing_ekf = self.sample_at_or_before(self.ekf, sample[0])
                crossing_imu = self.sample_at_or_before(self.imu, sample[0])
                analysis_crossing = {
                    "distance_m": crossing_projection,
                    "t_s": sample[0] - start_time,
                    "wheel_yaw_delta_deg": math.degrees(
                        angle_delta(sample[3], reference_yaw)
                    ),
                    "wheel_cross_track_m": -crossing_x * uy + crossing_y * ux,
                    "ekf_yaw_delta_deg": (
                        math.degrees(angle_delta(crossing_ekf[3], start_ekf[3]))
                        if crossing_ekf is not None else None
                    ),
                    "imu_yaw_delta_deg": (
                        math.degrees(angle_delta(crossing_imu[1], start_imu[1]))
                        if crossing_imu is not None else None
                    ),
                }
                break

        result = {
            "label": label,
            "command": {"vx": vx, "vy": vy},
            "command_seconds": command_end - start_time,
            "timed_mode": timed_mode,
            "wheel_projection_m": projection,
            "wheel_cross_track_m": cross_track,
            "wheel_distance_m": math.hypot(local_x, local_y),
            "wheel_yaw_delta_deg": yaw_change,
            "ekf_dx_m": ekf_dx,
            "ekf_dy_m": ekf_dy,
            "ekf_distance_m": math.hypot(ekf_dx, ekf_dy),
            "ekf_yaw_delta_deg": math.degrees(angle_delta(end_ekf[3], start_ekf[3])),
            "imu_yaw_delta_deg": math.degrees(angle_delta(end_imu[1], start_imu[1])),
            "final_peak_mps": max(
                (math.hypot(item[1], item[2]) for item in final_window), default=0.0
            ),
            "target_peak_mps": target_peak_mps,
            "measured_peak_mps": [
                max(abs(item[3 + i]) for item in debug_window) for i in range(3)
            ],
            "pwm_peak": [
                max(abs(item[6 + i]) for item in debug_window) for i in range(3)
            ],
            "steady_definition": {
                "final_ratio": STEADY_FINAL_RATIO,
                "minimum_hold_s": STEADY_HOLD_S,
                "bounds": list(steady_bounds) if steady_bounds else None,
            },
            "phases": phases,
            "at_0_15m": analysis_crossing,
            "straight_acceptance_yaw_limit_deg": STRAIGHT_ACCEPTANCE_YAW_DEG,
            "imu_gyro_z_integral_rad": gyro_z_integral,
            "encoder_delta": encoder_delta,
            "wheel_distance_delta_m": wheel_distance_delta,
            "start": {"wheel": pose_dict(start_wheel), "ekf": pose_dict(start_ekf)},
            "end": {"wheel": pose_dict(end_wheel), "ekf": pose_dict(end_ekf)},
            "trace_20hz": self.build_trace_20hz(
                start_time, end_time, command_end, vx, vy
            ),
        }
        self.last_leg_evidence = result

        if steady_bounds is None:
            raise SafetyAbort(f"{label}: no >=95% final-speed window held for 0.15 s")
        if analysis_crossing is None:
            raise SafetyAbort(f"{label}: did not reach 0.15 m analysis distance")
        if timed_mode:
            if math.hypot(local_x, local_y) > max_total_distance:
                raise SafetyAbort(f"{label}: final total distance {math.hypot(local_x, local_y):.4f} m")
        else:
            if projection < distance - MAX_OVERSHOOT_M:
                raise SafetyAbort(f"{label}: stopped short at {projection:.4f} m")
            if projection > distance + MAX_OVERSHOOT_M:
                raise SafetyAbort(f"{label}: stopped long at {projection:.4f} m")
        if abs(cross_track) > MAX_CROSS_TRACK_M:
            raise SafetyAbort(f"{label}: final cross-track {cross_track:.4f} m")
        if abs(yaw_change) > MAX_YAW_DEG:
            raise SafetyAbort(f"{label}: final yaw {yaw_change:.2f} deg")
        if (timed_mode and
                abs(analysis_crossing["wheel_yaw_delta_deg"]) >
                STRAIGHT_ACCEPTANCE_YAW_DEG):
            raise SafetyAbort(
                f"{label}: yaw at 0.15 m "
                f"{analysis_crossing['wheel_yaw_delta_deg']:.2f} deg exceeds "
                f"{STRAIGHT_ACCEPTANCE_YAW_DEG:.1f} deg straight-line limit"
            )
        return result

    def run_roundtrip(self, vx, vy, distance, pause):
        chain = self.wait_ready()
        start_wheel = self.wheel[-1]
        start_ekf = self.ekf[-1]
        start_imu = self.imu[-1]
        outbound = self.run_leg(vx, vy, distance, start_wheel[3], "outbound")
        self.spin_publish(0.0, 0.0, pause)
        self.assert_stopped()
        self.check_chain()
        inbound = self.run_leg(-vx, -vy, distance, start_wheel[3], "return")
        self.spin_publish(0.0, 0.0, STOP_TAIL_SECONDS)
        self.assert_stopped()
        end_wheel = self.wheel[-1]
        end_ekf = self.ekf[-1]
        end_imu = self.imu[-1]
        return {
            "mode": "MANUAL OPEN-FIELD MODE: COLLISION MONITOR BYPASSED",
            "chain": chain,
            "outbound": outbound,
            "return": inbound,
            "return_residual": {
                "wheel_m": math.hypot(
                    end_wheel[1] - start_wheel[1], end_wheel[2] - start_wheel[2]
                ),
                "wheel_yaw_deg": math.degrees(angle_delta(end_wheel[3], start_wheel[3])),
                "ekf_m": math.hypot(
                    end_ekf[1] - start_ekf[1], end_ekf[2] - start_ekf[2]
                ),
                "ekf_yaw_deg": math.degrees(angle_delta(end_ekf[3], start_ekf[3])),
                "imu_yaw_deg": math.degrees(angle_delta(end_imu[1], start_imu[1])),
            },
            "third_residual_compensation": False,
            "final_debug": list(self.debug[-1][1]),
        }

    def run_single_leg(self, vx, vy, distance, command_seconds=None):
        chain = self.wait_ready()
        reference_yaw = self.wheel[-1][3]
        leg = self.run_leg(
            vx, vy, distance, reference_yaw, "single_leg",
            command_seconds=command_seconds,
        )
        self.spin_publish(0.0, 0.0, STOP_TAIL_SECONDS)
        self.assert_stopped()
        self.check_chain()
        return {
            "mode": "MANUAL OPEN-FIELD MODE: COLLISION MONITOR BYPASSED",
            "chain": chain,
            "single_leg": leg,
            "return_executed": False,
            "third_residual_compensation": False,
            "final_debug": list(self.debug[-1][1]),
        }

    def run_zero_only(self):
        chain = self.wait_ready()
        final_start = len(self.final)
        self.spin_publish(0.0, 0.0, 4.0)
        self.assert_stopped()
        final_end = len(self.final)
        samples = self.final[final_start:final_end]
        gaps = [samples[i][0] - samples[i - 1][0] for i in range(1, len(samples))]
        if len(samples) < 60 or not gaps:
            raise SafetyAbort(
                f"final cadence invalid: samples={len(samples)}, "
                f"max_gap={max(gaps, default=float('inf')):.4f}s"
            )
        self.check_chain()
        return {
            "mode": "MANUAL OPEN-FIELD MODE: ZERO-ONLY SELF-CHECK",
            "chain": chain,
            "final_debug": list(self.debug[-1][1]),
            "final_sample_count": len(samples),
            "final_max_gap_s": max(gaps),
        }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--direction", choices=("+x", "-x", "+y", "-y"))
    parser.add_argument("--distance", type=float, default=0.15)
    parser.add_argument("--speed", type=float, default=0.15)
    parser.add_argument("--pause", type=float, default=1.5)
    parser.add_argument("--confirm-open-field", action="store_true", required=True)
    parser.add_argument("--zero-only", action="store_true")
    parser.add_argument("--single-leg", action="store_true")
    parser.add_argument(
        "--command-seconds",
        type=float,
        help="Timed single-leg command window; allowed range is 0.8 to 1.0 s.",
    )
    args = parser.parse_args()
    if not args.zero_only and args.direction is None:
        parser.error("--direction is required unless --zero-only is used")
    if args.zero_only and args.direction is not None:
        parser.error("--zero-only and --direction are mutually exclusive")
    if args.zero_only and args.single_leg:
        parser.error("--zero-only and --single-leg are mutually exclusive")
    if args.command_seconds is not None and not args.single_leg:
        parser.error("--command-seconds requires --single-leg")
    if args.command_seconds is not None and not 0.8 <= args.command_seconds <= 1.0:
        parser.error("--command-seconds must be in [0.8, 1.0] s")
    if not 0.05 <= args.distance <= 0.15:
        parser.error("distance must be in [0.05, 0.15] m")
    if not 0.10 <= args.speed <= 0.30:
        parser.error("speed must be in [0.10, 0.30] m/s")
    if not 1.0 <= args.pause <= 2.0:
        parser.error("pause must be in [1.0, 2.0] s")
    vectors = {
        "+x": (args.speed, 0.0),
        "-x": (-args.speed, 0.0),
        "+y": (0.0, args.speed),
        "-y": (0.0, -args.speed),
    }

    rclpy.init(signal_handler_options=SignalHandlerOptions.NO)
    node = ManualTranslationTest()
    executor = MultiThreadedExecutor(num_threads=3)
    executor.add_node(node)
    executor_thread = threading.Thread(
        target=executor.spin,
        name="manual_translation_executor",
        daemon=True,
    )
    executor_thread.start()
    old_handlers = {}

    def handle_signal(signum, _frame):
        node.request_stop(signum)

    for name in ("SIGINT", "SIGTERM", "SIGHUP"):
        if hasattr(signal, name):
            sig = getattr(signal, name)
            old_handlers[sig] = signal.getsignal(sig)
            signal.signal(sig, handle_signal)

    status = "FAILED"
    result = None
    try:
        if args.zero_only:
            result = node.run_zero_only()
        else:
            vx, vy = vectors[args.direction]
            if args.single_leg:
                result = node.run_single_leg(
                    vx, vy, args.distance, command_seconds=args.command_seconds
                )
            else:
                result = node.run_roundtrip(vx, vy, args.distance, args.pause)
        status = "SUCCESS"
    except Exception as exc:
        result = {"error": f"{type(exc).__name__}: {exc}"}
        if node.last_leg_evidence is not None:
            result["last_leg_evidence"] = node.last_leg_evidence
    finally:
        try:
            node.emergency_zero()
            if node.debug:
                node.assert_stopped()
                result["cleanup_final_debug"] = list(node.debug[-1][1])
        except Exception as exc:
            status = "FAILED"
            result["cleanup_error"] = f"{type(exc).__name__}: {exc}"
        node.stop_chain_monitor()
        for sig, handler in old_handlers.items():
            signal.signal(sig, handler)
        print(f"STATUS={status}")
        print("RESULT=" + json.dumps(result, ensure_ascii=False, sort_keys=True))
        executor.remove_node(node)
        executor.shutdown(timeout_sec=2.0)
        executor_thread.join(timeout=2.0)
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
    if status != "SUCCESS":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
