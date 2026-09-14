#!/usr/bin/env python3
"""One-shot wheel-only odometry-relative A->B->A controller.

This node never resets odometry and is the sole motion publisher on
``/cmd_vel_nav``.  A separate supervisor must provide the heartbeat and the
explicit permission boundary between the outbound and return legs.
"""

import json
import math
import signal
import threading
import time
from dataclasses import dataclass

import rclpy
from geometry_msgs.msg import Twist
from nav_msgs.msg import Odometry
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
from rclpy.signals import SignalHandlerOptions
from std_msgs.msg import Empty, String
from std_srvs.srv import Trigger


IDLE = "IDLE"
OUTBOUND = "OUTBOUND"
SETTLING_AT_B = "SETTLING_AT_B"
WAIT_RETURN_CONFIRM = "WAIT_RETURN_CONFIRM"
RETURN = "RETURN"
SUCCEEDED = "SUCCEEDED"
ABORTED = "ABORTED"

WATCHDOG_STATES = {OUTBOUND, SETTLING_AT_B, WAIT_RETURN_CONFIRM, RETURN}
MOVING_STATES = {OUTBOUND, RETURN}
TERMINAL_STATES = {SUCCEEDED, ABORTED}


def normalize_angle(value):
    return math.atan2(math.sin(value), math.cos(value))


def yaw_from_quaternion(q):
    return math.atan2(
        2.0 * (q.w * q.z + q.x * q.y),
        1.0 - 2.0 * (q.y * q.y + q.z * q.z),
    )


def rotate_body_to_world(x_body, y_body, yaw):
    c = math.cos(yaw)
    s = math.sin(yaw)
    return c * x_body - s * y_body, s * x_body + c * y_body


def rotate_world_to_body(x_world, y_world, yaw):
    c = math.cos(yaw)
    s = math.sin(yaw)
    return c * x_world + s * y_world, -s * x_world + c * y_world


def bounded_planar_command(error_x, error_y, gain, minimum_speed, maximum_speed):
    distance = math.hypot(error_x, error_y)
    if distance <= 0.0:
        return 0.0, 0.0
    requested = max(minimum_speed, min(maximum_speed, gain * distance))
    return requested * error_x / distance, requested * error_y / distance


@dataclass(frozen=True)
class PoseSample:
    receipt_monotonic: float
    ros_stamp_sec: float
    x: float
    y: float
    yaw: float

    def as_dict(self):
        return {
            "receipt_monotonic": self.receipt_monotonic,
            "ros_stamp_sec": self.ros_stamp_sec,
            "x": self.x,
            "y": self.y,
            "yaw_rad": self.yaw,
            "yaw_deg": math.degrees(self.yaw),
        }


