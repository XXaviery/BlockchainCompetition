#!/usr/bin/env python3
"""Safety supervisor for one wheel-only relative A->B->A run.

The supervisor publishes only ``std_msgs/Empty`` heartbeats.  All velocity
messages are observed, never produced here.  In execute mode it waits for the
literal line ``return`` on stdin after B; EOF or any signal requests cancel.
"""

import argparse
import glob
import json
import math
import os
import select
import signal
import sys
import time

import rclpy
from diagnostic_msgs.msg import DiagnosticArray
from geometry_msgs.msg import Twist
from lifecycle_msgs.msg import State
from lifecycle_msgs.srv import GetState
from nav_msgs.msg import Odometry
from rcl_interfaces.srv import GetParameters, GetParameterTypes
from rclpy.node import Node
from rclpy.signals import SignalHandlerOptions
from sensor_msgs.msg import Imu, LaserScan
from std_msgs.msg import Empty, Float64MultiArray, String
from std_srvs.srv import Trigger
from tf2_msgs.msg import TFMessage
from visualization_msgs.msg import Marker, MarkerArray


RATE_HZ = 20.0
PERIOD_S = 1.0 / RATE_HZ
ACTIVE_ID = State.PRIMARY_STATE_ACTIVE
EXPECTED_WHEEL_VECTOR = [
    False, False, False, False, False, False,
    True, True, False, False, False, True,
    False, False, False,
]
ZERO_TARGET_MPS = 0.001
ZERO_MEASURED_MPS = 0.005
ZERO_PWM = 0.5
NONZERO_CMD = 0.005
ODOM_STEP_M = 0.030
ODOM_STEP_YAW_RAD = math.radians(3.0)
MAX_YAW_FROM_A_RAD = math.radians(8.0)
MAX_CROSS_TRACK_M = 0.050
MAX_RADIUS_M = 0.250
MAX_PWM = 245.0

BAG_TOPICS = [
    "/cmd_vel_nav",
    "/cmd_vel_smoothed",
    "/cmd_vel",
    "/chassis/debug",
    "/wheel/odom",
    "/odom",
    "/imu",
    "/scan",
    "/scan/timing",
    "/tf",
    "/tf_static",
    "/diagnostics",
    "/collision_monitor/collision_points_marker",
    "/collision_monitor/stop_zone",
    "/relative_navigation/heartbeat",
    "/relative_navigation/status",
    "/relative_navigation/result",
]


class SafetyFailure(RuntimeError):
    pass


def normalize_angle(value):
    return math.atan2(math.sin(value), math.cos(value))


def yaw_from_quaternion(q):
    return math.atan2(
        2.0 * (q.w * q.z + q.x * q.y),
        1.0 - 2.0 * (q.y * q.y + q.z * q.z),
    )


def endpoint_name(endpoint):
    namespace = endpoint.node_namespace.rstrip("/")
    return (
        f"{namespace}/{endpoint.node_name}"
        if namespace
        else f"/{endpoint.node_name}"
    )


def sample_rate(samples):
    if len(samples) < 2:
        return 0.0
    duration = samples[-1][0] - samples[0][0]
    return (len(samples) - 1) / duration if duration > 0.0 else 0.0


def diagnostic_level(value):
    if isinstance(value, (bytes, bytearray)):
        return int(value[0]) if value else 0
    return int(value)


def pose_span(samples):
    if not samples:
        return {
            "count": 0,
            "displacement_m": None,
            "path_length_m": None,
            "yaw_span_deg": None,
            "yaw_delta_deg": None,
        }
    start = samples[0]
    end = samples[-1]
    return {
        "count": len(samples),
        "start": list(start[1:4]),
        "end": list(end[1:4]),
        "displacement_m": math.hypot(end[1] - start[1], end[2] - start[2]),
        "path_length_m": sum(
            math.hypot(b[1] - a[1], b[2] - a[2])
            for a, b in zip(samples, samples[1:])
        ),
        "yaw_span_deg": math.degrees(
            max(item[3] for item in samples) - min(item[3] for item in samples)
        ),
        "yaw_delta_deg": math.degrees(normalize_angle(end[3] - start[3])),
    }


