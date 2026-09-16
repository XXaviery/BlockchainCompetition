#!/usr/bin/env python3
"""Run one bounded out-and-back translation through the ROS safety chain."""

import argparse
import json
import math
import signal
import time

import rclpy
from geometry_msgs.msg import Twist
from lifecycle_msgs.srv import GetState
from nav_msgs.msg import Odometry
from rclpy.node import Node
try:
    from rclpy.signals import SignalHandlerOptions
except ImportError:  # ROS 2 distributions predating configurable signal handlers.
    SignalHandlerOptions = None
from sensor_msgs.msg import Imu, LaserScan
from std_msgs.msg import Float64MultiArray
from visualization_msgs.msg import Marker, MarkerArray


UPSTREAM_TOPIC = "/cmd_vel_nav"
SMOOTHED_TOPIC = "/cmd_vel_smoothed"
FINAL_TOPIC = "/cmd_vel"
BASE_FRAME_ID = "base_footprint"
STOP_ZONE_HALF_EXTENT_M = 0.24
STOP_ZONE_MIN_POINTS = 4
STOP_BURST_SECONDS = 1.5
TOPOLOGY_CHECK_INTERVAL_S = 0.25
STREAM_MAX_AGE_S = 0.6
CONTROL_STREAM_MAX_AGE_S = 0.2
NONZERO_CMD_THRESHOLD = 0.005
ZERO_CMD_THRESHOLD = 0.001
ZERO_MEASURED_THRESHOLD = 0.005
ZERO_PWM_THRESHOLD = 0.5
MIN_EFFECTIVE_DISPLACEMENT_M = 0.005
MIN_ENCODER_DELTA_COUNTS = 1.0
MAX_YAW_CHANGE_DEG = 5.0
MAX_CROSS_TRACK_M = 0.02
ACTIVE_LIFECYCLE_ID = 3
MIN_MARKER_SAMPLES = 3
MIN_MARKER_SAMPLE_SPAN_S = 0.4


def yaw_from_quaternion(q):
    return math.atan2(
        2.0 * (q.w * q.z + q.x * q.y),
        1.0 - 2.0 * (q.y * q.y + q.z * q.z),
    )


def angle_delta(current, reference):
    return math.atan2(math.sin(current - reference), math.cos(current - reference))


def pose_dict(sample):
    return {
        "stamp": sample[0],
        "x": sample[1],
        "y": sample[2],
        "yaw_deg": math.degrees(sample[3]),
    }


def command_peak(sample):
    """Return the largest absolute x/y/yaw component of a timed Twist sample."""
    return max(abs(sample[1]), abs(sample[2]), abs(sample[3]))


def planar_command_speed(sample):
    return math.hypot(sample[1], sample[2])


class SafetyAbort(RuntimeError):
    pass


class StopRequested(SafetyAbort):
    pass