class RelativeNavigationNode(Node):
    def __init__(self):
        super().__init__("relative_navigation_node")
        defaults = {
            "outbound_dx_m": 0.15,
            "outbound_dy_m": 0.0,
            "control_rate_hz": 20.0,
            "max_linear_speed_mps": 0.20,
            "min_effective_linear_speed_mps": 0.10,
            "max_angular_speed_radps": 0.20,
            "b_position_tolerance_m": 0.025,
            "return_position_tolerance_m": 0.040,
            "yaw_tolerance_rad": 0.0873,
            "yaw_hard_abort_rad": 0.1396,
            "cross_track_hard_abort_m": 0.050,
            "max_radius_from_a_m": 0.250,
            "overshoot_hard_abort_m": 0.050,
            "per_leg_timeout_s": 8.0,
            "b_settle_zero_duration_s": 2.0,
            "final_settle_zero_duration_s": 2.0,
            "heartbeat_timeout_s": 0.50,
            "odom_timeout_s": 0.30,
            "odom_step_translation_abort_m": 0.030,
            "odom_step_yaw_abort_rad": 0.05236,
            "linear_gain": 2.0,
            "angular_gain": 1.5,
            "shutdown_zero_duration_s": 2.0,
        }
        for name, value in defaults.items():
            self.declare_parameter(name, value)
        self.params = {
            name: float(self.get_parameter(name).value) for name in defaults
        }
        self._validate_parameters()

        self.command_publisher = self.create_publisher(Twist, "/cmd_vel_nav", 10)
        self.status_publisher = self.create_publisher(
            String, "/relative_navigation/status", 20
        )
        result_qos = QoSProfile(depth=1)
        result_qos.reliability = ReliabilityPolicy.RELIABLE
        result_qos.durability = DurabilityPolicy.TRANSIENT_LOCAL
        self.result_publisher = self.create_publisher(
            String, "/relative_navigation/result", result_qos
        )
        self.create_subscription(Odometry, "/odom", self._odom_callback, 100)
        self.create_subscription(
            Empty, "/relative_navigation/heartbeat", self._heartbeat_callback, 20
        )
        self.create_service(
            Trigger, "/relative_navigation/start_ab_a", self._start_callback
        )
        self.create_service(
            Trigger,
            "/relative_navigation/continue_return",
            self._continue_callback,
        )
        self.create_service(
            Trigger, "/relative_navigation/cancel", self._cancel_callback
        )

        self._lock = threading.RLock()
        self.state = IDLE
        self.state_since = time.monotonic()
        self.current_odom = None
        self.previous_odom = None
        self.last_heartbeat = None
        self.a_pose = None
        self.b_pose = None
        self.target_pose = None
        self.leg_start_pose = None
        self.leg_started = None
        self.leg_elapsed_s = 0.0
        self.settle_started = None
        self.return_settling = False
        self.abort_reason = ""
        self.pending_abort = None
        self.last_command = (0.0, 0.0, 0.0)
        self.max_radius_seen_m = 0.0
        self.max_cross_track_seen_m = 0.0
        self.max_abs_yaw_error_seen_rad = 0.0
        self.result_published = False
        self.shutdown_requested = None
        self._zero_tail_done = False
        self.timer = self.create_timer(
            1.0 / self.params["control_rate_hz"], self._control_tick
        )
        self.get_logger().info(
            "wheel-only relative navigation ready; launch does not auto-start motion"
        )

    def _validate_parameters(self):
        p = self.params
        required_positive = (
            "control_rate_hz",
            "max_linear_speed_mps",
            "min_effective_linear_speed_mps",
            "max_angular_speed_radps",
            "b_position_tolerance_m",
            "return_position_tolerance_m",
            "yaw_tolerance_rad",
            "yaw_hard_abort_rad",
            "cross_track_hard_abort_m",
            "max_radius_from_a_m",
            "overshoot_hard_abort_m",
            "per_leg_timeout_s",
            "b_settle_zero_duration_s",
            "final_settle_zero_duration_s",
            "heartbeat_timeout_s",
            "odom_timeout_s",
            "odom_step_translation_abort_m",
            "odom_step_yaw_abort_rad",
            "linear_gain",
            "angular_gain",
            "shutdown_zero_duration_s",
        )
        if any(not math.isfinite(p[name]) or p[name] <= 0.0 for name in required_positive):
            raise ValueError("all safety and controller limits must be finite and positive")
        if p["min_effective_linear_speed_mps"] > p["max_linear_speed_mps"]:
            raise ValueError("minimum effective speed exceeds maximum linear speed")
        if p["b_position_tolerance_m"] > 0.025:
            raise ValueError("B position tolerance must not exceed 0.025 m")
        if p["return_position_tolerance_m"] > 0.040:
            raise ValueError("return position tolerance must not exceed 0.040 m")
        if p["yaw_tolerance_rad"] > math.radians(5.01):
            raise ValueError("yaw tolerance must not exceed 5 degrees")
        if p["yaw_hard_abort_rad"] > math.radians(8.01):
            raise ValueError("yaw hard-abort must not exceed 8 degrees")
        if p["heartbeat_timeout_s"] > 0.50 or p["odom_timeout_s"] > 0.30:
            raise ValueError("watchdog timeouts exceed the authorized limits")
        if math.hypot(p["outbound_dx_m"], p["outbound_dy_m"]) <= 0.0:
            raise ValueError("outbound target must be nonzero")

    def _heartbeat_callback(self, _msg):
        with self._lock:
            self.last_heartbeat = time.monotonic()

    def _odom_callback(self, msg):
        now = time.monotonic()
        stamp = msg.header.stamp.sec + msg.header.stamp.nanosec * 1e-9
        sample = PoseSample(
            now,
            stamp,
            float(msg.pose.pose.position.x),
            float(msg.pose.pose.position.y),
            yaw_from_quaternion(msg.pose.pose.orientation),
        )
        with self._lock:
            previous = self.current_odom
            self.previous_odom = previous
            self.current_odom = sample
            if self.state in WATCHDOG_STATES and previous is not None:
                step = math.hypot(sample.x - previous.x, sample.y - previous.y)
                yaw_step = abs(normalize_angle(sample.yaw - previous.yaw))
                if step > self.params["odom_step_translation_abort_m"]:
                    self.pending_abort = (
                        f"odom single-step translation {step:.6f} m exceeds "
                        f"{self.params['odom_step_translation_abort_m']:.6f} m"
                    )
                elif yaw_step > self.params["odom_step_yaw_abort_rad"]:
                    self.pending_abort = (
                        f"odom single-step yaw {math.degrees(yaw_step):.3f} deg exceeds "
                        f"{math.degrees(self.params['odom_step_yaw_abort_rad']):.3f} deg"
                    )

    def _freshness_error(self, now):
        if self.last_heartbeat is None:
            return "heartbeat has never been received"
        heartbeat_age = now - self.last_heartbeat
        if heartbeat_age > self.params["heartbeat_timeout_s"]:
            return f"heartbeat stale for {heartbeat_age:.3f} s"
        if self.current_odom is None:
            return "odom has never been received"
        odom_age = now - self.current_odom.receipt_monotonic
        if odom_age > self.params["odom_timeout_s"]:
            return f"odom stale for {odom_age:.3f} s"
        return None

    def _start_callback(self, _request, response):
        with self._lock:
            now = time.monotonic()
            if self.state != IDLE:
                response.success = False
                response.message = f"start rejected in state {self.state}"
                return response
            freshness = self._freshness_error(now)
            if freshness:
                response.success = False
                response.message = "start rejected: " + freshness
                return response
            self.a_pose = self.current_odom
            dx_world, dy_world = rotate_body_to_world(
                self.params["outbound_dx_m"],
                self.params["outbound_dy_m"],
                self.a_pose.yaw,
            )
            self.b_pose = PoseSample(
                now,
                self.a_pose.ros_stamp_sec,
                self.a_pose.x + dx_world,
                self.a_pose.y + dy_world,
                self.a_pose.yaw,
            )
            self.target_pose = self.b_pose
            self.leg_start_pose = self.a_pose
            self.leg_started = now
            self.leg_elapsed_s = 0.0
            self.settle_started = None
            self.return_settling = False
            self.abort_reason = ""
            self.pending_abort = None
            self.max_radius_seen_m = 0.0
            self.max_cross_track_seen_m = 0.0
            self.max_abs_yaw_error_seen_rad = 0.0
            self._transition(OUTBOUND, now)
            response.success = True
            response.message = "A saved; one outbound leg started"
            return response

    def _continue_callback(self, _request, response):
        with self._lock:
            now = time.monotonic()
            if self.state != WAIT_RETURN_CONFIRM:
                response.success = False
                response.message = f"return rejected in state {self.state}"
                return response
            freshness = self._freshness_error(now)
            if freshness:
                response.success = False
                response.message = "return rejected: " + freshness
                return response
            self.target_pose = self.a_pose
            self.leg_start_pose = self.current_odom
            self.leg_started = now
            self.leg_elapsed_s = 0.0
            self.settle_started = None
            self.return_settling = False
            self._transition(RETURN, now)
            response.success = True
            response.message = "one return leg to the saved A pose started"
            return response

    def _cancel_callback(self, _request, response):
        with self._lock:
            if self.state in WATCHDOG_STATES:
                self._abort("cancel service requested")
                response.message = "active relative navigation cancelled"
            else:
                self._publish_zero()
                response.message = f"already nonmoving in state {self.state}"
            response.success = True
            return response

    def _transition(self, state, now=None):
        self.state = state
        self.state_since = time.monotonic() if now is None else now
        self.get_logger().info(f"relative navigation state={state}")

    def _publish_command(self, vx, vy, wz):
        speed = math.hypot(vx, vy)
        maximum = self.params["max_linear_speed_mps"]
        if speed > maximum and speed > 0.0:
            scale = maximum / speed
            vx *= scale
            vy *= scale
        wz = max(
            -self.params["max_angular_speed_radps"],
            min(self.params["max_angular_speed_radps"], wz),
        )
        msg = Twist()
        msg.linear.x = float(vx)
        msg.linear.y = float(vy)
        msg.angular.z = float(wz)
        self.command_publisher.publish(msg)
        self.last_command = (msg.linear.x, msg.linear.y, msg.angular.z)

    def _publish_zero(self):
        self._publish_command(0.0, 0.0, 0.0)

    def _relative_to_a(self, pose):
        if self.a_pose is None or pose is None:
            return None
        return rotate_world_to_body(
            pose.x - self.a_pose.x, pose.y - self.a_pose.y, self.a_pose.yaw
        )

    def _errors(self):
        if self.current_odom is None or self.target_pose is None:
            return None
        dx = self.target_pose.x - self.current_odom.x
        dy = self.target_pose.y - self.current_odom.y
        body_x, body_y = rotate_world_to_body(dx, dy, self.current_odom.yaw)
        return {
            "body_x": body_x,
            "body_y": body_y,
            "position": math.hypot(dx, dy),
            "yaw": normalize_angle(self.target_pose.yaw - self.current_odom.yaw),
        }

    def _corridor_metrics(self):
        local = self._relative_to_a(self.current_odom)
        if local is None:
            return 0.0, 0.0, 0.0
        distance = math.hypot(
            self.params["outbound_dx_m"], self.params["outbound_dy_m"]
        )
        ux = self.params["outbound_dx_m"] / distance
        uy = self.params["outbound_dy_m"] / distance
        projection = ux * local[0] + uy * local[1]
        cross_track = -uy * local[0] + ux * local[1]
        return projection, cross_track, math.hypot(*local)

    def _leg_overshoot(self):
        if self.leg_start_pose is None or self.target_pose is None:
            return 0.0
        leg_x = self.target_pose.x - self.leg_start_pose.x
        leg_y = self.target_pose.y - self.leg_start_pose.y
        length = math.hypot(leg_x, leg_y)
        if length <= 1e-9:
            return 0.0
        traveled_x = self.current_odom.x - self.leg_start_pose.x
        traveled_y = self.current_odom.y - self.leg_start_pose.y
        projection = (traveled_x * leg_x + traveled_y * leg_y) / length
        return max(0.0, projection - length)

    def _hard_safety_error(self):
        if self.a_pose is None or self.current_odom is None:
            return "missing saved A or current odom"
        _, cross_track, radius = self._corridor_metrics()
        yaw_from_a = abs(normalize_angle(self.current_odom.yaw - self.a_pose.yaw))
        self.max_radius_seen_m = max(self.max_radius_seen_m, radius)
        self.max_cross_track_seen_m = max(
            self.max_cross_track_seen_m, abs(cross_track)
        )
        self.max_abs_yaw_error_seen_rad = max(
            self.max_abs_yaw_error_seen_rad, yaw_from_a
        )
        if yaw_from_a > self.params["yaw_hard_abort_rad"]:
            return f"yaw from A {math.degrees(yaw_from_a):.3f} deg exceeds hard abort"
        if abs(cross_track) > self.params["cross_track_hard_abort_m"]:
            return f"cross-track {cross_track:.6f} m exceeds hard abort"
        if radius > self.params["max_radius_from_a_m"]:
            return f"radius from A {radius:.6f} m exceeds hard abort"
        if self.state in MOVING_STATES:
            overshoot = self._leg_overshoot()
            if overshoot > self.params["overshoot_hard_abort_m"]:
                return f"leg overshoot {overshoot:.6f} m exceeds hard abort"
        return None

    def _arrived(self, tolerance):
        errors = self._errors()
        return (
            errors is not None
            and errors["position"] <= tolerance
            and abs(errors["yaw"]) <= self.params["yaw_tolerance_rad"]
        )

    def _abort(self, reason):
        if self.state == ABORTED:
            self._publish_zero()
            return
        if self.state == SUCCEEDED:
            self._publish_zero()
            return
        self.abort_reason = str(reason)
        self.target_pose = None
        self.settle_started = None
        self.return_settling = False
        self._transition(ABORTED)
        self._publish_zero()
        self._publish_result(False)
        self.get_logger().error("relative navigation aborted: " + self.abort_reason)

    def _succeed(self):
        self.abort_reason = ""
        self.target_pose = None
        self._transition(SUCCEEDED)
        self._publish_zero()
        self._publish_result(True)

    def _control_moving_state(self, now):
        self.leg_elapsed_s = now - self.leg_started
        if self.leg_elapsed_s > self.params["per_leg_timeout_s"]:
            self._abort(
                f"{self.state} leg timeout at {self.leg_elapsed_s:.3f} s"
            )
            return
        tolerance = (
            self.params["b_position_tolerance_m"]
            if self.state == OUTBOUND
            else self.params["return_position_tolerance_m"]
        )
        if self.state == RETURN and self.return_settling:
            self._publish_zero()
            if not self._arrived(tolerance):
                self._abort("left A tolerance during final no-compensation settle")
                return
            if now - self.settle_started >= self.params["final_settle_zero_duration_s"]:
                self._succeed()
            return
        if self._arrived(tolerance):
            self._publish_zero()
            self.settle_started = now
            if self.state == OUTBOUND:
                self._transition(SETTLING_AT_B, now)
            else:
                self.return_settling = True
            return
        errors = self._errors()
        vx, vy = bounded_planar_command(
            errors["body_x"],
            errors["body_y"],
            self.params["linear_gain"],
            self.params["min_effective_linear_speed_mps"],
            self.params["max_linear_speed_mps"],
        )
        wz = self.params["angular_gain"] * errors["yaw"]
        self._publish_command(vx, vy, wz)

    def _control_tick(self):
        with self._lock:
            now = time.monotonic()
            if self.shutdown_requested is not None:
                self._abort("received " + self.shutdown_requested)
                self._publish_status(now)
                return
            if self.state in WATCHDOG_STATES:
                if self.pending_abort:
                    reason = self.pending_abort
                    self.pending_abort = None
                    self._abort(reason)
                    self._publish_status(now)
                    return
                error = self._freshness_error(now) or self._hard_safety_error()
                if error:
                    self._abort(error)
                    self._publish_status(now)
                    return
            if self.state in MOVING_STATES:
                self._control_moving_state(now)
            elif self.state == SETTLING_AT_B:
                self._publish_zero()
                if not self._arrived(self.params["b_position_tolerance_m"]):
                    self._abort("left B tolerance during no-compensation settle")
                elif now - self.settle_started >= self.params["b_settle_zero_duration_s"]:
                    self._transition(WAIT_RETURN_CONFIRM, now)
            else:
                self._publish_zero()
            self._publish_status(now)

    def _status_dict(self, now=None):
        now = time.monotonic() if now is None else now
        errors = self._errors()
        projection, cross_track, radius = self._corridor_metrics()
        return {
            "schema": "mof_relative_navigation_status_v1",
            "state": self.state,
            "time": {
                "monotonic_sec": now,
                "ros_time_sec": self.get_clock().now().nanoseconds * 1e-9,
                "state_elapsed_s": now - self.state_since,
            },
            "a": self.a_pose.as_dict() if self.a_pose else None,
            "b": self.b_pose.as_dict() if self.b_pose else None,
            "current_odom": self.current_odom.as_dict() if self.current_odom else None,
            "position_error_m": errors["position"] if errors else None,
            "body_error_m": (
                [errors["body_x"], errors["body_y"]] if errors else None
            ),
            "along_track_from_a_m": projection if self.a_pose else None,
            "lateral_error_m": cross_track if self.a_pose else None,
            "radius_from_a_m": radius if self.a_pose else None,
            "yaw_error_rad": errors["yaw"] if errors else None,
            "yaw_error_deg": math.degrees(errors["yaw"]) if errors else None,
            "current_command": {
                "vx": self.last_command[0],
                "vy": self.last_command[1],
                "wz": self.last_command[2],
            },
            "leg_elapsed_s": self.leg_elapsed_s,
            "abort_reason": self.abort_reason,
        }

    def _publish_status(self, now=None):
        msg = String()
        msg.data = json.dumps(
            self._status_dict(now), ensure_ascii=False, sort_keys=True
        )
        self.status_publisher.publish(msg)

    def _publish_result(self, success):
        if self.result_published:
            return
        result = self._status_dict()
        result.update({
            "schema": "mof_relative_navigation_result_v1",
            "success": bool(success),
            "final_state": self.state,
            "max_radius_from_a_m": self.max_radius_seen_m,
            "max_abs_cross_track_m": self.max_cross_track_seen_m,
            "max_abs_yaw_error_rad": self.max_abs_yaw_error_seen_rad,
            "max_abs_yaw_error_deg": math.degrees(
                self.max_abs_yaw_error_seen_rad
            ),
            "third_segment_compensation": False,
        })
        msg = String()
        msg.data = json.dumps(result, ensure_ascii=False, sort_keys=True)
        self.result_publisher.publish(msg)
        self.result_published = True

    def request_shutdown(self, signum):
        try:
            name = signal.Signals(signum).name
        except ValueError:
            name = str(signum)
        with self._lock:
            if self.shutdown_requested is None:
                self.shutdown_requested = name

    def publish_shutdown_zero_tail(self):
        if self._zero_tail_done:
            return
        self._zero_tail_done = True
        deadline = time.monotonic() + self.params["shutdown_zero_duration_s"]
        period = 1.0 / self.params["control_rate_hz"]
        while time.monotonic() < deadline:
            self._publish_zero()
            time.sleep(period)

    def destroy_node(self):
        try:
            with self._lock:
                if self.state not in TERMINAL_STATES:
                    self._abort("node destroy requested")
            self.publish_shutdown_zero_tail()
        finally:
            super().destroy_node()


def main(args=None):
    rclpy.init(args=args, signal_handler_options=SignalHandlerOptions.NO)
    node = None
    old_handlers = {}
    try:
        node = RelativeNavigationNode()

        def handle_signal(signum, _frame):
            node.request_shutdown(signum)

        for name in ("SIGINT", "SIGTERM", "SIGHUP"):
            if hasattr(signal, name):
                signum = getattr(signal, name)
                old_handlers[signum] = signal.getsignal(signum)
                signal.signal(signum, handle_signal)

        while rclpy.ok() and node.shutdown_requested is None:
            rclpy.spin_once(node, timeout_sec=0.05)
        if node.shutdown_requested is not None:
            with node._lock:
                node._abort("received " + node.shutdown_requested)
    except BaseException as exc:
        if node is not None:
            with node._lock:
                node._abort(f"unhandled {type(exc).__name__}: {exc}")
        if not isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
    finally:
        if node is not None:
            node.publish_shutdown_zero_tail()
            node.destroy_node()
        for signum, handler in old_handlers.items():
            signal.signal(signum, handler)
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