class RelativeNavigationSupervisor(Node):
    def __init__(self, timeline_path):
        super().__init__("ros_relative_ab_a_supervisor")
        self.started_monotonic = time.monotonic()
        self.boot_id = self._read_text("/proc/sys/kernel/random/boot_id")
        self.timeline_path = timeline_path
        self.timeline_file = open(timeline_path, "w", encoding="utf-8", buffering=1)
        self.heartbeat_pub = self.create_publisher(
            Empty, "/relative_navigation/heartbeat", 20
        )
        self.start_client = self.create_client(
            Trigger, "/relative_navigation/start_ab_a"
        )
        self.return_client = self.create_client(
            Trigger, "/relative_navigation/continue_return"
        )
        self.cancel_client = self.create_client(
            Trigger, "/relative_navigation/cancel"
        )
        self.lifecycle_clients = {
            name: self.create_client(GetState, f"/{name}/get_state")
            for name in ("velocity_smoother", "collision_monitor")
        }
        self.ekf_parameters = self.create_client(
            GetParameters, "/ekf_filter_node/get_parameters"
        )
        self.ekf_parameter_types = self.create_client(
            GetParameterTypes, "/ekf_filter_node/get_parameter_types"
        )

        self.cmd_nav = []
        self.cmd_smoothed = []
        self.cmd_final = []
        self.debug = []
        self.wheel = []
        self.odom = []
        self.imu = []
        self.scan = []
        self.scan_timing = []
        self.diagnostics = []
        self.markers = []
        self.status = []
        self.results = []
        self.last_seen = {}
        self.map_odom_events = []
        self.odom_jump_fault = None
        self.marker_fault = None
        self.stop_signal = None
        self.cancel_calls = 0
        self.start_calls = 0
        self.return_calls = 0
        self.first_nav_nonzero = None
        self.first_smoothed_nonzero = None
        self.first_final_nonzero = None
        self.intercept_since = None
        self.serial_identity = None
        self.stall_since = [None, None, None]
        self.saturation_since = [None, None, None]

        self.create_subscription(Twist, "/cmd_vel_nav", self._nav_callback, 50)
        self.create_subscription(
            Twist, "/cmd_vel_smoothed", self._smoothed_callback, 50
        )
        self.create_subscription(Twist, "/cmd_vel", self._final_callback, 50)
        self.create_subscription(
            Float64MultiArray, "/chassis/debug", self._debug_callback, 50
        )
        self.create_subscription(
            Odometry, "/wheel/odom", self._wheel_callback, 50
        )
        self.create_subscription(Odometry, "/odom", self._odom_callback, 100)
        self.create_subscription(Imu, "/imu", self._imu_callback, 200)
        self.create_subscription(LaserScan, "/scan", self._scan_callback, 30)
        self.create_subscription(
            Float64MultiArray, "/scan/timing", self._scan_timing_callback, 30
        )
        self.create_subscription(
            DiagnosticArray, "/diagnostics", self._diagnostics_callback, 20
        )
        self.create_subscription(
            MarkerArray,
            "/collision_monitor/collision_points_marker",
            self._marker_callback,
            50,
        )
        self.create_subscription(
            String, "/relative_navigation/status", self._status_callback, 50
        )
        self.create_subscription(
            String, "/relative_navigation/result", self._result_callback, 10
        )
        self.create_subscription(TFMessage, "/tf", self._tf_callback, 200)

    @staticmethod
    def _read_text(path):
        try:
            with open(path, "r", encoding="utf-8") as handle:
                return handle.read().strip()
        except OSError:
            return None

    @staticmethod
    def _cmd_tuple(msg):
        return (
            time.monotonic(),
            float(msg.linear.x),
            float(msg.linear.y),
            float(msg.angular.z),
        )

    @staticmethod
    def _pose_tuple(msg):
        return (
            time.monotonic(),
            float(msg.pose.pose.position.x),
            float(msg.pose.pose.position.y),
            yaw_from_quaternion(msg.pose.pose.orientation),
        )

    def _nav_callback(self, msg):
        sample = self._cmd_tuple(msg)
        self.cmd_nav.append(sample)
        self.last_seen["cmd_vel_nav"] = sample[0]
        if self.first_nav_nonzero is None and math.hypot(sample[1], sample[2]) > NONZERO_CMD:
            self.first_nav_nonzero = sample[0]

    def _smoothed_callback(self, msg):
        sample = self._cmd_tuple(msg)
        self.cmd_smoothed.append(sample)
        self.last_seen["cmd_vel_smoothed"] = sample[0]
        if self.first_smoothed_nonzero is None and math.hypot(sample[1], sample[2]) > NONZERO_CMD:
            self.first_smoothed_nonzero = sample[0]

    def _final_callback(self, msg):
        sample = self._cmd_tuple(msg)
        self.cmd_final.append(sample)
        self.last_seen["cmd_vel"] = sample[0]
        if self.first_final_nonzero is None and math.hypot(sample[1], sample[2]) > NONZERO_CMD:
            self.first_final_nonzero = sample[0]

    def _debug_callback(self, msg):
        if len(msg.data) < 15:
            return
        now = time.monotonic()
        self.debug.append((now, tuple(float(value) for value in msg.data[:15])))
        self.last_seen["debug"] = now

    def _wheel_callback(self, msg):
        sample = self._pose_tuple(msg)
        self.wheel.append(sample)
        self.last_seen["wheel"] = sample[0]

    def _odom_callback(self, msg):
        sample = self._pose_tuple(msg)
        previous = self.odom[-1] if self.odom else None
        self.odom.append(sample)
        self.last_seen["odom"] = sample[0]
        if previous is not None:
            step = math.hypot(sample[1] - previous[1], sample[2] - previous[2])
            yaw_step = abs(normalize_angle(sample[3] - previous[3]))
            if step > ODOM_STEP_M:
                self.odom_jump_fault = f"odom single-step translation {step:.6f} m"
            elif yaw_step > ODOM_STEP_YAW_RAD:
                self.odom_jump_fault = (
                    f"odom single-step yaw {math.degrees(yaw_step):.3f} deg"
                )

    def _imu_callback(self, _msg):
        now = time.monotonic()
        self.imu.append((now,))
        self.last_seen["imu"] = now

    def _scan_callback(self, _msg):
        now = time.monotonic()
        self.scan.append((now,))
        self.last_seen["scan"] = now

    def _scan_timing_callback(self, msg):
        now = time.monotonic()
        self.scan_timing.append((now, tuple(float(v) for v in msg.data)))
        self.last_seen["scan_timing"] = now

    def _diagnostics_callback(self, msg):
        now = time.monotonic()
        levels = [diagnostic_level(item.level) for item in msg.status]
        self.diagnostics.append((now, levels))
        self.last_seen["diagnostics"] = now

    def _marker_callback(self, msg):
        now = time.monotonic()
        count = 0
        frame_errors = []
        for marker in msg.markers:
            if marker.action != Marker.ADD or marker.type != Marker.POINTS:
                continue
            if marker.header.frame_id != "base_footprint":
                frame_errors.append(marker.header.frame_id)
                continue
            pose_yaw = yaw_from_quaternion(marker.pose.orientation)
            c = math.cos(pose_yaw)
            s = math.sin(pose_yaw)
            for point in marker.points:
                x = marker.pose.position.x + c * point.x - s * point.y
                y = marker.pose.position.y + s * point.x + c * point.y
                if abs(x) <= 0.24 and abs(y) <= 0.24:
                    count += 1
        if frame_errors:
            self.marker_fault = f"invalid collision marker frames: {frame_errors}"
        self.markers.append((now, count, frame_errors))
        self.last_seen["markers"] = now

    def _status_callback(self, msg):
        now = time.monotonic()
        try:
            value = json.loads(msg.data)
        except json.JSONDecodeError as exc:
            value = {"parse_error": str(exc), "raw": msg.data}
        self.status.append((now, value))
        self.last_seen["status"] = now

    def _result_callback(self, msg):
        now = time.monotonic()
        try:
            value = json.loads(msg.data)
        except json.JSONDecodeError as exc:
            value = {"parse_error": str(exc), "raw": msg.data}
        self.results.append((now, value))

    def _tf_callback(self, msg):
        now = time.monotonic()
        self.last_seen["tf"] = now
        for transform in msg.transforms:
            if transform.header.frame_id == "map" and transform.child_frame_id == "odom":
                self.map_odom_events.append({
                    "monotonic": now,
                    "x": transform.transform.translation.x,
                    "y": transform.transform.translation.y,
                    "yaw": yaw_from_quaternion(transform.transform.rotation),
                })

    def request_stop(self, signum, _frame=None):
        if self.stop_signal is None:
            try:
                self.stop_signal = signal.Signals(signum).name
            except ValueError:
                self.stop_signal = str(signum)

    def _wait_future(self, future, timeout, description):
        deadline = time.monotonic() + timeout
        while rclpy.ok() and not future.done() and time.monotonic() < deadline:
            self.publish_heartbeat()
            rclpy.spin_once(self, timeout_sec=0.02)
        if not future.done():
            raise SafetyFailure(f"{description} timed out")
        exception = future.exception()
        if exception is not None:
            raise SafetyFailure(f"{description} failed: {exception}")
        return future.result()

    def call_trigger(self, client, name):
        if not client.wait_for_service(timeout_sec=2.0):
            raise SafetyFailure(f"service unavailable: {name}")
        if name.endswith("start_ab_a"):
            self.start_calls += 1
            if self.start_calls > 1:
                raise SafetyFailure("start service would be called more than once")
        elif name.endswith("continue_return"):
            self.return_calls += 1
            if self.return_calls > 1:
                raise SafetyFailure("return service would be called more than once")
        elif name.endswith("cancel"):
            self.cancel_calls += 1
        response = self._wait_future(
            client.call_async(Trigger.Request()), 2.0, name
        )
        return {"success": bool(response.success), "message": response.message}

    def publish_heartbeat(self):
        self.heartbeat_pub.publish(Empty())

    def spin_tick(self, phase):
        tick = time.monotonic()
        self.publish_heartbeat()
        deadline = tick + PERIOD_S
        while rclpy.ok() and time.monotonic() < deadline:
            rclpy.spin_once(
                self,
                timeout_sec=min(0.01, max(0.0, deadline - time.monotonic())),
            )
        self.write_timeline(phase)

    @staticmethod
    def _latest(samples):
        return list(samples[-1][1:]) if samples else None

    def write_timeline(self, phase):
        status = self.status[-1][1] if self.status else None
        row = {
            "monotonic": time.monotonic(),
            "elapsed_s": time.monotonic() - self.started_monotonic,
            "phase": phase,
            "cmd_vel_nav": self._latest(self.cmd_nav),
            "cmd_vel_smoothed": self._latest(self.cmd_smoothed),
            "cmd_vel": self._latest(self.cmd_final),
            "chassis_debug": self._latest(self.debug),
            "wheel_odom": self._latest(self.wheel),
            "odom": self._latest(self.odom),
            "collision_points": self.markers[-1][1] if self.markers else None,
            "relative_status": status,
            "map_to_odom_count": len(self.map_odom_events),
            "stop_signal": self.stop_signal,
        }
        self.timeline_file.write(json.dumps(row, sort_keys=True) + "\n")

    def endpoint_summary(self, topic):
        return {
            "publishers": sorted(
                endpoint_name(item)
                for item in self.get_publishers_info_by_topic(topic)
            ),
            "subscriptions": sorted(
                endpoint_name(item)
                for item in self.get_subscriptions_info_by_topic(topic)
            ),
        }

    def check_velocity_topology(self, require_recorder=False):
        observer = self.get_fully_qualified_name()
        expected = {
            "/cmd_vel_nav": (
                {"/relative_navigation_node"},
                {"/velocity_smoother", observer},
            ),
            "/cmd_vel_smoothed": (
                {"/velocity_smoother"},
                {"/collision_monitor", observer},
            ),
            "/cmd_vel": (
                {"/collision_monitor"},
                {"/esp32_cmd_vel_bridge", observer},
            ),
        }
        result = {}
        for topic, (expected_publishers, required_subscriptions) in expected.items():
            summary = self.endpoint_summary(topic)
            result[topic] = summary
            publishers = set(summary["publishers"])
            subscriptions = set(summary["subscriptions"])
            if publishers != expected_publishers:
                raise SafetyFailure(
                    f"publisher topology mismatch {topic}: {summary}"
                )
            required = set(required_subscriptions)
            if require_recorder:
                required.add("/rosbag2_recorder")
            if not required.issubset(subscriptions):
                raise SafetyFailure(
                    f"subscriber topology missing endpoint {topic}: {summary}"
                )
            permitted = required | {"/rosbag2_recorder"}
            if subscriptions - permitted:
                raise SafetyFailure(
                    f"unexpected subscriber topology {topic}: {summary}"
                )
        heartbeat = self.endpoint_summary("/relative_navigation/heartbeat")
        if heartbeat["publishers"] != [observer]:
            raise SafetyFailure(f"heartbeat publisher mismatch: {heartbeat}")
        return result

    def check_nodes(self, require_recorder=False):
        names = [
            f"{namespace.rstrip('/')}/{name}" if namespace.rstrip("/") else f"/{name}"
            for name, namespace in self.get_node_names_and_namespaces()
        ]
        expected = {
            "/relative_navigation_node",
            "/esp32_cmd_vel_bridge",
            "/ekf_filter_node",
            "/velocity_smoother",
            "/collision_monitor",
            "/lifecycle_manager_relative_navigation",
            "/base_to_imu_tf",
            "/footprint_to_link_tf",
            "/base_to_laser_tf",
            self.get_fully_qualified_name(),
        }
        if require_recorder:
            expected.add("/rosbag2_recorder")
        missing = sorted(expected - set(names))
        duplicates = sorted(name for name in expected if names.count(name) != 1)
        forbidden_terms = (
            "slam_toolbox",
            "/amcl",
            "map_server",
            "controller_server",
            "planner_server",
            "behavior_server",
            "bt_navigator",
            "waypoint_follower",
            "manual",
            "imu_bias_corrector",
        )
        forbidden = sorted(
            name for name in names if any(term in name for term in forbidden_terms)
        )
        if missing or duplicates or forbidden:
            raise SafetyFailure(
                "node topology mismatch: "
                + json.dumps({
                    "missing": missing,
                    "duplicates": duplicates,
                    "forbidden": forbidden,
                    "all": sorted(names),
                }, sort_keys=True)
            )
        corrected = self.endpoint_summary("/imu_corrected")
        if corrected["publishers"]:
            raise SafetyFailure(f"/imu_corrected has publisher: {corrected}")
        return sorted(names)

    def check_processes(self):
        found = []
        forbidden = []
        launch_count = 0
        allowed_pid_terms = (
            "mof_relative_navigation_wheel_only.launch.py",
            "relative_navigation_node",
            "esp32_cmd_vel_bridge",
            "ekf_node",
            "velocity_smoother",
            "collision_monitor",
            "lifecycle_manager",
            "static_transform_publisher",
            "ros_relative_ab_a_supervisor.py",
            "ros2 bag record",
            "rosbag2_recorder",
        )
        forbidden_terms = (
            "safe_velocity.launch.py",
            "manual_open_field",
            "mof_mapping",
            "mof_nav_with",
            "slam_toolbox",
            " amcl",
            "map_server",
            "controller_server",
            "planner_server",
            "behavior_server",
            "bt_navigator",
            "imu_bias_corrector",
        )
        for path in glob.glob("/proc/[0-9]*/cmdline"):
            try:
                raw = open(path, "rb").read().replace(b"\0", b" ").decode(
                    "utf-8", errors="replace"
                )
            except OSError:
                continue
            if not raw:
                continue
            if any(term in raw for term in allowed_pid_terms):
                found.append({"pid": int(path.split("/")[2]), "cmdline": raw})
            if "mof_relative_navigation_wheel_only.launch.py" in raw:
                launch_count += 1
            if any(term in raw for term in forbidden_terms):
                forbidden.append({"pid": int(path.split("/")[2]), "cmdline": raw})
        if launch_count != 1 or forbidden:
            raise SafetyFailure(
                f"process topology mismatch: launch_count={launch_count}, forbidden={forbidden}"
            )
        return found

    def serial_owners(self):
        stable = "/dev/mof_esp32"
        if not os.path.islink(stable):
            raise SafetyFailure("stable serial link /dev/mof_esp32 is absent")
        resolved = os.path.realpath(stable)
        if resolved not in ("/dev/ttyUSB0", "/dev/ttyUSB1"):
            raise SafetyFailure(f"stable serial link resolves unexpectedly: {resolved}")
        device_stat = os.stat(resolved)
        identity = {
            "resolved": resolved,
            "rdev": int(device_stat.st_rdev),
            "inode": int(device_stat.st_ino),
            "ctime_ns": int(device_stat.st_ctime_ns),
            "sysfs": os.path.realpath(
                f"/sys/class/tty/{os.path.basename(resolved)}/device"
            ),
        }
        if self.serial_identity is None:
            self.serial_identity = identity
        elif identity != self.serial_identity:
            raise SafetyFailure(
                f"serial identity changed: before={self.serial_identity}, now={identity}"
            )
        owners = {}
        for fd_path in glob.glob("/proc/[0-9]*/fd/*"):
            try:
                if os.path.realpath(fd_path) != resolved:
                    continue
                pid = int(fd_path.split("/")[2])
                cmdline = open(f"/proc/{pid}/cmdline", "rb").read().replace(
                    b"\0", b" "
                ).decode("utf-8", errors="replace")
                owners[pid] = cmdline
            except (OSError, ValueError):
                continue
        if len(owners) != 1:
            raise SafetyFailure(
                f"serial owner count is {len(owners)}, expected 1: {owners}"
            )
        if "esp32_cmd_vel_bridge" not in next(iter(owners.values())):
            raise SafetyFailure(f"serial owner is not bridge: {owners}")
        return {
            "stable": stable,
            "resolved": resolved,
            "identity": identity,
            "owners": owners,
        }

    def check_lifecycle(self):
        result = {}
        for name, client in self.lifecycle_clients.items():
            if not client.wait_for_service(timeout_sec=1.0):
                raise SafetyFailure(f"lifecycle service unavailable: {name}")
            response = self._wait_future(
                client.call_async(GetState.Request()), 1.0, f"lifecycle {name}"
            )
            state = response.current_state
            result[name] = {"id": int(state.id), "label": state.label}
            if state.id != ACTIVE_ID or state.label != "active":
                raise SafetyFailure(f"lifecycle not active: {result}")
        return result

    def check_ekf_parameters(self):
        if not self.ekf_parameters.wait_for_service(timeout_sec=2.0):
            raise SafetyFailure("EKF parameter service unavailable")
        names = [
            "odom0",
            "odom0_config",
            "frequency",
            "predict_to_current_time",
            "world_frame",
        ]
        request = GetParameters.Request()
        request.names = names
        response = self._wait_future(
            self.ekf_parameters.call_async(request), 2.0, "EKF parameters"
        )
        values = response.values
        if len(values) != len(names):
            raise SafetyFailure(
                f"EKF parameter response length {len(values)} != {len(names)}"
            )
        if not self.ekf_parameter_types.wait_for_service(timeout_sec=2.0):
            raise SafetyFailure("EKF parameter-types service unavailable")
        type_request = GetParameterTypes.Request()
        type_request.names = ["imu0"]
        type_response = self._wait_future(
            self.ekf_parameter_types.call_async(type_request),
            2.0,
            "EKF imu0 parameter type",
        )
        if len(type_response.types) != 1:
            raise SafetyFailure(
                f"EKF imu0 type response length {len(type_response.types)} != 1"
            )
        result = {
            "odom0": values[0].string_value,
            "odom0_config": list(values[1].bool_array_value),
            "imu0_type": int(type_response.types[0]),
            "frequency": values[2].double_value,
            "predict_to_current_time": values[3].bool_value,
            "world_frame": values[4].string_value,
        }
        if (
            result["odom0"] != "/wheel/odom"
            or result["odom0_config"] != EXPECTED_WHEEL_VECTOR
            or result["imu0_type"] != 0
            or abs(result["frequency"] - 50.0) > 1e-9
            or not result["predict_to_current_time"]
            or result["world_frame"] != "odom"
        ):
            raise SafetyFailure(f"EKF is not wheel-only: {result}")
        return result

    def check_recorder_subscriptions(self):
        missing = []
        for topic in BAG_TOPICS:
            summary = self.endpoint_summary(topic)
            if "/rosbag2_recorder" not in summary["subscriptions"]:
                missing.append({"topic": topic, "endpoints": summary})
        if missing:
            raise SafetyFailure(f"rosbag subscriptions missing: {missing}")

    def wait_ready(self, require_recorder=False, timeout=15.0):
        deadline = time.monotonic() + timeout
        last_error = "telemetry not ready"
        while time.monotonic() < deadline:
            self.spin_tick("readiness")
            try:
                now = time.monotonic()
                required = {
                    "cmd_vel_nav": 0.30,
                    "cmd_vel_smoothed": 0.30,
                    "debug": 0.30,
                    "wheel": 0.30,
                    "odom": 0.20,
                    "imu": 0.20,
                    "scan": 0.50,
                    "scan_timing": 0.50,
                    "diagnostics": 1.00,
                    "status": 0.30,
                    "tf": 0.30,
                }
                stale = {
                    name: now - self.last_seen.get(name, 0.0)
                    for name, limit in required.items()
                    if now - self.last_seen.get(name, 0.0) > limit
                }
                if stale:
                    raise SafetyFailure(f"readiness streams stale: {stale}")
                if not self.status or self.status[-1][1].get("state") != "IDLE":
                    raise SafetyFailure(
                        f"relative node not IDLE: {self.status[-1][1] if self.status else None}"
                    )
                self.assert_latest_zero()
                graph = self.check_velocity_topology(require_recorder)
                nodes = self.check_nodes(require_recorder)
                lifecycle = self.check_lifecycle()
                ekf = self.check_ekf_parameters()
                serial = self.serial_owners()
                processes = self.check_processes()
                if self.map_odom_events:
                    raise SafetyFailure("map->odom exists before Gate")
                if require_recorder:
                    self.check_recorder_subscriptions()
                return {
                    "graph": graph,
                    "nodes": nodes,
                    "lifecycle": lifecycle,
                    "ekf": ekf,
                    "serial": serial,
                    "processes": processes,
                }
            except SafetyFailure as exc:
                last_error = str(exc)
        raise SafetyFailure(last_error)

    def assert_latest_zero(self):
        if not self.debug:
            raise SafetyFailure("missing chassis debug")
        values = self.debug[-1][1]
        target = max(abs(value) for value in values[0:3])
        measured = max(abs(value) for value in values[3:6])
        pwm = max(abs(value) for value in values[6:9])
        if target > ZERO_TARGET_MPS or measured > ZERO_MEASURED_MPS or pwm > ZERO_PWM:
            raise SafetyFailure(
                f"chassis not zero: target={target}, measured={measured}, pwm={pwm}"
            )
        for name, samples in (
            ("cmd_vel_nav", self.cmd_nav),
            ("cmd_vel_smoothed", self.cmd_smoothed),
        ):
            if not samples or max(abs(value) for value in samples[-1][1:4]) > 1e-6:
                raise SafetyFailure(f"latest {name} is absent or nonzero")
        if self.cmd_final and max(abs(value) for value in self.cmd_final[-1][1:4]) > 1e-6:
            raise SafetyFailure("latest cmd_vel is nonzero")

    def check_runtime(self, require_recorder=True):
        now = time.monotonic()
        if self.stop_signal:
            raise SafetyFailure("received " + self.stop_signal)
        if self._read_text("/proc/sys/kernel/random/boot_id") != self.boot_id:
            raise SafetyFailure("Raspberry Pi boot ID changed")
        required = {
            "cmd_vel_nav": 0.30,
            "cmd_vel_smoothed": 0.30,
            "debug": 0.30,
            "wheel": 0.30,
            "odom": 0.30,
            "imu": 0.30,
            "scan": 0.60,
            "scan_timing": 0.60,
            "diagnostics": 1.00,
            "status": 0.30,
        }
        stale = {
            name: now - self.last_seen.get(name, 0.0)
            for name, limit in required.items()
            if now - self.last_seen.get(name, 0.0) > limit
        }
        if stale:
            raise SafetyFailure(f"runtime telemetry stale: {stale}")
        if self.map_odom_events:
            raise SafetyFailure("map->odom appeared during wheel-only run")
        if self.odom_jump_fault:
            raise SafetyFailure(self.odom_jump_fault)
        if self.marker_fault:
            raise SafetyFailure(self.marker_fault)
        if self.markers and self.markers[-1][1] > 0:
            raise SafetyFailure(
                f"StopZone contains {self.markers[-1][1]} point(s)"
            )
        if self.first_nav_nonzero is not None:
            if self.first_smoothed_nonzero is None and now - self.first_nav_nonzero > 0.5:
                raise SafetyFailure("nonzero nav command did not reach velocity smoother")
            if self.first_final_nonzero is None and now - self.first_nav_nonzero > 0.5:
                raise SafetyFailure(
                    "nonzero nav command did not produce causal final command; "
                    "Collision Monitor interception or chain failure"
                )
            nav_is_nonzero = bool(
                self.cmd_nav
                and math.hypot(self.cmd_nav[-1][1], self.cmd_nav[-1][2]) > NONZERO_CMD
            )
            if nav_is_nonzero and now - self.last_seen.get("cmd_vel", 0.0) > 0.30:
                raise SafetyFailure("final cmd_vel stale during nonzero motion")
        self.check_collision_causality(now)
        if self.status:
            value = self.status[-1][1]
            if value.get("state") == "ABORTED":
                raise SafetyFailure(
                    "controller ABORTED: " + str(value.get("abort_reason"))
                )
            lateral = value.get("lateral_error_m")
            radius = value.get("radius_from_a_m")
            current = value.get("current_odom")
            a_pose = value.get("a")
            if lateral is not None and abs(lateral) > MAX_CROSS_TRACK_M:
                raise SafetyFailure(f"cross-track exceeded: {lateral}")
            if radius is not None and radius > MAX_RADIUS_M:
                raise SafetyFailure(f"radius from A exceeded: {radius}")
            if current and a_pose:
                yaw_error = abs(normalize_angle(current["yaw_rad"] - a_pose["yaw_rad"]))
                if yaw_error > MAX_YAW_FROM_A_RAD:
                    raise SafetyFailure(
                        f"yaw from A exceeded: {math.degrees(yaw_error)} deg"
                    )
        values = self.debug[-1][1]
        for index in range(3):
            target = abs(values[index])
            measured = abs(values[3 + index])
            pwm = abs(values[6 + index])
            if target >= 0.04 and measured < ZERO_MEASURED_MPS:
                self.stall_since[index] = self.stall_since[index] or now
                if now - self.stall_since[index] >= 0.30:
                    raise SafetyFailure(f"wheel {index + 1} sustained stop")
            else:
                self.stall_since[index] = None
            if target >= 0.04 and pwm >= MAX_PWM and measured < 0.5 * target:
                self.saturation_since[index] = self.saturation_since[index] or now
                if now - self.saturation_since[index] >= 0.30:
                    raise SafetyFailure(f"wheel {index + 1} sustained PWM saturation")
            else:
                self.saturation_since[index] = None
        if max((max(levels, default=0) for _, levels in self.diagnostics[-20:]), default=0) > 1:
            raise SafetyFailure("diagnostics contains ERROR level")
        return {
            "topology": self.check_velocity_topology(require_recorder),
            "nodes": self.check_nodes(require_recorder),
            "lifecycle": self.check_lifecycle(),
            "serial": self.serial_owners(),
            "processes": self.check_processes(),
        }

    def check_collision_causality(self, now=None):
        now = time.monotonic() if now is None else now
        smoothed = (
            math.hypot(self.cmd_smoothed[-1][1], self.cmd_smoothed[-1][2])
            if self.cmd_smoothed else 0.0
        )
        final = (
            math.hypot(self.cmd_final[-1][1], self.cmd_final[-1][2])
            if self.cmd_final else 0.0
        )
        if smoothed > NONZERO_CMD and final <= NONZERO_CMD:
            self.intercept_since = self.intercept_since or now
            if now - self.intercept_since > 0.5:
                raise SafetyFailure(
                    "Collision Monitor suppressed a sustained nonzero command"
                )
        else:
            self.intercept_since = None

    def cancel_and_hold(self, reason, hold_s=2.2):
        response = None
        error = None
        try:
            response = self.call_trigger(
                self.cancel_client, "/relative_navigation/cancel"
            )
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
        deadline = time.monotonic() + hold_s
        while rclpy.ok() and time.monotonic() < deadline:
            self.spin_tick("cancel_zero_hold")
        zero_error = None
        try:
            self.assert_latest_zero()
        except Exception as exc:
            zero_error = f"{type(exc).__name__}: {exc}"
        return {
            "reason": reason,
            "cancel_response": response,
            "cancel_error": error,
            "zero_error": zero_error,
            "final_debug": list(self.debug[-1][1]) if self.debug else None,
        }

    def run_static_gate(self, duration_s):
        preflight = self.wait_ready(require_recorder=False)
        cancel_probe = self.call_trigger(
            self.cancel_client, "/relative_navigation/cancel"
        )
        if not cancel_probe["success"]:
            raise SafetyFailure(f"cancel service probe rejected: {cancel_probe}")
        starts = {
            "nav": len(self.cmd_nav),
            "smoothed": len(self.cmd_smoothed),
            "final": len(self.cmd_final),
            "debug": len(self.debug),
            "wheel": len(self.wheel),
            "odom": len(self.odom),
            "imu": len(self.imu),
            "scan": len(self.scan),
            "scan_timing": len(self.scan_timing),
            "diagnostics": len(self.diagnostics),
            "markers": len(self.markers),
            "status": len(self.status),
        }
        gate_started = time.monotonic()
        deadline = gate_started + duration_s
        next_topology = gate_started
        topology_samples = []
        while time.monotonic() < deadline:
            self.spin_tick("static_gate")
            now = time.monotonic()
            if self.stop_signal:
                raise SafetyFailure("received " + self.stop_signal)
            if now >= next_topology:
                topology_samples.append({
                    "time": now,
                    "velocity": self.check_velocity_topology(False),
                    "nodes": self.check_nodes(False),
                    "lifecycle": self.check_lifecycle(),
                    "serial": self.serial_owners(),
                    "processes": self.check_processes(),
                })
                next_topology = now + 0.5
            if self.map_odom_events:
                raise SafetyFailure("map->odom appeared during static Gate")
            if self.marker_fault:
                raise SafetyFailure(self.marker_fault)
            if self.status and self.status[-1][1].get("state") != "IDLE":
                raise SafetyFailure(f"state changed before start: {self.status[-1][1]}")

        gate_ended = time.monotonic()
        slices = {
            "nav": self.cmd_nav[starts["nav"]:],
            "smoothed": self.cmd_smoothed[starts["smoothed"]:],
            "final": self.cmd_final[starts["final"]:],
            "debug": self.debug[starts["debug"]:],
            "wheel": self.wheel[starts["wheel"]:],
            "odom": self.odom[starts["odom"]:],
            "imu": self.imu[starts["imu"]:],
            "scan": self.scan[starts["scan"]:],
            "scan_timing": self.scan_timing[starts["scan_timing"]:],
            "diagnostics": self.diagnostics[starts["diagnostics"]:],
            "markers": self.markers[starts["markers"]:],
            "status": self.status[starts["status"]:],
        }
        failures = []
        rates = {
            "wheel_odom_hz": sample_rate(slices["wheel"]),
            "odom_hz": sample_rate(slices["odom"]),
            "imu_hz": sample_rate(slices["imu"]),
            "scan_hz": sample_rate(slices["scan"]),
            "scan_timing_hz": sample_rate(slices["scan_timing"]),
            "debug_hz": sample_rate(slices["debug"]),
            "status_hz": sample_rate(slices["status"]),
        }
        limits = {
            "wheel_odom_hz": 18.0,
            "odom_hz": 45.0,
            "imu_hz": 90.0,
            "scan_hz": 9.0,
            "debug_hz": 18.0,
        }
        for name, minimum in limits.items():
            if rates[name] < minimum:
                failures.append(f"{name}={rates[name]:.3f} < {minimum}")
        wheel_span = pose_span(slices["wheel"])
        odom_span = pose_span(slices["odom"])
        for name, span in (("wheel", wheel_span), ("odom", odom_span)):
            if span["displacement_m"] is None or span["displacement_m"] > 0.002:
                failures.append(f"{name} displacement={span['displacement_m']}")
            if span["yaw_span_deg"] is None or span["yaw_span_deg"] > 0.1:
                failures.append(f"{name} yaw_span={span['yaw_span_deg']}")
        command_peaks = {
            name: max(
                (max(abs(value) for value in sample[1:4]) for sample in values),
                default=(0.0 if name == "cmd_vel" else float("inf")),
            )
            for name, values in (
                ("cmd_vel_nav", slices["nav"]),
                ("cmd_vel_smoothed", slices["smoothed"]),
                ("cmd_vel", slices["final"]),
            )
        }
        debug_values = [item[1] for item in slices["debug"]]
        target_peak = max(
            (max(abs(value) for value in row[0:3]) for row in debug_values),
            default=float("inf"),
        )
        measured_peak = max(
            (max(abs(value) for value in row[3:6]) for row in debug_values),
            default=float("inf"),
        )
        pwm_peak = max(
            (max(abs(value) for value in row[6:9]) for row in debug_values),
            default=float("inf"),
        )
        encoder_delta = (
            [debug_values[-1][9 + i] - debug_values[0][9 + i] for i in range(3)]
            if debug_values else None
        )
        if any(value > 1e-9 for value in command_peaks.values()):
            failures.append(f"nonzero velocity command peaks: {command_peaks}")
        if target_peak > ZERO_TARGET_MPS:
            failures.append(f"target peak={target_peak}")
        if measured_peak > ZERO_MEASURED_MPS:
            failures.append(f"measured peak={measured_peak}")
        if pwm_peak > ZERO_PWM:
            failures.append(f"PWM peak={pwm_peak}")
        if encoder_delta is None or any(abs(value) > 0.0 for value in encoder_delta):
            failures.append(f"encoder delta={encoder_delta}")
        marker_counts = [item[1] for item in slices["markers"]]
        if not marker_counts:
            failures.append("no qualified collision marker frames")
        elif any(count != 0 for count in marker_counts):
            failures.append(f"StopZone counts range={min(marker_counts), max(marker_counts)}")
        diag_max = max(
            (max(levels, default=0) for _, levels in slices["diagnostics"]),
            default=255,
        )
        if diag_max > 0:
            failures.append(f"diagnostics max level={diag_max}")
        result = {
            "schema": "mof_relative_navigation_static_gate_v1",
            "success": not failures,
            "duration_s": gate_ended - gate_started,
            "failures": failures,
            "preflight": preflight,
            "cancel_service_probe": cancel_probe,
            "rates": rates,
            "wheel": wheel_span,
            "odom": odom_span,
            "command_peaks": command_peaks,
            "target_peak_mps": target_peak,
            "measured_peak_mps": measured_peak,
            "pwm_peak": pwm_peak,
            "encoder_delta": encoder_delta,
            "stopzone_frame_count": len(marker_counts),
            "stopzone_min_max": (
                [min(marker_counts), max(marker_counts)] if marker_counts else None
            ),
            "diagnostics_max_level": diag_max,
            "map_to_odom_count": len(self.map_odom_events),
            "topology_samples": topology_samples,
            "final_debug": list(self.debug[-1][1]) if self.debug else None,
        }
        if failures:
            raise SafetyFailure(json.dumps(result, sort_keys=True))
        return result

    def _motion_summary(self, status_value, phase):
        return {
            "phase": phase,
            "state": status_value.get("state"),
            "a": status_value.get("a"),
            "b": status_value.get("b"),
            "current_odom": status_value.get("current_odom"),
            "position_error_m": status_value.get("position_error_m"),
            "lateral_error_m": status_value.get("lateral_error_m"),
            "yaw_error_deg": status_value.get("yaw_error_deg"),
            "leg_elapsed_s": status_value.get("leg_elapsed_s"),
            "cmd_vel_nav_peak": max(
                (math.hypot(item[1], item[2]) for item in self.cmd_nav), default=0.0
            ),
            "cmd_vel_smoothed_peak": max(
                (math.hypot(item[1], item[2]) for item in self.cmd_smoothed), default=0.0
            ),
            "cmd_vel_peak": max(
                (math.hypot(item[1], item[2]) for item in self.cmd_final), default=0.0
            ),
            "stopzone_min_max": (
                [min(item[1] for item in self.markers), max(item[1] for item in self.markers)]
                if self.markers else None
            ),
            "final_debug": list(self.debug[-1][1]) if self.debug else None,
        }

    def run_execute(self):
        preflight = self.wait_ready(require_recorder=True)
        zero_sample = {
            "debug": list(self.debug[-1][1]),
            "odom": list(self.odom[-1][1:4]),
            "wheel": list(self.wheel[-1][1:4]),
        }
        start_response = self.call_trigger(
            self.start_client, "/relative_navigation/start_ab_a"
        )
        if not start_response["success"]:
            raise SafetyFailure(f"start rejected: {start_response}")
        outbound_started = time.monotonic()
        next_deep_check = outbound_started
        while True:
            self.spin_tick("outbound")
            now = time.monotonic()
            deep = now >= next_deep_check
            if deep:
                self.check_runtime(require_recorder=True)
                next_deep_check = now + 0.25
            else:
                self.check_runtime_fast()
            state = self.status[-1][1].get("state")
            if state == "WAIT_RETURN_CONFIRM":
                break
            if now - outbound_started > 12.0:
                raise SafetyFailure("outbound supervisor timeout")
        outbound = self._motion_summary(self.status[-1][1], "outbound")
        if (
            outbound["position_error_m"] is None
            or outbound["position_error_m"] > 0.025
            or abs(outbound["lateral_error_m"] or 0.0) > 0.030
            or abs(outbound["yaw_error_deg"] or 0.0) > 5.0
        ):
            raise SafetyFailure(f"outbound acceptance failed: {outbound}")
        self.assert_latest_zero()
        print("OUTBOUND_RESULT=" + json.dumps(outbound, sort_keys=True), flush=True)
        print("WAITING_RETURN_CONFIRM: type return or cancel", flush=True)

        next_deep_check = time.monotonic()
        while True:
            self.spin_tick("wait_return_confirm")
            now = time.monotonic()
            if now >= next_deep_check:
                self.check_runtime(require_recorder=True)
                self.assert_latest_zero()
                next_deep_check = now + 0.25
            else:
                self.check_runtime_fast()
            readable, _, _ = select.select([sys.stdin], [], [], 0.0)
            if not readable:
                continue
            line = sys.stdin.readline()
            if line == "":
                raise SafetyFailure("supervisor stdin closed before return permission")
            choice = line.strip().lower()
            if choice == "cancel":
                raise SafetyFailure("return denied by operator")
            if choice == "return":
                break
            print("CONTROL_REJECTED: expected return or cancel", flush=True)

        return_response = self.call_trigger(
            self.return_client, "/relative_navigation/continue_return"
        )
        if not return_response["success"]:
            raise SafetyFailure(f"return rejected: {return_response}")
        return_started = time.monotonic()
        next_deep_check = return_started
        while True:
            self.spin_tick("return")
            now = time.monotonic()
            if now >= next_deep_check:
                self.check_runtime(require_recorder=True)
                next_deep_check = now + 0.25
            else:
                self.check_runtime_fast()
            state = self.status[-1][1].get("state")
            if state == "SUCCEEDED":
                break
            if now - return_started > 12.0:
                raise SafetyFailure("return supervisor timeout")
        final_hold_deadline = time.monotonic() + 2.2
        while time.monotonic() < final_hold_deadline:
            self.spin_tick("final_zero_hold")
            self.check_runtime_fast(allow_succeeded=True)
        self.assert_latest_zero()
        final_status = self.status[-1][1]
        final = self._motion_summary(final_status, "return")
        if (
            final_status.get("radius_from_a_m") is None
            or final_status["radius_from_a_m"] > 0.040
        ):
            raise SafetyFailure(f"return position residual failed: {final}")
        current = final_status.get("current_odom")
        a_pose = final_status.get("a")
        yaw_residual = (
            abs(math.degrees(normalize_angle(current["yaw_rad"] - a_pose["yaw_rad"])))
            if current and a_pose else float("inf")
        )
        if yaw_residual > 5.0:
            raise SafetyFailure(f"return yaw residual {yaw_residual} deg")
        return {
            "schema": "mof_relative_navigation_supervisor_result_v1",
            "success": True,
            "preflight": preflight,
            "pre_start_zero_sample": zero_sample,
            "start_response": start_response,
            "outbound": outbound,
            "return_response": return_response,
            "return": final,
            "controller_result": self.results[-1][1] if self.results else None,
            "service_call_counts": {
                "start": self.start_calls,
                "return": self.return_calls,
                "cancel": self.cancel_calls,
            },
            "final_debug": list(self.debug[-1][1]),
            "map_to_odom_count": len(self.map_odom_events),
        }

    def check_runtime_fast(self, allow_succeeded=False):
        now = time.monotonic()
        if self.stop_signal:
            raise SafetyFailure("received " + self.stop_signal)
        if self.map_odom_events:
            raise SafetyFailure("map->odom appeared during wheel-only run")
        if self.odom_jump_fault:
            raise SafetyFailure(self.odom_jump_fault)
        if self.marker_fault:
            raise SafetyFailure(self.marker_fault)
        if self.markers and self.markers[-1][1] > 0:
            raise SafetyFailure(f"StopZone contains {self.markers[-1][1]} point(s)")
        for name, limit in (
            ("cmd_vel_nav", 0.30),
            ("cmd_vel_smoothed", 0.30),
            ("debug", 0.30),
            ("wheel", 0.30),
            ("odom", 0.30),
            ("imu", 0.30),
            ("scan", 0.60),
            ("scan_timing", 0.60),
            ("status", 0.30),
        ):
            age = now - self.last_seen.get(name, 0.0)
            if age > limit:
                raise SafetyFailure(f"runtime {name} stale for {age:.3f} s")
        if self.status:
            state = self.status[-1][1].get("state")
            if state == "ABORTED":
                raise SafetyFailure(
                    "controller ABORTED: "
                    + str(self.status[-1][1].get("abort_reason"))
                )
            if state == "SUCCEEDED" and not allow_succeeded:
                return
        if self.first_nav_nonzero is not None and self.first_final_nonzero is None:
            if now - self.first_nav_nonzero > 0.5:
                raise SafetyFailure("final command missing after nonzero nav request")
        nav_is_nonzero = bool(
            self.cmd_nav
            and math.hypot(self.cmd_nav[-1][1], self.cmd_nav[-1][2]) > NONZERO_CMD
        )
        if nav_is_nonzero and now - self.last_seen.get("cmd_vel", 0.0) > 0.30:
            raise SafetyFailure("final cmd_vel stale during nonzero motion")
        self.check_collision_causality(now)

    def close(self):
        try:
            self.timeline_file.flush()
            os.fsync(self.timeline_file.fileno())
        finally:
            self.timeline_file.close()