class SafeRoundTrip(Node):
    def __init__(self, upstream_topic):
        super().__init__("mof_safe_roundtrip_test")
        self.upstream_topic = upstream_topic
        self.publisher = self.create_publisher(Twist, upstream_topic, 10)
        self.create_subscription(Twist, SMOOTHED_TOPIC, self.smoothed_callback, 20)
        self.create_subscription(Twist, FINAL_TOPIC, self.final_callback, 20)
        self.create_subscription(Odometry, "/wheel/odom", self.wheel_callback, 50)
        self.create_subscription(Odometry, "/odom", self.ekf_callback, 100)
        self.create_subscription(Imu, "/imu", self.imu_callback, 100)
        self.create_subscription(LaserScan, "/scan", self.scan_callback, 20)
        self.create_subscription(
            MarkerArray,
            "/collision_monitor/collision_points_marker",
            self.collision_points_callback,
            10,
        )
        self.create_subscription(
            Float64MultiArray, "/chassis/debug", self.debug_callback, 50
        )
        self.smoothed = []
        self.final = []
        self.wheel = []
        self.ekf = []
        self.imu = []
        self.debug = []
        self.last_scan_time = None
        self.last_collision_time = None
        self.collision_point_count = 0
        self.collision_marker_count = 0
        self.observation_point_count = 0
        self.collision_marker_error = None
        self.collision_samples = []
        self.zero_message = Twist()
        self.stop_signal = None
        self.lifecycle_clients = {
            "velocity_smoother": self.create_client(
                GetState, "/velocity_smoother/get_state"
            ),
            "collision_monitor": self.create_client(
                GetState, "/collision_monitor/get_state"
            ),
        }
        self.lifecycle_status = {
            name: {
                "id": None,
                "label": None,
                "stamp": 0.0,
                "future": None,
                "request_stamp": 0.0,
            }
            for name in self.lifecycle_clients
        }

    def smoothed_callback(self, msg):
        self.smoothed.append((time.monotonic(), msg.linear.x, msg.linear.y, msg.angular.z))

    def final_callback(self, msg):
        self.final.append((time.monotonic(), msg.linear.x, msg.linear.y, msg.angular.z))

    def wheel_callback(self, msg):
        self.wheel.append((
            time.monotonic(),
            msg.pose.pose.position.x,
            msg.pose.pose.position.y,
            yaw_from_quaternion(msg.pose.pose.orientation),
        ))

    def ekf_callback(self, msg):
        self.ekf.append((
            time.monotonic(),
            msg.pose.pose.position.x,
            msg.pose.pose.position.y,
            yaw_from_quaternion(msg.pose.pose.orientation),
        ))

    def imu_callback(self, msg):
        self.imu.append((
            time.monotonic(),
            yaw_from_quaternion(msg.orientation),
            msg.angular_velocity.z,
        ))

    def scan_callback(self, _msg):
        self.last_scan_time = time.monotonic()

    def collision_points_callback(self, msg):
        now = time.monotonic()
        candidate_markers = [
            marker
            for marker in msg.markers
            if marker.action == Marker.ADD and marker.type == Marker.POINTS
        ]
        invalid_frames = sorted({
            marker.header.frame_id
            for marker in candidate_markers
            if marker.header.frame_id != BASE_FRAME_ID
        })
        if invalid_frames:
            self.collision_marker_error = (
                "POINTS/ADD marker frame must be base_footprint, got "
                + repr(invalid_frames)
            )

        points = []
        for marker in candidate_markers:
            if marker.header.frame_id != BASE_FRAME_ID:
                continue
            yaw = yaw_from_quaternion(marker.pose.orientation)
            cos_yaw = math.cos(yaw)
            sin_yaw = math.sin(yaw)
            for point in marker.points:
                points.append((
                    marker.pose.position.x + cos_yaw * point.x - sin_yaw * point.y,
                    marker.pose.position.y + sin_yaw * point.x + cos_yaw * point.y,
                ))

        self.last_collision_time = now
        self.collision_marker_count = len(candidate_markers)
        self.observation_point_count = len(points)
        self.collision_point_count = sum(
            abs(x) <= STOP_ZONE_HALF_EXTENT_M
            and abs(y) <= STOP_ZONE_HALF_EXTENT_M
            for x, y in points
        )
        self.collision_samples.append((
            now,
            self.collision_point_count,
            self.collision_marker_count,
            self.observation_point_count,
            self.collision_marker_error,
        ))

    def debug_callback(self, msg):
        if len(msg.data) >= 15:
            self.debug.append((time.monotonic(), tuple(msg.data[:15])))

    def publish(self, vx=0.0, vy=0.0):
        msg = Twist()
        msg.linear.x = vx
        msg.linear.y = vy
        self.publisher.publish(msg)

    def request_stop(self, signum):
        if self.stop_signal is None:
            try:
                self.stop_signal = signal.Signals(signum).name
            except ValueError:
                self.stop_signal = str(signum)

    def raise_if_stop_requested(self):
        if self.stop_signal is not None:
            raise StopRequested(f"received {self.stop_signal}")

    def zero_for(self, seconds, ignore_stop=False):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if not ignore_stop:
                self.raise_if_stop_requested()
            try:
                self.publisher.publish(self.zero_message)
            except Exception:
                if not ignore_stop:
                    raise
            if rclpy.ok():
                rclpy.spin_once(self, timeout_sec=0.02)
            else:
                time.sleep(0.02)

    @staticmethod
    def endpoint_name(item):
        namespace = item.node_namespace.rstrip("/")
        return f"{namespace}/{item.node_name}" if namespace else f"/{item.node_name}"

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
        if self.upstream_topic != UPSTREAM_TOPIC:
            raise SafetyAbort(
                f"unsafe upstream topic {self.upstream_topic!r}; required {UPSTREAM_TOPIC}"
            )
        observer = self.get_fully_qualified_name()
        actual = {
            UPSTREAM_TOPIC: self.endpoint_summary(UPSTREAM_TOPIC),
            SMOOTHED_TOPIC: self.endpoint_summary(SMOOTHED_TOPIC),
            FINAL_TOPIC: self.endpoint_summary(FINAL_TOPIC),
        }
        expected = {
            UPSTREAM_TOPIC: {
                "publishers": [observer],
                "subscriptions": ["/velocity_smoother"],
            },
            SMOOTHED_TOPIC: {
                "publishers": ["/velocity_smoother"],
                "subscriptions": sorted(["/collision_monitor", observer]),
            },
            FINAL_TOPIC: {
                "publishers": ["/collision_monitor"],
                "subscriptions": sorted(["/esp32_cmd_vel_bridge", observer]),
            },
        }
        if actual != expected:
            raise SafetyAbort(
                "unexpected safety-chain endpoints: "
                + json.dumps({"actual": actual, "expected": expected}, sort_keys=True)
            )
        return actual

    def poll_lifecycle(self):
        now = time.monotonic()
        for name, client in self.lifecycle_clients.items():
            status = self.lifecycle_status[name]
            future = status["future"]
            if future is not None:
                if future.done():
                    try:
                        response = future.result()
                    except Exception as exc:
                        raise SafetyAbort(
                            f"{name} lifecycle query failed: {exc}"
                        ) from exc
                    status["id"] = response.current_state.id
                    status["label"] = response.current_state.label
                    status["stamp"] = now
                    status["future"] = None
                elif now - status["request_stamp"] > 0.5:
                    raise SafetyAbort(f"{name} lifecycle query timed out")

            if (
                status["future"] is None
                and now - status["request_stamp"] >= TOPOLOGY_CHECK_INTERVAL_S
            ):
                if not client.service_is_ready():
                    raise SafetyAbort(f"{name} lifecycle service is unavailable")
                status["future"] = client.call_async(GetState.Request())
                status["request_stamp"] = now

    def lifecycle_summary(self):
        self.poll_lifecycle()
        now = time.monotonic()
        summary = {
            name: {
                "id": status["id"],
                "label": status["label"],
                "age_s": (
                    None if status["stamp"] == 0.0 else now - status["stamp"]
                ),
            }
            for name, status in self.lifecycle_status.items()
        }
        inactive = [
            name
            for name, status in self.lifecycle_status.items()
            if status["id"] != ACTIVE_LIFECYCLE_ID
            or status["label"] != "active"
            or now - status["stamp"] > STREAM_MAX_AGE_S
        ]
        if inactive:
            raise SafetyAbort(
                "lifecycle nodes not confirmed active: "
                + json.dumps({name: summary[name] for name in inactive}, sort_keys=True)
            )
        return summary

    def check_safety_chain(self):
        return {
            "topics": self.check_topology(),
            "lifecycle": self.lifecycle_summary(),
        }

    def wait_ready(self, timeout=12.0):
        deadline = time.monotonic() + timeout
        last_error = "waiting for topics"
        while rclpy.ok() and time.monotonic() < deadline:
            self.raise_if_stop_requested()
            self.publish()
            rclpy.spin_once(self, timeout_sec=0.05)
            try:
                safety_chain = self.check_safety_chain()
            except SafetyAbort as exc:
                last_error = str(exc)
                continue
            now = time.monotonic()
            streams_ready = (
                self.wheel and self.ekf and self.imu and self.debug
                and self.smoothed and self.last_scan_time is not None
                and now - self.wheel[-1][0] < 0.5
                and now - self.ekf[-1][0] < 0.5
                and now - self.imu[-1][0] < 0.5
                and now - self.debug[-1][0] < 0.5
                and now - self.smoothed[-1][0] < CONTROL_STREAM_MAX_AGE_S
                and now - self.last_scan_time < 0.5
            )
            if streams_ready:
                self.assert_collision_marker_valid()
                self.assert_chassis_stopped()
                return safety_chain
            stamps = {
                "wheel odom": self.wheel[-1][0] if self.wheel else None,
                "EKF odom": self.ekf[-1][0] if self.ekf else None,
                "IMU": self.imu[-1][0] if self.imu else None,
                "chassis debug": self.debug[-1][0] if self.debug else None,
                "smoothed cmd": self.smoothed[-1][0] if self.smoothed else None,
                "scan": self.last_scan_time,
            }
            missing = [name for name, stamp in stamps.items() if stamp is None]
            stale = [
                name for name, stamp in stamps.items()
                if stamp is not None and now - stamp >= 0.5
            ]
            last_error = f"missing={missing}, stale={stale}"
        raise SafetyAbort(last_error)

    def latest_pose(self, source):
        samples = self.ekf if source == "ekf" else self.wheel
        if not samples:
            raise SafetyAbort(f"missing {source} odometry")
        return samples[-1]

    def assert_collision_marker_valid(self):
        if self.collision_marker_error is not None:
            raise SafetyAbort(self.collision_marker_error)

    def assert_fresh(self, control_streams=(), control_grace_start=None):
        self.raise_if_stop_requested()
        now = time.monotonic()
        required = {
            "wheel odom": self.wheel[-1][0] if self.wheel else 0.0,
            "EKF odom": self.ekf[-1][0] if self.ekf else 0.0,
            "IMU": self.imu[-1][0] if self.imu else 0.0,
            "chassis debug": self.debug[-1][0] if self.debug else 0.0,
            "scan": self.last_scan_time or 0.0,
        }
        stale = [
            name for name, stamp in required.items()
            if now - stamp > STREAM_MAX_AGE_S
        ]
        control = {
            "smoothed cmd": self.smoothed[-1][0] if self.smoothed else 0.0,
            "final cmd": self.final[-1][0] if self.final else 0.0,
        }
        for name in control_streams:
            stamp = control.get(name, 0.0)
            waiting_for_phase_sample = (
                control_grace_start is not None
                and stamp < control_grace_start
                and now - control_grace_start <= CONTROL_STREAM_MAX_AGE_S
            )
            if not waiting_for_phase_sample and now - stamp > CONTROL_STREAM_MAX_AGE_S:
                stale.append(name)
        if stale:
            raise SafetyAbort("stale streams: " + ", ".join(stale))
        self.assert_collision_marker_valid()

    def assert_chassis_stopped(self):
        if not self.debug:
            raise SafetyAbort("missing chassis debug while checking stopped state")
        values = self.debug[-1][1]
        target_peak = max(abs(values[i]) for i in range(3))
        measured_peak = max(abs(values[3 + i]) for i in range(3))
        pwm_peak = max(abs(values[6 + i]) for i in range(3))
        if (
            target_peak > ZERO_CMD_THRESHOLD
            or measured_peak > ZERO_MEASURED_THRESHOLD
            or pwm_peak > ZERO_PWM_THRESHOLD
        ):
            raise SafetyAbort(
                "chassis not stopped: "
                f"target={target_peak:.6f}, measured={measured_peak:.6f}, "
                f"pwm={pwm_peak:.1f}"
            )

    def monitored_stop(self, seconds, require_final=True):
        phase_start = time.monotonic()
        deadline = phase_start + seconds
        next_chain_check = phase_start
        while rclpy.ok() and time.monotonic() < deadline:
            self.raise_if_stop_requested()
            self.publish()
            rclpy.spin_once(self, timeout_sec=0.02)
            controls = ("smoothed cmd", "final cmd") if require_final else (
                "smoothed cmd",
            )
            self.assert_fresh(controls, control_grace_start=phase_start)
            now = time.monotonic()
            if now >= next_chain_check:
                self.check_safety_chain()
                next_chain_check = now + TOPOLOGY_CHECK_INTERVAL_S
        self.raise_if_stop_requested()
        self.assert_fresh(("smoothed cmd", "final cmd") if require_final else (
            "smoothed cmd",
        ))
        chain = self.check_safety_chain()
        self.assert_chassis_stopped()
        return chain

    def require_stable_stop_zone(self, seconds=0.6):
        start_index = len(self.collision_samples)
        phase_start = time.monotonic()
        deadline = phase_start + seconds
        next_chain_check = phase_start
        while rclpy.ok() and time.monotonic() < deadline:
            self.raise_if_stop_requested()
            self.publish()
            rclpy.spin_once(self, timeout_sec=0.02)
            self.assert_fresh(
                ("smoothed cmd",), control_grace_start=phase_start
            )
            now = time.monotonic()
            if now >= next_chain_check:
                self.check_safety_chain()
                next_chain_check = now + TOPOLOGY_CHECK_INTERVAL_S
        samples = self.collision_samples[start_index:]
        if len(samples) < MIN_MARKER_SAMPLES:
            raise SafetyAbort("insufficient fresh StopZone marker samples")
        if samples[-1][0] - samples[0][0] < MIN_MARKER_SAMPLE_SPAN_S:
            raise SafetyAbort("StopZone marker samples did not cover a stable window")
        errors = [sample[4] for sample in samples if sample[4] is not None]
        if errors:
            raise SafetyAbort(errors[0])
        counts = [sample[1] for sample in samples]
        if min(counts) < STOP_ZONE_MIN_POINTS:
            raise SafetyAbort(
                "StopZone obstacle is not stable: "
                f"min={min(counts)}, max={max(counts)}, required>="
                f"{STOP_ZONE_MIN_POINTS}"
            )
        self.assert_chassis_stopped()
        return {
            "sample_count": len(samples),
            "span_s": samples[-1][0] - samples[0][0],
            "min_points": min(counts),
            "max_points": max(counts),
        }

    def run_leg(self, vx, vy, duration_limit, distance_limit, group_yaw, label):
        leg_start_time = time.monotonic()
        leg_start = self.latest_pose("ekf")
        wheel_start = self.latest_pose("wheel")
        debug_start_index = len(self.debug)
        final_start_index = len(self.final)
        speed = math.hypot(vx, vy)
        ux, uy = vx / speed, vy / speed
        next_topology_check = leg_start_time
        stopped_since = [None, None, None]
        saturated_since = [None, None, None]

        while rclpy.ok():
            self.raise_if_stop_requested()
            self.publish(vx, vy)
            rclpy.spin_once(self, timeout_sec=0.02)
            now = time.monotonic()
            self.assert_fresh(
                ("smoothed cmd", "final cmd"),
                control_grace_start=leg_start_time,
            )

            if now >= next_topology_check:
                self.check_safety_chain()
                next_topology_check = now + TOPOLOGY_CHECK_INTERVAL_S

            current = self.latest_pose("ekf")
            dx = current[1] - leg_start[1]
            dy = current[2] - leg_start[2]
            distance = math.hypot(dx, dy)
            yaw_change = abs(math.degrees(angle_delta(current[3], group_yaw)))
            if yaw_change > MAX_YAW_CHANGE_DEG:
                raise SafetyAbort(f"{label}: unexpected yaw {yaw_change:.2f} deg")

            local_x = math.cos(group_yaw) * dx + math.sin(group_yaw) * dy
            local_y = -math.sin(group_yaw) * dx + math.cos(group_yaw) * dy
            projection = local_x * ux + local_y * uy
            cross_track = -local_x * uy + local_y * ux
            if abs(cross_track) > MAX_CROSS_TRACK_M:
                raise SafetyAbort(
                    f"{label}: cross-track {cross_track:.4f} m exceeds "
                    f"{MAX_CROSS_TRACK_M:.3f} m"
                )
            if distance > MIN_EFFECTIVE_DISPLACEMENT_M and projection < -0.005:
                raise SafetyAbort(f"{label}: physical/odom direction is reversed")

            elapsed = now - leg_start_time
            final_window = self.final[final_start_index:]
            debug_window = [sample for _, sample in self.debug[debug_start_index:]]
            final_peak = max(
                (planar_command_speed(sample) for sample in final_window),
                default=0.0,
            )
            target_peak = max(
                (max(abs(sample[i]) for i in range(3)) for sample in debug_window),
                default=0.0,
            )
            if elapsed > 0.40 and final_peak <= NONZERO_CMD_THRESHOLD:
                raise SafetyAbort(f"{label}: no non-zero final /cmd_vel evidence")
            if elapsed > 0.40 and target_peak <= NONZERO_CMD_THRESHOLD:
                raise SafetyAbort(f"{label}: no non-zero ESP32 target evidence")

            latest_debug = self.debug[-1][1]
            for i in range(3):
                target = abs(latest_debug[i])
                measured = abs(latest_debug[3 + i])
                pwm = abs(latest_debug[6 + i])
                if elapsed > 0.5 and target > 0.015 and measured < 0.005:
                    stopped_since[i] = stopped_since[i] or now
                    if now - stopped_since[i] > 0.30:
                        raise SafetyAbort(f"{label}: wheel {i + 1} stopped")
                else:
                    stopped_since[i] = None
                if pwm >= 250:
                    saturated_since[i] = saturated_since[i] or now
                    if now - saturated_since[i] > 0.30:
                        raise SafetyAbort(f"{label}: wheel {i + 1} PWM saturated")
                else:
                    saturated_since[i] = None

            if projection >= distance_limit or elapsed >= duration_limit:
                end_time = time.monotonic()
                return self.finalize_leg(
                    label=label,
                    start_time=leg_start_time,
                    end_time=end_time,
                    start=leg_start,
                    end=current,
                    wheel_start=wheel_start,
                    wheel_end=self.latest_pose("wheel"),
                    projection=projection,
                    cross_track=cross_track,
                    final_start_index=final_start_index,
                    debug_start_index=debug_start_index,
                )

        raise SafetyAbort(f"{label}: ROS shutdown")

    def finalize_leg(
        self,
        *,
        label,
        start_time,
        end_time,
        start,
        end,
        wheel_start,
        wheel_end,
        projection,
        cross_track,
        final_start_index,
        debug_start_index,
    ):
        final = [
            sample for sample in self.final[final_start_index:]
            if start_time <= sample[0] <= end_time
        ]
        debug = [
            sample for stamp, sample in self.debug[debug_start_index:]
            if start_time <= stamp <= end_time
        ]
        if not final:
            raise SafetyAbort(f"{label}: missing final /cmd_vel samples")
        if not debug:
            raise SafetyAbort(f"{label}: missing debug samples")
        final_peak = max(planar_command_speed(sample) for sample in final)
        target_peak = max(max(abs(x[i]) for i in range(3)) for x in debug)
        measured_peak = max(max(abs(x[3 + i]) for i in range(3)) for x in debug)
        encoder_delta = [debug[-1][9 + i] - debug[0][9 + i] for i in range(3)]
        encoder_peak = max(abs(value) for value in encoder_delta)
        ekf_distance = math.hypot(end[1] - start[1], end[2] - start[2])
        wheel_distance = math.hypot(
            wheel_end[1] - wheel_start[1], wheel_end[2] - wheel_start[2]
        )

        if final_peak <= NONZERO_CMD_THRESHOLD:
            raise SafetyAbort(f"{label}: final /cmd_vel never became non-zero")
        if target_peak <= NONZERO_CMD_THRESHOLD:
            raise SafetyAbort(f"{label}: ESP32 target never became non-zero")
        if encoder_peak < MIN_ENCODER_DELTA_COUNTS:
            raise SafetyAbort(
                f"{label}: encoder delta {encoder_peak:.1f} below "
                f"{MIN_ENCODER_DELTA_COUNTS:.1f} count"
            )
        if ekf_distance < MIN_EFFECTIVE_DISPLACEMENT_M:
            raise SafetyAbort(
                f"{label}: EKF displacement {ekf_distance:.4f} m below "
                f"{MIN_EFFECTIVE_DISPLACEMENT_M:.4f} m"
            )
        if projection < MIN_EFFECTIVE_DISPLACEMENT_M:
            raise SafetyAbort(
                f"{label}: forward projection {projection:.4f} m below "
                f"{MIN_EFFECTIVE_DISPLACEMENT_M:.4f} m"
            )

        target_mean = [sum(x[i] for x in debug) / len(debug) for i in range(3)]
        measured_mean = [sum(x[3 + i] for x in debug) / len(debug) for i in range(3)]
        pwm_mean = [sum(x[6 + i] for x in debug) / len(debug) for i in range(3)]
        pwm_peak = [max(abs(x[6 + i]) for x in debug) for i in range(3)]
        return {
            "label": label,
            "start_time": start_time,
            "end_time": end_time,
            "elapsed": end_time - start_time,
            "distance": ekf_distance,
            "wheel_distance": wheel_distance,
            "projection": projection,
            "cross_track": cross_track,
            "start": start,
            "end": end,
            "final_sample_count": len(final),
            "final_peak_mps": final_peak,
            "target_peak_mps": target_peak,
            "measured_peak_mps": measured_peak,
            "target_mean": target_mean,
            "measured_mean": measured_mean,
            "pwm_mean": pwm_mean,
            "pwm_peak": pwm_peak,
            "encoder_delta": encoder_delta,
        }

    def run_roundtrip(self, vx, vy, duration_limit, distance_limit, pause):
        topology = self.monitored_stop(1.2)
        start_ekf = self.latest_pose("ekf")
        start_wheel = self.latest_pose("wheel")
        start_imu = self.imu[-1]

        outbound = self.run_leg(
            vx, vy, duration_limit, distance_limit, start_ekf[3], "outbound"
        )
        # No return command is issued unless outbound evidence and this complete
        # monitored stop both pass. Any exception propagates directly to cleanup.
        self.monitored_stop(pause)
        turn_ekf = self.latest_pose("ekf")
        turn_wheel = self.latest_pose("wheel")

        inbound = self.run_leg(
            -vx,
            -vy,
            duration_limit,
            distance_limit,
            start_ekf[3],
            "return",
        )
        self.monitored_stop(1.2)
        end_ekf = self.latest_pose("ekf")
        end_wheel = self.latest_pose("wheel")
        end_imu = self.imu[-1]

        return_residual = math.hypot(end_ekf[1] - start_ekf[1], end_ekf[2] - start_ekf[2])
        return_yaw = math.degrees(angle_delta(end_ekf[3], start_ekf[3]))
        imu_yaw = math.degrees(angle_delta(end_imu[1], start_imu[1]))
        return {
            "topology": topology,
            "command": {"vx": vx, "vy": vy},
            "start": {"ekf": pose_dict(start_ekf), "wheel": pose_dict(start_wheel)},
            "turn": {"ekf": pose_dict(turn_ekf), "wheel": pose_dict(turn_wheel)},
            "end": {"ekf": pose_dict(end_ekf), "wheel": pose_dict(end_wheel)},
            "outbound": {
                key: value for key, value in outbound.items()
                if key not in ("start", "end", "start_time", "end_time")
            },
            "return": {
                key: value for key, value in inbound.items()
                if key not in ("start", "end", "start_time", "end_time")
            },
            "return_residual_m": return_residual,
            "return_yaw_residual_deg": return_yaw,
            "imu_yaw_residual_deg": imu_yaw,
            "third_residual_compensation": False,
            "physical_direction_observation_required": True,
            "final_debug": list(self.debug[-1][1]),
        }

    def run_blocked_test(self, vx, vy, seconds=1.0):
        topology = self.monitored_stop(1.2, require_final=False)
        marker_window = self.require_stable_stop_zone()
        start_ekf = self.latest_pose("ekf")
        initial_debug = self.debug[-1][1]
        if (
            max(abs(initial_debug[i]) for i in range(3)) > ZERO_CMD_THRESHOLD
            or max(abs(initial_debug[3 + i]) for i in range(3))
            > ZERO_MEASURED_THRESHOLD
            or max(abs(initial_debug[6 + i]) for i in range(3))
            > ZERO_PWM_THRESHOLD
        ):
            raise SafetyAbort("non-zero target/measured/PWM before blocked test")

        request_start_time = time.monotonic()
        start_debug_index = len(self.debug)
        start_smoothed_index = len(self.smoothed)
        start_final_index = len(self.final)
        first_nonzero_smoothed_time = None
        deadline = request_start_time + seconds
        next_chain_check = request_start_time
        while rclpy.ok() and time.monotonic() < deadline:
            self.raise_if_stop_requested()
            self.publish(vx, vy)
            rclpy.spin_once(self, timeout_sec=0.02)
            now = time.monotonic()
            self.assert_fresh(
                ("smoothed cmd",), control_grace_start=request_start_time
            )
            if now >= next_chain_check:
                self.check_safety_chain()
                next_chain_check = now + TOPOLOGY_CHECK_INTERVAL_S
            if (
                self.last_collision_time is None
                or now - self.last_collision_time > STREAM_MAX_AGE_S
                or self.collision_point_count < STOP_ZONE_MIN_POINTS
            ):
                raise SafetyAbort("StopZone obstacle disappeared during blocked test")

            recent_smoothed = self.smoothed[start_smoothed_index:]
            if first_nonzero_smoothed_time is None:
                first_nonzero = next(
                    (
                        sample for sample in recent_smoothed
                        if command_peak(sample) > NONZERO_CMD_THRESHOLD
                    ),
                    None,
                )
                if first_nonzero is not None:
                    first_nonzero_smoothed_time = first_nonzero[0]

            causal_final = [
                sample for sample in self.final[start_final_index:]
                if first_nonzero_smoothed_time is not None
                and sample[0] >= first_nonzero_smoothed_time
            ]
            if any(command_peak(sample) > ZERO_CMD_THRESHOLD for sample in causal_final):
                raise SafetyAbort("Collision Monitor emitted non-zero final velocity")
            if (
                first_nonzero_smoothed_time is not None
                and not causal_final
                and now - first_nonzero_smoothed_time > CONTROL_STREAM_MAX_AGE_S
            ):
                raise SafetyAbort(
                    "no new final /cmd_vel after non-zero smoothed request"
                )
            recent_debug = [sample for _, sample in self.debug[start_debug_index:]]
            if any(
                max(abs(sample[i]) for i in range(3)) > ZERO_CMD_THRESHOLD
                or max(abs(sample[6 + i]) for i in range(3)) > ZERO_PWM_THRESHOLD
                for sample in recent_debug
            ):
                raise SafetyAbort("ESP32 received non-zero target/PWM during blocked test")
            current_ekf = self.latest_pose("ekf")
            displacement = math.hypot(
                current_ekf[1] - start_ekf[1], current_ekf[2] - start_ekf[2]
            )
            if displacement > 0.005:
                raise SafetyAbort(
                    f"unexpected blocked-test displacement {displacement:.4f} m"
                )

        # Freeze the request window before cleanup zero messages can arrive.
        request_end_time = time.monotonic()
        end_smoothed_index = len(self.smoothed)
        end_final_index = len(self.final)
        end_debug_index = len(self.debug)
        end_ekf = self.latest_pose("ekf")
        smoothed = [
            sample for sample in self.smoothed[start_smoothed_index:end_smoothed_index]
            if request_start_time <= sample[0] <= request_end_time
        ]
        final = [
            sample for sample in self.final[start_final_index:end_final_index]
            if request_start_time <= sample[0] <= request_end_time
        ]
        debug = [
            sample for stamp, sample in self.debug[start_debug_index:end_debug_index]
            if request_start_time <= stamp <= request_end_time
        ]
        smoothed_peak = max(
            (command_peak(sample) for sample in smoothed), default=0.0
        )
        causal_final = [
            sample for sample in final
            if first_nonzero_smoothed_time is not None
            and sample[0] >= first_nonzero_smoothed_time
        ]
        final_peak = max((command_peak(sample) for sample in causal_final), default=0.0)
        target_peak = max(
            (max(abs(sample[i]) for i in range(3)) for sample in debug), default=0.0
        )
        measured_peak = max(
            (max(abs(sample[3 + i]) for i in range(3)) for sample in debug), default=0.0
        )
        pwm_peak = max(
            (max(abs(sample[6 + i]) for i in range(3)) for sample in debug), default=0.0
        )
        displacement = math.hypot(end_ekf[1] - start_ekf[1], end_ekf[2] - start_ekf[2])
        if first_nonzero_smoothed_time is None or smoothed_peak <= NONZERO_CMD_THRESHOLD:
            raise SafetyAbort("upstream request did not reach /cmd_vel_smoothed")
        if not causal_final:
            raise SafetyAbort(
                "blocked test received no new final /cmd_vel after non-zero request"
            )
        if not debug:
            raise SafetyAbort("blocked test received no chassis debug samples")
        if (
            final_peak > ZERO_CMD_THRESHOLD
            or target_peak > ZERO_CMD_THRESHOLD
            or pwm_peak > ZERO_PWM_THRESHOLD
        ):
            raise SafetyAbort("Collision Monitor failed to hold the final chain at zero")
        if displacement > 0.005:
            raise SafetyAbort(f"unexpected blocked-test displacement {displacement:.4f} m")
        return {
            "topology": topology,
            "marker_stability": marker_window,
            "collision_markers": self.collision_marker_count,
            "collision_points": self.collision_point_count,
            "observation_points": self.observation_point_count,
            "requested": {"vx": vx, "vy": vy},
            "request_window_s": request_end_time - request_start_time,
            "smoothed_peak_mps": smoothed_peak,
            "causal_final_zero_samples": len(causal_final),
            "final_peak_mps": final_peak,
            "target_peak_mps": target_peak,
            "measured_peak_mps": measured_peak,
            "pwm_peak": pwm_peak,
            "ekf_displacement_m": displacement,
            "final_debug": list(self.debug[-1][1]),
        }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--topic", default=UPSTREAM_TOPIC)
    parser.add_argument("--vx", type=float, default=0.0)
    parser.add_argument("--vy", type=float, default=0.0)
    parser.add_argument("--duration", type=float, default=0.8)
    parser.add_argument("--distance", type=float, default=0.04)
    parser.add_argument("--pause", type=float, default=1.5)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--zero-only", action="store_true")
    mode.add_argument("--expect-blocked", action="store_true")
    args = parser.parse_args()

    if args.topic != UPSTREAM_TOPIC:
        parser.error(f"--topic must be {UPSTREAM_TOPIC}; bypass topics are forbidden")
    speed = math.hypot(args.vx, args.vy)
    if not args.zero_only and not 0.0 < speed <= 0.1001:
        parser.error("combined translation speed must be in (0, 0.10] m/s")
    if not 0.1 <= args.duration <= 1.5:
        parser.error("duration must be in [0.1, 1.5] seconds")
    if not 0.01 <= args.distance <= 0.15:
        parser.error("distance must be in [0.01, 0.15] metres")
    if not 1.0 <= args.pause <= 2.0:
        parser.error("pause must be in [1.0, 2.0] seconds")
    if args.expect_blocked and (
        abs(args.vx - 0.05) > 1e-6 or abs(args.vy) > 1e-6
    ):
        parser.error("--expect-blocked requires --vx 0.05 --vy 0")

    if SignalHandlerOptions is None:
        rclpy.init()
    else:
        rclpy.init(signal_handler_options=SignalHandlerOptions.NO)
    node = SafeRoundTrip(args.topic)
    installed_handlers = {}

    def handle_stop_signal(signum, _frame):
        node.request_stop(signum)

    for signal_name in ("SIGINT", "SIGTERM", "SIGHUP"):
        if hasattr(signal, signal_name):
            sig = getattr(signal, signal_name)
            installed_handlers[sig] = signal.getsignal(sig)
            signal.signal(sig, handle_stop_signal)

    result = None
    status = "FAILED"
    try:
        safety_chain = node.wait_ready()
        if args.zero_only:
            final_start_index = len(node.final)
            safety_chain = node.monitored_stop(
                STOP_BURST_SECONDS, require_final=False
            )
            new_final = node.final[final_start_index:]
            if any(command_peak(sample) > ZERO_CMD_THRESHOLD for sample in new_final):
                raise SafetyAbort("non-zero final /cmd_vel during zero-only test")
            latest = node.debug[-1][1]
            node.assert_chassis_stopped()
            result = {
                "safety_chain": safety_chain,
                "collision_markers": node.collision_marker_count,
                "collision_points": node.collision_point_count,
                "observation_points": node.observation_point_count,
                "new_final_zero_samples": len(new_final),
                "final_debug": list(latest),
            }
        elif args.expect_blocked:
            result = node.run_blocked_test(args.vx, args.vy, seconds=1.0)
        else:
            result = node.run_roundtrip(
                args.vx, args.vy, args.duration, args.distance, args.pause
            )
        status = "SUCCESS"
    except (KeyboardInterrupt, Exception) as exc:
        result = {"error": f"{type(exc).__name__}: {exc}"}
    finally:
        cleanup_error = None
        try:
            node.zero_for(STOP_BURST_SECONDS, ignore_stop=True)
        except Exception as exc:
            cleanup_error = f"{type(exc).__name__}: {exc}"
            status = "FAILED"
            if not isinstance(result, dict):
                result = {}
            result["cleanup_error"] = cleanup_error
        finally:
            for sig, old_handler in installed_handlers.items():
                signal.signal(sig, old_handler)
            print(f"STATUS={status}")
            print("RESULT=" + json.dumps(result, ensure_ascii=False, sort_keys=True))
            node.destroy_node()
            if rclpy.ok():
                rclpy.shutdown()

    if status != "SUCCESS":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
