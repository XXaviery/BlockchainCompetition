#!/usr/bin/env python3
"""One-shot raised chassis diagnostic and direct-wheel A->B->A supervisor.

The diagnostic mode is the only mode that creates a velocity publisher.  Gate
and navigation modes publish only the relative-controller heartbeat and call
its standard Trigger services.
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
from geometry_msgs.msg import Twist
from lifecycle_msgs.msg import State
from lifecycle_msgs.srv import GetState
from nav_msgs.msg import Odometry
from rclpy.node import Node
from rclpy.signals import SignalHandlerOptions
from std_msgs.msg import Empty, Float64MultiArray, String
from std_srvs.srv import Trigger
from tf2_msgs.msg import TFMessage


# Run above the required 20 Hz so graph/serial checks cannot pull the recorded
# supervisor timeline below the evidence floor.
RATE_HZ = 25.0
PERIOD_S = 1.0 / RATE_HZ
NONZERO = 0.005
ZERO_TARGET = 0.001
ZERO_MEASURED = 0.005
ZERO_PWM = 0.5
SATURATION_PWM = 245.0
LOGICAL_TO_PHYSICAL = {
    "M1": "rear (180 deg)",
    "M2": "right-front (300 deg)",
    "M3": "left-front (60 deg)",
}


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
    return f"{namespace}/{endpoint.node_name}" if namespace else f"/{endpoint.node_name}"


def sample_rate(samples):
    if len(samples) < 2:
        return 0.0
    elapsed = samples[-1][0] - samples[0][0]
    return (len(samples) - 1) / elapsed if elapsed > 0.0 else 0.0


class DirectWheelSupervisor(Node):
    def __init__(self, mode, timeline_path):
        super().__init__("ros_direct_wheel_basic_supervisor")
        self.mode = mode
        self.started = time.monotonic()
        self.timeline_file = open(timeline_path, "w", encoding="utf-8", buffering=1)
        self.boot_id = self._read_text("/proc/sys/kernel/random/boot_id")
        self.serial_identity = None
        self.stop_signal = None
        self.requested = (0.0, 0.0, 0.0)
        self.nav = []
        self.final = []
        self.debug = []
        self.wheel = []
        self.status = []
        self.results = []
        self.map_odom_events = []
        self.last_seen = {}
        self.start_calls = 0
        self.return_calls = 0
        self.cancel_calls = 0
        self.first_nav_nonzero = None
        self.first_final_nonzero = None
        self.stall_since = [None, None, None]
        self.saturation_since = [None, None, None]
        self.sign_mismatch_since = [None, None, None]
        self.overspeed_since = [None, None, None]
        self.previous_wheel = None
        self.wheel_jump_fault = None

        self.command_pub = None
        if mode in ("raised-diagnostic", "raised-rotation"):
            self.command_pub = self.create_publisher(Twist, "/cmd_vel_nav", 20)
        self.heartbeat_pub = None
        if mode in ("static-gate", "execute"):
            self.heartbeat_pub = self.create_publisher(
                Empty, "/relative_navigation/heartbeat", 20
            )

        self.create_subscription(Twist, "/cmd_vel_nav", self._nav_cb, 50)
        self.create_subscription(Twist, "/cmd_vel", self._final_cb, 50)
        self.create_subscription(
            Float64MultiArray, "/chassis/debug", self._debug_cb, 50
        )
        self.create_subscription(Odometry, "/wheel/odom", self._wheel_cb, 100)
        self.create_subscription(
            String, "/relative_navigation/status", self._status_cb, 50
        )
        self.create_subscription(
            String, "/relative_navigation/result", self._result_cb, 10
        )
        self.create_subscription(TFMessage, "/tf", self._tf_cb, 100)

        self.lifecycle = self.create_client(
            GetState, "/velocity_smoother/get_state"
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

    @staticmethod
    def _read_text(path):
        try:
            with open(path, "r", encoding="utf-8") as handle:
                return handle.read().strip()
        except OSError:
            return None

    @staticmethod
    def _twist_sample(msg):
        return (
            time.monotonic(),
            float(msg.linear.x),
            float(msg.linear.y),
            float(msg.angular.z),
        )

    @staticmethod
    def _pose_sample(msg):
        return (
            time.monotonic(),
            float(msg.pose.pose.position.x),
            float(msg.pose.pose.position.y),
            yaw_from_quaternion(msg.pose.pose.orientation),
        )

    def _nav_cb(self, msg):
        sample = self._twist_sample(msg)
        self.nav.append(sample)
        self.last_seen["nav"] = sample[0]
        if self.first_nav_nonzero is None and max(abs(v) for v in sample[1:]) > NONZERO:
            self.first_nav_nonzero = sample[0]

    def _final_cb(self, msg):
        sample = self._twist_sample(msg)
        self.final.append(sample)
        self.last_seen["final"] = sample[0]
        if self.first_final_nonzero is None and max(abs(v) for v in sample[1:]) > NONZERO:
            self.first_final_nonzero = sample[0]

    def _debug_cb(self, msg):
        if len(msg.data) < 15:
            return
        now = time.monotonic()
        self.debug.append((now, tuple(float(v) for v in msg.data[:15])))
        self.last_seen["debug"] = now

    def _wheel_cb(self, msg):
        sample = self._pose_sample(msg)
        if self.previous_wheel is not None:
            step = math.hypot(
                sample[1] - self.previous_wheel[1],
                sample[2] - self.previous_wheel[2],
            )
            yaw_step = abs(normalize_angle(sample[3] - self.previous_wheel[3]))
            if step > 0.030:
                self.wheel_jump_fault = f"wheel odom step {step:.6f} m"
            elif yaw_step > math.radians(3.0):
                self.wheel_jump_fault = (
                    f"wheel odom yaw step {math.degrees(yaw_step):.3f} deg"
                )
        self.previous_wheel = sample
        self.wheel.append(sample)
        self.last_seen["wheel"] = sample[0]

    def _status_cb(self, msg):
        now = time.monotonic()
        try:
            value = json.loads(msg.data)
        except json.JSONDecodeError as exc:
            value = {"parse_error": str(exc), "raw": msg.data}
        self.status.append((now, value))
        self.last_seen["status"] = now

    def _result_cb(self, msg):
        try:
            value = json.loads(msg.data)
        except json.JSONDecodeError as exc:
            value = {"parse_error": str(exc), "raw": msg.data}
        self.results.append((time.monotonic(), value))

    def _tf_cb(self, msg):
        for transform in msg.transforms:
            if transform.header.frame_id == "map" and transform.child_frame_id == "odom":
                self.map_odom_events.append({
                    "monotonic": time.monotonic(),
                    "translation": [
                        transform.transform.translation.x,
                        transform.transform.translation.y,
                    ],
                    "yaw": yaw_from_quaternion(transform.transform.rotation),
                })

    def request_stop(self, signum, _frame=None):
        if self.stop_signal is None:
            try:
                self.stop_signal = signal.Signals(signum).name
            except ValueError:
                self.stop_signal = str(signum)

    def publish_command(self, vx, vy=0.0, wz=0.0):
        if self.command_pub is None:
            raise SafetyFailure("this mode is not authorized to publish velocity")
        msg = Twist()
        msg.linear.x = float(vx)
        msg.linear.y = float(vy)
        msg.angular.z = float(wz)
        self.command_pub.publish(msg)
        self.requested = (msg.linear.x, msg.linear.y, msg.angular.z)

    def publish_heartbeat(self):
        if self.heartbeat_pub is not None:
            self.heartbeat_pub.publish(Empty())

    def spin_tick(self, phase, command=None):
        tick = time.monotonic()
        if command is not None:
            self.publish_command(*command)
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
        debug = self.debug[-1][1] if self.debug else None
        row = {
            "monotonic": time.monotonic(),
            "elapsed_s": time.monotonic() - self.started,
            "phase": phase,
            "requested": list(self.requested),
            "cmd_vel_nav": self._latest(self.nav),
            "cmd_vel": self._latest(self.final),
            "target": list(debug[0:3]) if debug else None,
            "measured": list(debug[3:6]) if debug else None,
            "pwm": list(debug[6:9]) if debug else None,
            "encoder": list(debug[9:12]) if debug else None,
            "wheel_distance_m": list(debug[12:15]) if debug else None,
            "wheel_odom": self._latest(self.wheel),
            "relative_status": self.status[-1][1] if self.status else None,
            "map_to_odom_count": len(self.map_odom_events),
            "stop_signal": self.stop_signal,
        }
        self.timeline_file.write(json.dumps(row, sort_keys=True) + "\n")

    def endpoint_summary(self, topic):
        return {
            "publishers": sorted(
                endpoint_name(item) for item in self.get_publishers_info_by_topic(topic)
            ),
            "subscriptions": sorted(
                endpoint_name(item) for item in self.get_subscriptions_info_by_topic(topic)
            ),
        }

    def check_graph(self, require_recorder):
        observer = self.get_fully_qualified_name()
        nav_publisher = (
            observer if self.mode.startswith("raised-") else "/relative_navigation_node"
        )
        expected = {
            "/cmd_vel_nav": ({nav_publisher}, {"/velocity_smoother", observer}),
            "/cmd_vel": ({"/velocity_smoother"}, {"/esp32_cmd_vel_bridge", observer}),
        }
        graph = {}
        for topic, (publishers_expected, subscriptions_required) in expected.items():
            summary = self.endpoint_summary(topic)
            graph[topic] = summary
            if set(summary["publishers"]) != publishers_expected:
                raise SafetyFailure(f"publisher topology mismatch {topic}: {summary}")
            required = set(subscriptions_required)
            if require_recorder:
                required.add("/rosbag2_recorder")
            if not required.issubset(set(summary["subscriptions"])):
                raise SafetyFailure(f"subscriber topology mismatch {topic}: {summary}")
            permitted = required | {"/rosbag2_recorder"}
            unexpected = set(summary["subscriptions"]) - permitted
            if unexpected:
                raise SafetyFailure(f"unexpected subscriber {topic}: {summary}")
        if not self.mode.startswith("raised-"):
            heartbeat = self.endpoint_summary("/relative_navigation/heartbeat")
            if heartbeat["publishers"] != [observer]:
                raise SafetyFailure(f"heartbeat publisher mismatch: {heartbeat}")
        for forbidden_topic in ("/odom", "/imu", "/scan", "/cmd_vel_smoothed"):
            if self.endpoint_summary(forbidden_topic)["publishers"]:
                raise SafetyFailure(f"forbidden publisher exists on {forbidden_topic}")
        return graph

    def check_nodes(self, require_recorder):
        names = [
            f"{namespace.rstrip('/')}/{name}" if namespace.rstrip("/") else f"/{name}"
            for name, namespace in self.get_node_names_and_namespaces()
        ]
        expected = {
            "/esp32_cmd_vel_bridge",
            "/velocity_smoother",
            self.get_fully_qualified_name(),
        }
        if not self.mode.startswith("raised-"):
            expected.add("/relative_navigation_node")
        if require_recorder:
            expected.add("/rosbag2_recorder")
        # launch_ros creates one helper node for the lifecycle transition event
        # handlers.  It has no velocity endpoints and is tied to this launch PID.
        launch_helpers = [name for name in names if name.startswith("/launch_ros_")]
        if len(launch_helpers) != 1:
            raise SafetyFailure(f"launch_ros helper count mismatch: {launch_helpers}")
        expected.update(launch_helpers)
        missing = sorted(expected - set(names))
        duplicates = sorted(name for name in expected if names.count(name) != 1)
        forbidden_terms = (
            "ekf", "imu_bias", "slam", "amcl", "map_server", "controller_server",
            "planner_server", "collision_monitor", "lidar", "laser", "manual", "wasd",
        )
        forbidden = sorted(
            name for name in names if any(term in name.lower() for term in forbidden_terms)
        )
        unexpected = sorted(set(names) - expected)
        if missing or duplicates or forbidden or unexpected:
            raise SafetyFailure(json.dumps({
                "missing": missing,
                "duplicates": duplicates,
                "forbidden": forbidden,
                "unexpected": unexpected,
                "all": sorted(names),
            }, sort_keys=True))
        return sorted(names)

    def check_lifecycle(self):
        if not self.lifecycle.wait_for_service(timeout_sec=1.0):
            raise SafetyFailure("velocity_smoother lifecycle service unavailable")
        future = self.lifecycle.call_async(GetState.Request())
        response = self.wait_future(future, 1.0, "velocity_smoother lifecycle")
        state = response.current_state
        if state.id != State.PRIMARY_STATE_ACTIVE or state.label != "active":
            raise SafetyFailure(f"velocity_smoother is not active: {state.id}/{state.label}")
        return {"id": int(state.id), "label": state.label}

    def serial_owners(self):
        stable = "/dev/mof_esp32"
        if not os.path.islink(stable):
            raise SafetyFailure("stable serial link is absent")
        resolved = os.path.realpath(stable)
        if resolved not in ("/dev/ttyUSB0", "/dev/ttyUSB1"):
            raise SafetyFailure(f"unexpected stable serial target: {resolved}")
        st = os.stat(resolved)
        identity = {
            "resolved": resolved,
            "rdev": int(st.st_rdev),
            "inode": int(st.st_ino),
            "ctime_ns": int(st.st_ctime_ns),
            "sysfs": os.path.realpath(f"/sys/class/tty/{os.path.basename(resolved)}/device"),
        }
        if self.serial_identity is None:
            self.serial_identity = identity
        elif self.serial_identity != identity:
            raise SafetyFailure(
                f"serial identity changed: {self.serial_identity} -> {identity}"
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
        if len(owners) != 1 or "esp32_cmd_vel_bridge" not in next(iter(owners.values()), ""):
            raise SafetyFailure(f"serial owner mismatch: {owners}")
        return {"identity": identity, "owners": owners}

    def check_boot(self):
        current = self._read_text("/proc/sys/kernel/random/boot_id")
        if current != self.boot_id:
            raise SafetyFailure(f"Raspberry Pi boot ID changed: {self.boot_id} -> {current}")

    def wait_future(self, future, timeout, description):
        deadline = time.monotonic() + timeout
        while rclpy.ok() and not future.done() and time.monotonic() < deadline:
            self.publish_heartbeat()
            rclpy.spin_once(self, timeout_sec=0.02)
        if not future.done():
            raise SafetyFailure(f"{description} timed out")
        if future.exception() is not None:
            raise SafetyFailure(f"{description} failed: {future.exception()}")
        return future.result()

    def call_trigger(self, client, name):
        if not client.wait_for_service(timeout_sec=2.0):
            raise SafetyFailure(f"service unavailable: {name}")
        if name.endswith("start_ab_a"):
            self.start_calls += 1
            if self.start_calls > 1:
                raise SafetyFailure("start would be called more than once")
        elif name.endswith("continue_return"):
            self.return_calls += 1
            if self.return_calls > 1:
                raise SafetyFailure("return would be called more than once")
        else:
            self.cancel_calls += 1
        response = self.wait_future(client.call_async(Trigger.Request()), 2.0, name)
        return {"success": bool(response.success), "message": response.message}

    def wait_ready(self, require_recorder, timeout=15.0):
        deadline = time.monotonic() + timeout
        last_error = "not ready"
        while time.monotonic() < deadline:
            command = (0.0, 0.0, 0.0) if self.mode.startswith("raised-") else None
            self.spin_tick("readiness", command)
            try:
                now = time.monotonic()
                required = {"nav": 0.30, "final": 0.30, "debug": 0.30, "wheel": 0.30}
                if not self.mode.startswith("raised-"):
                    required["status"] = 0.30
                stale = {
                    key: now - self.last_seen.get(key, 0.0)
                    for key, limit in required.items()
                    if now - self.last_seen.get(key, 0.0) > limit
                }
                if stale:
                    raise SafetyFailure(f"stale streams: {stale}")
                self.assert_latest_zero()
                if not self.mode.startswith("raised-"):
                    if not self.status or self.status[-1][1].get("state") != "IDLE":
                        raise SafetyFailure("relative controller is not IDLE")
                    for client, name in (
                        (self.start_client, "start"),
                        (self.return_client, "return"),
                        (self.cancel_client, "cancel"),
                    ):
                        if not client.wait_for_service(timeout_sec=0.1):
                            raise SafetyFailure(f"{name} service unavailable")
                return {
                    "graph": self.check_graph(require_recorder),
                    "nodes": self.check_nodes(require_recorder),
                    "lifecycle": self.check_lifecycle(),
                    "serial": self.serial_owners(),
                    "boot_id": self.boot_id,
                }
            except SafetyFailure as exc:
                last_error = str(exc)
        raise SafetyFailure(last_error)

    def assert_latest_zero(self):
        if not self.debug:
            raise SafetyFailure("missing chassis debug")
        values = self.debug[-1][1]
        if max(abs(v) for v in values[0:3]) > ZERO_TARGET:
            raise SafetyFailure(f"target is not zero: {values[0:3]}")
        if max(abs(v) for v in values[3:6]) > ZERO_MEASURED:
            raise SafetyFailure(f"measured is not zero: {values[3:6]}")
        if max(abs(v) for v in values[6:9]) > ZERO_PWM:
            raise SafetyFailure(f"PWM is not zero: {values[6:9]}")
        for name, samples in (("cmd_vel_nav", self.nav), ("cmd_vel", self.final)):
            if not samples or max(abs(v) for v in samples[-1][1:]) > 1e-6:
                raise SafetyFailure(f"{name} is absent or nonzero")

    def check_wheels(self, now):
        values = self.debug[-1][1]
        for index in range(3):
            target = values[index]
            measured = values[3 + index]
            pwm = values[6 + index]
            active = abs(target) >= 0.04
            if active and abs(measured) < ZERO_MEASURED:
                self.stall_since[index] = self.stall_since[index] or now
                if now - self.stall_since[index] >= 0.30:
                    raise SafetyFailure(f"M{index + 1} feedback/stall for 0.30 s")
            else:
                self.stall_since[index] = None
            if active and abs(pwm) >= SATURATION_PWM and abs(measured) < 0.5 * abs(target):
                self.saturation_since[index] = self.saturation_since[index] or now
                if now - self.saturation_since[index] >= 0.30:
                    raise SafetyFailure(f"M{index + 1} PWM saturation with severe under-speed")
            else:
                self.saturation_since[index] = None
            if active and measured * target < 0.0 and abs(measured) >= ZERO_MEASURED:
                self.sign_mismatch_since[index] = self.sign_mismatch_since[index] or now
                if now - self.sign_mismatch_since[index] >= 0.15:
                    raise SafetyFailure(f"M{index + 1} measured direction opposes target")
            else:
                self.sign_mismatch_since[index] = None
            speed_limit = max(0.30, 1.8 * abs(target))
            if active and abs(measured) > speed_limit:
                self.overspeed_since[index] = self.overspeed_since[index] or now
                if now - self.overspeed_since[index] >= 0.10:
                    raise SafetyFailure(f"M{index + 1} measured overspeed {measured:.4f} m/s")
            else:
                self.overspeed_since[index] = None

    def check_runtime(self, require_recorder, navigation, deep=False):
        now = time.monotonic()
        if self.stop_signal:
            raise SafetyFailure("received " + self.stop_signal)
        self.check_boot()
        if self.map_odom_events:
            raise SafetyFailure("map->odom appeared")
        if navigation and self.wheel_jump_fault:
            raise SafetyFailure(self.wheel_jump_fault)
        for key in ("nav", "final", "debug", "wheel"):
            age = now - self.last_seen.get(key, 0.0)
            if age > 0.30:
                raise SafetyFailure(f"{key} stale for {age:.3f} s")
        if navigation:
            status_age = now - self.last_seen.get("status", 0.0)
            if status_age > 0.30:
                raise SafetyFailure(f"status stale for {status_age:.3f} s")
            value = self.status[-1][1]
            if value.get("state") == "ABORTED":
                raise SafetyFailure("controller ABORTED: " + str(value.get("abort_reason")))
            lateral = value.get("lateral_error_m")
            radius = value.get("radius_from_a_m")
            current = value.get("current_odom")
            a_pose = value.get("a")
            if lateral is not None and abs(lateral) > 0.050:
                raise SafetyFailure(f"cross-track exceeded: {lateral}")
            if radius is not None and radius > 0.250:
                raise SafetyFailure(f"radius from A exceeded: {radius}")
            if current and a_pose:
                yaw = abs(normalize_angle(current["yaw_rad"] - a_pose["yaw_rad"]))
                if yaw > math.radians(8.0):
                    raise SafetyFailure(f"yaw from A exceeded: {math.degrees(yaw):.3f} deg")
        if self.first_nav_nonzero is not None and self.first_final_nonzero is None:
            if now - self.first_nav_nonzero > 0.5:
                raise SafetyFailure("nonzero /cmd_vel_nav did not reach /cmd_vel")
        self.check_wheels(now)
        if not deep:
            return None
        return {
            "graph": self.check_graph(require_recorder),
            "nodes": self.check_nodes(require_recorder),
            "lifecycle": self.check_lifecycle(),
            "serial": self.serial_owners(),
        }

    def zero_tail(self, seconds=2.2):
        deadline = time.monotonic() + seconds
        while rclpy.ok() and time.monotonic() < deadline:
            command = (0.0, 0.0, 0.0) if self.command_pub is not None else None
            self.spin_tick("zero_tail", command)

    def run_raised_test(self, command, duration, test_kind):
        preflight = self.wait_ready(require_recorder=True)
        baseline_start = len(self.debug)
        baseline_deadline = time.monotonic() + 1.0
        while time.monotonic() < baseline_deadline:
            self.spin_tick("raised_zero_baseline", (0.0, 0.0, 0.0))
            self.assert_latest_zero()
        encoder_start = list(self.debug[-1][1][9:12])
        command_started = time.monotonic()
        motion_ended = command_started
        next_deep = command_started
        first_failure = None
        try:
            while time.monotonic() - command_started < duration:
                self.spin_tick("raised_" + test_kind, command)
                now = time.monotonic()
                deep = now >= next_deep
                self.check_runtime(
                    require_recorder=True, navigation=False, deep=deep
                )
                if deep:
                    next_deep = now + 0.25
                target = self.debug[-1][1][0:3]
                if test_kind == "plus_x" and self.final and abs(self.final[-1][1]) >= 0.15:
                    if (
                        abs(target[0]) > 0.020
                        or target[1] < 0.040
                        or target[2] > -0.040
                        or abs(target[1] + target[2]) > 0.020
                    ):
                        raise SafetyFailure(f"+X target pattern abnormal: {target}")
                if test_kind == "rotation" and self.final and abs(self.final[-1][3]) >= 0.20:
                    expected_sign = -1.0 if self.final[-1][3] > 0.0 else 1.0
                    signed = [expected_sign * value for value in target]
                    if min(signed) < 0.020 or max(target) - min(target) > 0.020:
                        raise SafetyFailure(f"rotation target pattern abnormal: {target}")
        except SafetyFailure as exc:
            first_failure = str(exc)
        finally:
            motion_ended = time.monotonic()
            self.zero_tail(2.2)
        self.assert_latest_zero()
        debug_slice = self.debug[baseline_start:]
        peak = lambda start, end: [
            max((abs(row[1][index]) for row in debug_slice), default=0.0)
            for index in range(start, end)
        ]
        return {
            "schema": "mof_direct_wheel_raised_diagnostic_v2",
            "success": first_failure is None,
            "status": "DATA_PASS_PENDING_PHYSICAL" if first_failure is None else "FAILED",
            "first_failure": first_failure,
            "preflight": preflight,
            "test_kind": test_kind,
            "command_duration_s": min(duration, motion_ended - command_started),
            "requested": list(command),
            "logical_to_physical": LOGICAL_TO_PHYSICAL,
            "target_peak_abs": peak(0, 3),
            "measured_peak_abs": peak(3, 6),
            "pwm_peak_abs": peak(6, 9),
            "encoder_start": encoder_start,
            "encoder_final": list(self.debug[-1][1][9:12]),
            "final_debug": list(self.debug[-1][1]),
            "physical_observation_required": True,
        }

    def run_static_gate(self, duration):
        preflight = self.wait_ready(require_recorder=False)
        starts = {"debug": len(self.debug), "wheel": len(self.wheel), "nav": len(self.nav), "final": len(self.final)}
        started = time.monotonic()
        next_deep = started
        while time.monotonic() - started < duration:
            self.spin_tick("static_gate")
            self.assert_latest_zero()
            if self.status[-1][1].get("state") != "IDLE":
                raise SafetyFailure("relative controller left IDLE during Gate")
            if time.monotonic() >= next_deep:
                self.check_runtime(
                    require_recorder=False, navigation=True, deep=True
                )
                next_deep = time.monotonic() + 0.25
        debug = self.debug[starts["debug"]:]
        wheel = self.wheel[starts["wheel"]:]
        nav = self.nav[starts["nav"]:]
        final = self.final[starts["final"]:]
        enc_delta = [debug[-1][1][9 + i] - debug[0][1][9 + i] for i in range(3)]
        displacement = math.hypot(wheel[-1][1] - wheel[0][1], wheel[-1][2] - wheel[0][2])
        yaw_span = math.degrees(max(v[3] for v in wheel) - min(v[3] for v in wheel))
        failures = []
        if sample_rate(debug) < 18.0 or sample_rate(wheel) < 18.0:
            failures.append(f"rates debug={sample_rate(debug):.3f}, wheel={sample_rate(wheel):.3f}")
        if any(abs(v) > 0.0 for v in enc_delta):
            failures.append(f"encoder delta={enc_delta}")
        if displacement > 0.002 or yaw_span > 0.1:
            failures.append(f"wheel odom displacement={displacement}, yaw_span={yaw_span}")
        for name, samples in (("cmd_vel_nav", nav), ("cmd_vel", final)):
            if not samples or max(max(abs(v) for v in row[1:]) for row in samples) > 1e-9:
                failures.append(f"nonzero or missing {name}")
        if failures:
            raise SafetyFailure("; ".join(failures))
        return {
            "schema": "mof_direct_wheel_static_gate_v1",
            "success": True,
            "duration_s": time.monotonic() - started,
            "preflight": preflight,
            "rates": {"debug_hz": sample_rate(debug), "wheel_odom_hz": sample_rate(wheel)},
            "encoder_delta": enc_delta,
            "wheel_displacement_m": displacement,
            "wheel_yaw_span_deg": yaw_span,
            "final_debug": list(self.debug[-1][1]),
        }

    def motion_summary(self):
        value = self.status[-1][1]
        return {
            "state": value.get("state"),
            "a": value.get("a"),
            "b": value.get("b"),
            "current_odom": value.get("current_odom"),
            "position_error_m": value.get("position_error_m"),
            "lateral_error_m": value.get("lateral_error_m"),
            "yaw_error_deg": value.get("yaw_error_deg"),
            "radius_from_a_m": value.get("radius_from_a_m"),
            "leg_elapsed_s": value.get("leg_elapsed_s"),
            "final_debug": list(self.debug[-1][1]),
        }

    def run_execute(self):
        preflight = self.wait_ready(require_recorder=True)
        pre_zero = {"debug": list(self.debug[-1][1]), "wheel": list(self.wheel[-1][1:])}
        start_response = self.call_trigger(self.start_client, "/relative_navigation/start_ab_a")
        if not start_response["success"]:
            raise SafetyFailure(f"start rejected: {start_response}")
        next_deep = time.monotonic()
        while True:
            self.spin_tick("outbound")
            now = time.monotonic()
            deep = now >= next_deep
            self.check_runtime(
                require_recorder=True, navigation=True, deep=deep
            )
            if deep:
                next_deep = now + 0.25
            state = self.status[-1][1].get("state")
            if state == "WAIT_RETURN_CONFIRM":
                break
        outbound = self.motion_summary()
        if (
            outbound["position_error_m"] is None
            or outbound["position_error_m"] > 0.025
            or abs(outbound["lateral_error_m"] or 0.0) > 0.030
            or abs(outbound["yaw_error_deg"] or 0.0) > 5.0
        ):
            raise SafetyFailure(f"outbound acceptance failed: {outbound}")
        self.assert_latest_zero()
        print("OUTBOUND_RESULT=" + json.dumps(outbound, sort_keys=True), flush=True)
        print("WAITING_RETURN_CONFIRM: type literal return or cancel", flush=True)
        while True:
            self.spin_tick("wait_return_confirm")
            now = time.monotonic()
            deep = now >= next_deep
            self.check_runtime(
                require_recorder=True, navigation=True, deep=deep
            )
            if deep:
                next_deep = now + 0.25
            self.assert_latest_zero()
            readable, _, _ = select.select([sys.stdin], [], [], 0.0)
            if not readable:
                continue
            line = sys.stdin.readline()
            if line == "":
                raise SafetyFailure("stdin closed before return permission")
            choice = line.strip()
            if choice == "cancel":
                raise SafetyFailure("return denied by operator")
            if choice == "return":
                break
            print("CONTROL_REJECTED: expected exact return or cancel", flush=True)
        return_response = self.call_trigger(
            self.return_client, "/relative_navigation/continue_return"
        )
        if not return_response["success"]:
            raise SafetyFailure(f"return rejected: {return_response}")
        next_deep = time.monotonic()
        while True:
            self.spin_tick("return")
            now = time.monotonic()
            deep = now >= next_deep
            self.check_runtime(
                require_recorder=True, navigation=True, deep=deep
            )
            if deep:
                next_deep = now + 0.25
            if self.status[-1][1].get("state") == "SUCCEEDED":
                break
        self.zero_tail(2.2)
        self.assert_latest_zero()
        final = self.motion_summary()
        status = self.status[-1][1]
        if status.get("radius_from_a_m") is None or status["radius_from_a_m"] > 0.040:
            raise SafetyFailure(f"return residual failed: {final}")
        current = status.get("current_odom")
        a_pose = status.get("a")
        yaw_residual = abs(math.degrees(normalize_angle(current["yaw_rad"] - a_pose["yaw_rad"])))
        if yaw_residual > 5.0:
            raise SafetyFailure(f"return yaw residual {yaw_residual:.3f} deg")
        return {
            "schema": "mof_direct_wheel_ab_a_result_v1",
            "success": True,
            "preflight": preflight,
            "pre_start_zero": pre_zero,
            "start_response": start_response,
            "outbound": outbound,
            "return_response": return_response,
            "return": final,
            "controller_result": self.results[-1][1] if self.results else None,
            "service_calls": {"start": self.start_calls, "return": self.return_calls, "cancel": self.cancel_calls},
            "logical_to_physical": LOGICAL_TO_PHYSICAL,
        }

    def cancel_and_zero(self, reason):
        response = None
        if not self.mode.startswith("raised-"):
            try:
                response = self.call_trigger(self.cancel_client, "/relative_navigation/cancel")
            except Exception as exc:
                response = {"error": f"{type(exc).__name__}: {exc}"}
        try:
            self.zero_tail(2.2)
        except Exception:
            pass
        return {"reason": reason, "cancel_response": response, "final_debug": list(self.debug[-1][1]) if self.debug else None}

    def close(self):
        self.timeline_file.flush()
        os.fsync(self.timeline_file.fileno())
        self.timeline_file.close()


def write_json(path, value):
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")


def main():
    parser = argparse.ArgumentParser()
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--raised-diagnostic", action="store_true")
    modes.add_argument("--raised-rotation", action="store_true")
    modes.add_argument("--static-gate", action="store_true")
    modes.add_argument("--execute", action="store_true")
    parser.add_argument("--duration", type=float, default=5.0)
    parser.add_argument("--motion-duration", type=float, default=4.0)
    parser.add_argument("--timeline", required=True)
    parser.add_argument("--result", required=True)
    args = parser.parse_args()
    if args.static_gate and not 5.0 <= args.duration <= 5.2:
        parser.error("the one formal static Gate must be 5.0 to 5.2 seconds")
    if args.raised_rotation and not 4.0 <= args.motion_duration <= 4.2:
        parser.error("the authorized raised rotation must be 4.0 to 4.2 seconds")
    mode = (
        "raised-diagnostic" if args.raised_diagnostic
        else "raised-rotation" if args.raised_rotation
        else "static-gate" if args.static_gate
        else "execute"
    )

    rclpy.init(signal_handler_options=SignalHandlerOptions.NO)
    node = DirectWheelSupervisor(mode, args.timeline)
    old_handlers = {}
    for name in ("SIGINT", "SIGTERM", "SIGHUP"):
        if hasattr(signal, name):
            signum = getattr(signal, name)
            old_handlers[signum] = signal.getsignal(signum)
            signal.signal(signum, node.request_stop)
    result = None
    status = "FAILED"
    try:
        if mode == "raised-diagnostic":
            result = node.run_raised_test(
                (0.20, 0.0, 0.0), min(3.0, args.motion_duration), "plus_x"
            )
            if not result["success"]:
                raise SafetyFailure(result["first_failure"])
        elif mode == "raised-rotation":
            result = node.run_raised_test(
                (0.0, 0.0, 0.35), args.motion_duration, "rotation"
            )
            if not result["success"]:
                raise SafetyFailure(result["first_failure"])
        elif mode == "static-gate":
            result = node.run_static_gate(args.duration)
        else:
            result = node.run_execute()
        status = "SUCCESS"
    except BaseException as exc:
        reason = f"{type(exc).__name__}: {exc}"
        cleanup = node.cancel_and_zero(reason)
        result = {
            "schema": "mof_direct_wheel_supervisor_failure_v1",
            "success": False,
            "mode": mode,
            "first_failure": reason,
            "cleanup": cleanup,
            "start_calls": node.start_calls,
            "return_calls": node.return_calls,
            "cancel_calls": node.cancel_calls,
            "nonzero_final_observed": node.first_final_nonzero is not None,
            "last_status": node.status[-1][1] if node.status else None,
            "logical_to_physical": LOGICAL_TO_PHYSICAL,
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