def write_json(path, value):
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")


def main():
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--static-gate", action="store_true")
    mode.add_argument("--execute", action="store_true")
    parser.add_argument("--duration", type=float, default=15.0)
    parser.add_argument("--timeline", required=True)
    parser.add_argument("--result", required=True)
    args = parser.parse_args()
    if args.static_gate and not 15.0 <= args.duration <= 15.2:
        parser.error("the one formal static Gate duration must be 15.0 to 15.2 s")

    rclpy.init(signal_handler_options=SignalHandlerOptions.NO)
    node = RelativeNavigationSupervisor(args.timeline)
    old_handlers = {}
    for name in ("SIGINT", "SIGTERM", "SIGHUP"):
        if hasattr(signal, name):
            signum = getattr(signal, name)
            old_handlers[signum] = signal.getsignal(signum)
            signal.signal(signum, node.request_stop)
    status = "FAILED"
    result = None
    try:
        if args.static_gate:
            result = node.run_static_gate(args.duration)
        else:
            result = node.run_execute()
        status = "SUCCESS"
    except BaseException as exc:
        reason = f"{type(exc).__name__}: {exc}"
        cleanup = node.cancel_and_hold(reason)
        result = {
            "schema": "mof_relative_navigation_supervisor_failure_v1",
            "success": False,
            "first_failure": reason,
            "cleanup": cleanup,
            "start_calls": node.start_calls,
            "return_calls": node.return_calls,
            "cancel_calls": node.cancel_calls,
            "nonzero_motion_observed": node.first_final_nonzero is not None,
            "last_status": node.status[-1][1] if node.status else None,
            "controller_result": node.results[-1][1] if node.results else None,
            "map_to_odom_count": len(node.map_odom_events),
        }
    finally:
        try:
            write_json(args.result, result)
            print(f"STATUS={status}", flush=True)
            print("SUPERVISOR_RESULT=" + json.dumps(result, sort_keys=True), flush=True)
        finally:
            node.close()
            for signum, handler in old_handlers.items():
                signal.signal(signum, handler)
            node.destroy_node()
            if rclpy.ok():
                rclpy.shutdown()
    return 0 if status == "SUCCESS" else 2


if __name__ == "__main__":
    sys.exit(main())
