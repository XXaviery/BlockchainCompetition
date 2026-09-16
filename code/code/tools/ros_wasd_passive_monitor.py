#!/usr/bin/env python3
"""Passive WASD chassis monitor: records telemetry and never publishes velocity."""

import argparse
import json
import signal
import sys
import time

import rclpy
from geometry_msgs.msg import Twist
from rclpy.node import Node
from rclpy.signals import SignalHandlerOptions
from std_msgs.msg import Float64MultiArray


class WasdPassiveMonitor(Node):
    def __init__(self, timeline_path):
        super().__init__("ros_wasd_passive_monitor")
        self.timeline = open(timeline_path, "w", encoding="utf-8", buffering=1)
        self.started = time.monotonic()
        self.last_debug_at = None
        self.last_nav = [0.0, 0.0, 0.0]
        self.last_final = [0.0, 0.0, 0.0]
        self.zero_samples = 0
        self.ready_printed = False
        self.stop_requested = False
        self.failure = None
        self.stall_since = [None, None, None]
        self.saturation_since = [None, None, None]
        self.create_subscription(Twist, "/cmd_vel_nav", self.nav_cb, 50)
        self.create_subscription(Twist, "/cmd_vel", self.final_cb, 50)
        self.create_subscription(
            Float64MultiArray, "/chassis/debug", self.debug_cb, 100
        )
        self.create_timer(0.05, self.watchdog)

    @staticmethod
    def twist(msg):
        return [float(msg.linear.x), float(msg.linear.y), float(msg.angular.z)]

    def nav_cb(self, msg):
        self.last_nav = self.twist(msg)

    def final_cb(self, msg):
        self.last_final = self.twist(msg)

    def fail(self, reason, row=None):
        if self.failure is None:
            self.failure = {
                "reason": reason,
                "monotonic": time.monotonic(),
                "elapsed_s": time.monotonic() - self.started,
                "row": row,
            }
            print("FAULT_JSON=" + json.dumps(self.failure, sort_keys=True), flush=True)

    def debug_cb(self, msg):
        if len(msg.data) < 15:
            self.fail("short /chassis/debug frame")
            return
        now = time.monotonic()
        self.last_debug_at = now
        values = [float(value) for value in msg.data[:15]]
        target = values[0:3]
        measured = values[3:6]
        pwm = values[6:9]
        encoder = values[9:12]
        row = {
            "monotonic": now,
            "elapsed_s": now - self.started,
            "cmd_vel_nav": self.last_nav,
            "cmd_vel": self.last_final,
            "target": target,
            "measured": measured,
            "pwm": pwm,
            "encoder": encoder,
            "wheel_distance_m": values[12:15],
        }
        self.timeline.write(json.dumps(row, sort_keys=True) + "\n")

        all_zero = (
            max(abs(value) for value in target) < 0.001
            and max(abs(value) for value in measured) < 0.005
            and max(abs(value) for value in pwm) < 0.5
        )
        self.zero_samples = self.zero_samples + 1 if all_zero else 0
        if self.zero_samples >= 5 and not self.ready_printed:
            self.ready_printed = True
            print("MONITOR_READY_ZERO=1", flush=True)

        for index in range(3):
            active = abs(target[index]) >= 0.04
            # The previous M2 event peaked at 0.295 m/s.  A single fresh debug
            # sample above both 0.12 m/s and twice its target is a hard stop.
            if active and abs(measured[index]) > max(0.12, 2.0 * abs(target[index])):
                self.fail(
                    f"M{index + 1} measured overspeed {measured[index]:.6f}", row
                )
                return
            if active and measured[index] * target[index] < 0.0 and abs(measured[index]) >= 0.02:
                self.fail(
                    f"M{index + 1} measured direction opposes target", row
                )
                return
            if active and abs(measured[index]) < 0.005:
                self.stall_since[index] = self.stall_since[index] or now
                if now - self.stall_since[index] >= 0.30:
                    self.fail(f"M{index + 1} feedback/stall for 0.30 s", row)
                    return
            else:
                self.stall_since[index] = None
            if active and abs(pwm[index]) >= 245 and abs(measured[index]) < 0.5 * abs(target[index]):
                self.saturation_since[index] = self.saturation_since[index] or now
                if now - self.saturation_since[index] >= 0.30:
                    self.fail(
                        f"M{index + 1} PWM saturation with severe under-speed", row
                    )
                    return
            else:
                self.saturation_since[index] = None

    def watchdog(self):
        if self.last_debug_at is not None and time.monotonic() - self.last_debug_at > 0.30:
            self.fail("/chassis/debug stale for over 0.30 s")

    def close(self):
        self.timeline.flush()
        self.timeline.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--timeline", required=True)
    args = parser.parse_args()
    rclpy.init(signal_handler_options=SignalHandlerOptions.NO)
    node = WasdPassiveMonitor(args.timeline)

    def stop(_signum, _frame):
        node.stop_requested = True

    for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(signum, stop)
    try:
        while rclpy.ok() and not node.stop_requested and node.failure is None:
            rclpy.spin_once(node, timeout_sec=0.05)
    finally:
        node.close()
        node.destroy_node()
        rclpy.shutdown()
    return 2 if node.failure is not None else 0


if __name__ == "__main__":
    sys.exit(main())
