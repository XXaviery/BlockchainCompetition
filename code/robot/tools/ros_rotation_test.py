#!/usr/bin/env python3
"""Safely exercise ROS rotation commands and summarize odometry/IMU response."""

import argparse
import math
import time

import rclpy
from geometry_msgs.msg import Twist
from nav_msgs.msg import Odometry
from rclpy.node import Node
from sensor_msgs.msg import Imu
from std_msgs.msg import Float64MultiArray


def yaw_from_quaternion(q) -> float:
    return math.atan2(
        2.0 * (q.w * q.z + q.x * q.y),
        1.0 - 2.0 * (q.y * q.y + q.z * q.z),
    )


def unwrap_delta(current: float, previous: float) -> float:
    return math.atan2(math.sin(current - previous), math.cos(current - previous))


class RotationTest(Node):
    def __init__(self, topic: str) -> None:
        super().__init__("mof_rotation_test")
        self.publisher = self.create_publisher(Twist, topic, 10)
        self.create_subscription(Odometry, "/wheel/odom", self.odom_callback, 50)
        self.create_subscription(Odometry, "/odom", self.ekf_callback, 100)
        self.create_subscription(Imu, "/imu", self.imu_callback, 100)
        self.create_subscription(
            Float64MultiArray, "/chassis/debug", self.debug_callback, 50
        )
        self.odom_samples = []
        self.ekf_samples = []
        self.imu_samples = []
        self.debug_samples = []
        self.debug_timed_samples = []

    def odom_callback(self, msg: Odometry) -> None:
        stamp = msg.header.stamp.sec + msg.header.stamp.nanosec * 1e-9
        self.odom_samples.append(
            (
                stamp,
                yaw_from_quaternion(msg.pose.pose.orientation),
                msg.twist.twist.angular.z,
                msg.pose.pose.position.x,
                msg.pose.pose.position.y,
            )
        )

    def imu_callback(self, msg: Imu) -> None:
        stamp = msg.header.stamp.sec + msg.header.stamp.nanosec * 1e-9
        self.imu_samples.append(
            (stamp, yaw_from_quaternion(msg.orientation), msg.angular_velocity.z)
        )

    def ekf_callback(self, msg: Odometry) -> None:
        stamp = msg.header.stamp.sec + msg.header.stamp.nanosec * 1e-9
        self.ekf_samples.append(
            (
                stamp,
                yaw_from_quaternion(msg.pose.pose.orientation),
                msg.twist.twist.angular.z,
            )
        )

    def debug_callback(self, msg: Float64MultiArray) -> None:
        if len(msg.data) >= 15:
            sample = tuple(msg.data[:15])
            self.debug_samples.append(sample)
            self.debug_timed_samples.append((time.monotonic(), sample))

    def publish_rotation(self, angular_z: float) -> None:
        msg = Twist()
        msg.angular.z = angular_z
        self.publisher.publish(msg)


def spin_for(node: RotationTest, seconds: float, angular_z: float) -> None:
    deadline = time.monotonic() + seconds
    while rclpy.ok() and time.monotonic() < deadline:
        node.publish_rotation(angular_z)
        rclpy.spin_once(node, timeout_sec=0.02)


def accumulated_yaw(samples, yaw_index: int) -> float:
    return sum(
        unwrap_delta(current[yaw_index], previous[yaw_index])
        for previous, current in zip(samples, samples[1:])
    )


