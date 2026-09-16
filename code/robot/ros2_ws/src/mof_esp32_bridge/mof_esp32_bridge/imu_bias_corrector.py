#!/usr/bin/env python3
"""Stationary-only gyro-Z bias corrector for the six-axis IMU.

The raw /imu stream remains available for diagnostics.  This node publishes
/imu_corrected only after a verified stationary window has established the
initial bias.  Once motion is observed the bias is latched; later stationary
periods may update it only with a very small gain.
"""

import copy
import math
import time

import rclpy
from geometry_msgs.msg import Twist
from rclpy.node import Node
from sensor_msgs.msg import Imu
from std_msgs.msg import Float64MultiArray


class ImuBiasCorrector(Node):
    def __init__(self):
        super().__init__("imu_bias_corrector")
        self.declare_parameter("stationary_seconds", 5.0)
        self.declare_parameter("recalibration_min_interval", 30.0)
        self.declare_parameter("slow_update_alpha", 0.001)
        self.declare_parameter("motion_epsilon", 0.001)
        self.declare_parameter("encoder_epsilon", 0.5)
        self.stationary_seconds = float(self.get_parameter("stationary_seconds").value)
        self.recalibration_min_interval = float(
            self.get_parameter("recalibration_min_interval").value
        )
        self.slow_update_alpha = float(self.get_parameter("slow_update_alpha").value)
        self.motion_epsilon = float(self.get_parameter("motion_epsilon").value)
        self.encoder_epsilon = float(self.get_parameter("encoder_epsilon").value)

        self.corrected_pub = self.create_publisher(Imu, "/imu_corrected", 50)
        self.diag_pub = self.create_publisher(Float64MultiArray, "/imu_bias_estimate", 10)
        self.create_subscription(Imu, "/imu", self.imu_callback, 100)
        self.create_subscription(Float64MultiArray, "/chassis/debug", self.debug_callback, 100)
        self.create_subscription(Twist, "/cmd_vel", self.cmd_callback, 20)

        self.bias = None
        self.bias_samples = []
        self.stationary_since = None
        self.last_stationary_update = None
        self.last_encoder = None
        self.last_encoder_change = 0.0
        self.last_debug_time = None
        self.last_cmd_time = None
        self.target = [math.inf] * 3
        self.measured = [math.inf] * 3
        self.pwm = [math.inf] * 3
        self.cmd = [math.inf] * 3
        self.last_diag = None

    def debug_callback(self, msg):
        if len(msg.data) < 15:
            return
        self.target = list(msg.data[0:3])
        self.measured = list(msg.data[3:6])
        self.pwm = list(msg.data[6:9])
        counts = tuple(msg.data[9:12])
        now = time.monotonic()
        if self.last_encoder is not None and max(
            abs(a - b) for a, b in zip(counts, self.last_encoder)
        ) > self.encoder_epsilon:
            self.last_encoder_change = now
        self.last_encoder = counts
        self.last_debug_time = time.monotonic()

    def cmd_callback(self, msg):
        self.cmd = [msg.linear.x, msg.linear.y, msg.angular.z]
        self.last_cmd_time = time.monotonic()

    def stationary_ok(self):
        if self.last_debug_time is None or time.monotonic() - self.last_debug_time > 0.6:
            return False
        if self.last_cmd_time is None or time.monotonic() - self.last_cmd_time > 0.6:
            return False
        if time.monotonic() - self.last_encoder_change < 0.6:
            return False
        if max(abs(v) for v in self.target + self.measured + self.pwm + self.cmd) > self.motion_epsilon:
            return False
        return True

    def publish_diag(self, stationary):
        status = [
            float(self.bias or 0.0),
            float(self.bias is not None),
            float(stationary),
            float(self.bias is not None and self.last_stationary_update is not None),
            float(len(self.bias_samples)),
        ]
        if status == self.last_diag:
            return
        msg = Float64MultiArray()
        msg.data = status
        self.diag_pub.publish(msg)
        self.last_diag = status

    def update_bias(self, gyro_z, now, stationary):
        if not stationary:
            self.stationary_since = None
            self.bias_samples.clear()
            return
        if self.stationary_since is None:
            self.stationary_since = now
        if self.bias is None:
            self.bias_samples.append(gyro_z)
            if now - self.stationary_since >= self.stationary_seconds:
                self.bias = sum(self.bias_samples) / len(self.bias_samples)
                self.last_stationary_update = now
                self.bias_samples.clear()
            return
        if now - self.last_stationary_update < self.recalibration_min_interval:
            return
        self.bias += self.slow_update_alpha * (gyro_z - self.bias)
        self.last_stationary_update = now

    def imu_callback(self, source):
        now = time.monotonic()
        stationary = self.stationary_ok()
        self.update_bias(source.angular_velocity.z, now, stationary)
        self.publish_diag(stationary)

        # Do not feed an uncalibrated gyro into EKF. If calibration is
        # interrupted by motion, publish raw data with an explicit zero bias;
        # no estimate was applied and no real rotation is absorbed.
        if self.bias is None:
            return
        msg = copy.deepcopy(source)
        msg.angular_velocity.z -= self.bias
        self.corrected_pub.publish(msg)


def main(args=None):
    rclpy.init(args=args)
    node = ImuBiasCorrector()
    try:
        rclpy.spin(node)
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