def gyro_integral(samples) -> float:
    total = 0.0
    for previous, current in zip(samples, samples[1:]):
        dt = current[0] - previous[0]
        if 0.0 < dt < 0.1:
            total += 0.5 * (previous[2] + current[2]) * dt
    return total


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--topic", default="/cmd_vel_nav")
    parser.add_argument("--angular", type=float, required=True)
    parser.add_argument("--duration", type=float, default=2.0)
    args = parser.parse_args()
    if abs(args.angular) > 1.0:
        parser.error("--angular magnitude must be no greater than 1.0 rad/s")
    maximum_duration = 30.0 if args.angular == 0.0 else 5.0
    if not 0.1 <= args.duration <= maximum_duration:
        parser.error(f"--duration must be between 0.1 and {maximum_duration:.1f} seconds")

    rclpy.init()
    node = RotationTest(args.topic)
    try:
        discovery_deadline = time.monotonic() + 5.0
        while (
            rclpy.ok()
            and time.monotonic() < discovery_deadline
            and (not node.odom_samples or not node.imu_samples or not node.ekf_samples)
        ):
            node.publish_rotation(0.0)
            rclpy.spin_once(node, timeout_sec=0.05)
        if not node.odom_samples or not node.imu_samples or not node.ekf_samples:
            raise RuntimeError("missing /wheel/odom, /imu, or /odom input")
        node.odom_samples.clear()
        node.ekf_samples.clear()
        node.imu_samples.clear()
        node.debug_samples.clear()
        node.debug_timed_samples.clear()
        print(
            f"COMMAND angular_z={args.angular:.3f}rad/s duration={args.duration:.2f}s",
            flush=True,
        )
        command_start = time.monotonic()
        spin_for(node, args.duration, args.angular)
        command_end = time.monotonic()
        spin_for(node, 1.0, 0.0)
    finally:
        for _ in range(15):
            node.publish_rotation(0.0)
            rclpy.spin_once(node, timeout_sec=0.02)

    odom_yaw = accumulated_yaw(node.odom_samples, 1)
    ekf_yaw = accumulated_yaw(node.ekf_samples, 1)
    imu_yaw = accumulated_yaw(node.imu_samples, 1)
    imu_gyro_yaw = gyro_integral(node.imu_samples)
    peak_odom_w = max((abs(s[2]) for s in node.odom_samples), default=0.0)
    peak_ekf_w = max((abs(s[2]) for s in node.ekf_samples), default=0.0)
    max_ekf_yaw_step = max(
        (
            abs(unwrap_delta(current[1], previous[1]))
            for previous, current in zip(node.ekf_samples, node.ekf_samples[1:])
        ),
        default=0.0,
    )
    peak_imu_w = max((abs(s[2]) for s in node.imu_samples), default=0.0)
    dx = node.odom_samples[-1][3] - node.odom_samples[0][3]
    dy = node.odom_samples[-1][4] - node.odom_samples[0][4]
    print(f"ODOM samples={len(node.odom_samples)} yaw={math.degrees(odom_yaw):+.3f}deg peak_w={peak_odom_w:.4f}rad/s")
    print(
        f"EKF samples={len(node.ekf_samples)} yaw={math.degrees(ekf_yaw):+.3f}deg "
        f"peak_w={peak_ekf_w:.4f}rad/s max_step={math.degrees(max_ekf_yaw_step):.3f}deg"
    )
    print(f"IMU samples={len(node.imu_samples)} yaw={math.degrees(imu_yaw):+.3f}deg gyro_integral={math.degrees(imu_gyro_yaw):+.3f}deg peak_gz={peak_imu_w:.4f}rad/s")
    print(f"ODOM translation dx={dx:+.4f}m dy={dy:+.4f}m")
    if node.debug_samples:
        active = max(node.debug_samples, key=lambda s: sum(abs(v) for v in s[0:3]))
        peak_pwm = [
            max(node.debug_samples, key=lambda s, i=i: abs(s[6 + i]))[6 + i]
            for i in range(3)
        ]
        count_delta = [
            node.debug_samples[-1][9 + i] - node.debug_samples[0][9 + i]
            for i in range(3)
        ]
        print("TARGET " + " ".join(f"v{i + 1}={active[i]:+.4f}" for i in range(3)))
        print("PEAK_PWM " + " ".join(f"pwm{i + 1}={peak_pwm[i]:+.0f}" for i in range(3)))
        print("ENCODER_DELTA " + " ".join(f"c{i + 1}={count_delta[i]:+.0f}" for i in range(3)))
        steady = [
            sample
            for stamp, sample in node.debug_timed_samples
            if command_start + min(0.8, args.duration * 0.25) <= stamp <= command_end
        ]
        if steady:
            avg_measured = [
                sum(sample[3 + i] for sample in steady) / len(steady)
                for i in range(3)
            ]
            avg_pwm = [
                sum(sample[6 + i] for sample in steady) / len(steady)
                for i in range(3)
            ]
            print(
                "STEADY_MEASURED "
                + " ".join(f"v{i + 1}={avg_measured[i]:+.4f}" for i in range(3))
            )
            print(
                "STEADY_PWM "
                + " ".join(f"pwm{i + 1}={avg_pwm[i]:+.1f}" for i in range(3))
            )
    else:
        print("CHASSIS_DEBUG missing")
    node.destroy_node()
    rclpy.shutdown()


if __name__ == "__main__":
    main()
