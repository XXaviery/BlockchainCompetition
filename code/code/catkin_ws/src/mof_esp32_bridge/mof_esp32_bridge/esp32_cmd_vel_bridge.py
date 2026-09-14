import struct
import threading
import time
import math
import queue

import rclpy
from geometry_msgs.msg import TransformStamped, Twist, TwistStamped
from nav_msgs.msg import Odometry
from rcl_interfaces.msg import SetParametersResult
from rclpy.duration import Duration
from rclpy.node import Node
from rclpy.time import Time
from sensor_msgs.msg import Imu, LaserScan
from std_msgs.msg import Float64MultiArray
from tf2_ros import TransformBroadcaster

try:
    import serial
except ImportError as exc:
    raise SystemExit("Missing pyserial. Install with: sudo apt install python3-serial") from exc


FRAME_HEAD = b"\xAA\x66"
PAYLOAD_LEN = 12
CHASSIS_TELEMETRY_HEAD = b"\xAA\x77"
CHASSIS_TELEMETRY_PAYLOAD_LEN = 54
CHASSIS_TELEMETRY_FRAME_LEN = 58
IMU_TELEMETRY_HEAD = b"\xAA\x88"
IMU_TELEMETRY_PAYLOAD_LEN = 64
IMU_TELEMETRY_FRAME_LEN = 68
IMU_STATUS_READY = 0x01
IMU_STATUS_SAMPLE_VALID = 0x02
IMU_STATUS_GYRO_CALIBRATED = 0x04
IMU_STATUS_ATTITUDE_VALID = 0x08
TUNING_CMD_HEAD = b"\xAA\x67"
TUNING_RESPONSE_HEAD = b"\xAA\x78"
TUNING_RESPONSE_PAYLOAD_LEN = 14
TUNING_RESPONSE_FRAME_LEN = 18
TUNING_GROUPS = {
    "runtime_running_pwm_positive": 4,
    "runtime_running_pwm_negative": 5,
    "runtime_ff_positive": 6,
    "runtime_ff_negative": 7,
}
RX_MIN_FRAME_LEN = min(
    CHASSIS_TELEMETRY_FRAME_LEN,
    IMU_TELEMETRY_FRAME_LEN,
    TUNING_RESPONSE_FRAME_LEN,
)
LIDAR_SCAN_HEAD = b"\xAA\x55"
LIDAR_SCAN_TAIL = b"\x55\xAA"
LIDAR_SCAN_TYPE = 0x01
LIDAR_POINT_COUNT = 360
LIDAR_SCAN_PAYLOAD_LEN = LIDAR_POINT_COUNT * 2
LIDAR_SCAN_FRAME_LEN = 5 + LIDAR_SCAN_PAYLOAD_LEN + 1 + 2
LIDAR_RANGE_MIN_M = 0.02
LIDAR_RANGE_MAX_M = 12.0
LIDAR_DEFAULT_SCAN_TIME_S = 0.10
LIDAR_REVERSE_SCAN_DIRECTION = True
WHEEL_BASE_RADIUS_M = 0.138
SQRT3_OVER_2 = 0.86602540


class Esp32CmdVelBridge(Node):
    def __init__(self):
        super().__init__("esp32_cmd_vel_bridge")

        self.declare_parameter("port", "/dev/mof_esp32")
        self.declare_parameter("baud", 921600)
        # The bridge consumes the final, safety-filtered velocity command.
        # Manual tools that intentionally bypass the Nav2 safety chain must
        # opt in with an explicit topic override.
        self.declare_parameter("topic", "/cmd_vel")
        self.declare_parameter("message_type", "twist")
        self.declare_parameter("timeout_s", 0.5)
        self.declare_parameter("max_vx", 0.6)
        self.declare_parameter("max_vy", 0.6)
        self.declare_parameter("max_w", 3.0)
        self.declare_parameter("discard_rx", True)
        self.declare_parameter("publish_scan", True)
        self.declare_parameter("scan_topic", "/scan")
        self.declare_parameter("scan_frame_id", "laser")
        self.declare_parameter("scan_timing_topic", "/scan/timing")
        self.declare_parameter("lidar_range_min", LIDAR_RANGE_MIN_M)
        self.declare_parameter("lidar_range_max", LIDAR_RANGE_MAX_M)
        self.declare_parameter("lidar_spike_filter", True)
        self.declare_parameter("lidar_spike_filter_window", 2)
        self.declare_parameter("lidar_spike_filter_min_neighbors", 2)
        self.declare_parameter("lidar_spike_filter_max_delta", 0.45)
        self.declare_parameter("publish_odom", True)
        self.declare_parameter("odom_topic", "/odom")
        self.declare_parameter("odom_frame_id", "odom")
        self.declare_parameter("base_frame_id", "base_footprint")
        self.declare_parameter("publish_odom_tf", True)
        self.declare_parameter("wheel_odom_angular_scale", 1.0)
        self.declare_parameter("publish_imu", True)
        self.declare_parameter("imu_topic", "/imu")
        self.declare_parameter("imu_frame_id", "imu_link")
        self.declare_parameter("publish_chassis_debug", True)
        self.declare_parameter("chassis_debug_topic", "/chassis/debug")
        self.declare_parameter("allow_runtime_tuning", False)
        self.declare_parameter("runtime_running_pwm_positive", [83.0, 83.0, 83.0])
        self.declare_parameter("runtime_running_pwm_negative", [83.0, 83.0, 83.0])
        self.declare_parameter("runtime_ff_positive", [2.0, 2.0, 2.0])
        self.declare_parameter("runtime_ff_negative", [2.0, 2.0, 2.0])
        self.declare_parameter("tuning_response_topic", "/chassis/tuning_response")

        self.port = str(self.get_parameter("port").value)
        self.baud = int(self.get_parameter("baud").value)
        self.topic = str(self.get_parameter("topic").value)
        self.message_type = str(self.get_parameter("message_type").value).lower()
        self.timeout_s = float(self.get_parameter("timeout_s").value)
        self.max_vx = float(self.get_parameter("max_vx").value)
        self.max_vy = float(self.get_parameter("max_vy").value)
        self.max_w = float(self.get_parameter("max_w").value)
        self.discard_rx = bool(self.get_parameter("discard_rx").value)
        self.publish_scan = bool(self.get_parameter("publish_scan").value)
        self.scan_topic = str(self.get_parameter("scan_topic").value)
        self.scan_frame_id = str(self.get_parameter("scan_frame_id").value)
        self.scan_timing_topic = str(self.get_parameter("scan_timing_topic").value)
        self.lidar_range_min = float(self.get_parameter("lidar_range_min").value)
        self.lidar_range_max = float(self.get_parameter("lidar_range_max").value)
        self.lidar_spike_filter = bool(self.get_parameter("lidar_spike_filter").value)
        self.lidar_spike_filter_window = max(1, int(self.get_parameter("lidar_spike_filter_window").value))
        self.lidar_spike_filter_min_neighbors = max(
            1,
            int(self.get_parameter("lidar_spike_filter_min_neighbors").value),
        )
        self.lidar_spike_filter_max_delta = float(self.get_parameter("lidar_spike_filter_max_delta").value)
        self.publish_odom = bool(self.get_parameter("publish_odom").value)
        self.odom_topic = str(self.get_parameter("odom_topic").value)
        self.odom_frame_id = str(self.get_parameter("odom_frame_id").value)
        self.base_frame_id = str(self.get_parameter("base_frame_id").value)
        self.publish_odom_tf = bool(self.get_parameter("publish_odom_tf").value)
        self.wheel_odom_angular_scale = float(
            self.get_parameter("wheel_odom_angular_scale").value
        )
        self.publish_imu = bool(self.get_parameter("publish_imu").value)
        self.imu_topic = str(self.get_parameter("imu_topic").value)
        self.imu_frame_id = str(self.get_parameter("imu_frame_id").value)
        self.publish_chassis_debug = bool(
            self.get_parameter("publish_chassis_debug").value
        )
        self.chassis_debug_topic = str(
            self.get_parameter("chassis_debug_topic").value
        )
        self.allow_runtime_tuning = bool(
            self.get_parameter("allow_runtime_tuning").value
        )
        self.tuning_response_topic = str(
            self.get_parameter("tuning_response_topic").value
        )

        self.serial = serial.Serial(self.port, self.baud, timeout=0, write_timeout=0.1)
        self.serial_lock = threading.Lock()
        self.rx_buffer = bytearray()
        self.odom_queue = queue.SimpleQueue()
        self.scan_queue = queue.SimpleQueue()
        self.imu_queue = queue.SimpleQueue()
        self.chassis_debug_queue = queue.SimpleQueue()
        self.tuning_response_queue = queue.SimpleQueue()
        self.latest_chassis_values = None
        self.scan_publisher = None
        if self.publish_scan:
            self.scan_publisher = self.create_publisher(LaserScan, self.scan_topic, 10)
        self.scan_timing_publisher = None
        if self.publish_scan:
            self.scan_timing_publisher = self.create_publisher(
                Float64MultiArray, self.scan_timing_topic, 10
            )
        self.odom_publisher = None
        if self.publish_odom:
            self.odom_publisher = self.create_publisher(Odometry, self.odom_topic, 20)
        self.imu_publisher = None
        if self.publish_imu:
            self.imu_publisher = self.create_publisher(Imu, self.imu_topic, 50)
        self.chassis_debug_publisher = None
        if self.publish_chassis_debug:
            self.chassis_debug_publisher = self.create_publisher(
                Float64MultiArray, self.chassis_debug_topic, 20
            )
        self.tuning_response_publisher = None
        if self.allow_runtime_tuning:
            self.tuning_response_publisher = self.create_publisher(
                Float64MultiArray, self.tuning_response_topic, 10
            )
        self.odom_tf_broadcaster = None
        if self.publish_odom_tf:
            self.odom_tf_broadcaster = TransformBroadcaster(self)
        self.odom_x = 0.0
        self.odom_y = 0.0
        self.odom_yaw = 0.0
        self.last_odom_time = None
        self.last_scan_frame_time = None
        self.last_scan_stamp_ns = None
        self.last_cmd_time = time.monotonic()
        self.stopped = True
        self.running = True
        self.add_on_set_parameters_callback(self.runtime_tuning_parameter_callback)

        if self.message_type in ("twist_stamped", "twiststamped", "stamped"):
            self.subscription = self.create_subscription(
                TwistStamped, self.topic, self.twist_stamped_callback, 10
            )
            subscribed_type = "geometry_msgs/TwistStamped"
        else:
            self.subscription = self.create_subscription(
                Twist, self.topic, self.twist_callback, 10
            )
            subscribed_type = "geometry_msgs/Twist"

        self.watchdog_timer = self.create_timer(0.05, self.watchdog_update)
        self.rx_publish_timer = self.create_timer(0.02, self.flush_rx_publish_queues)

        self.rx_thread = None
        if (self.discard_rx or self.publish_scan or self.publish_odom or
                self.publish_odom_tf or self.publish_imu):
            self.rx_thread = threading.Thread(target=self.discard_rx_loop, daemon=True)
            self.rx_thread.start()

        self.get_logger().info(
            f"Subscribed {self.topic} ({subscribed_type}), sending to {self.port} at {self.baud}"
        )

    @staticmethod
    def clamp(value, limit):
        if limit <= 0:
            return value
        return max(-limit, min(limit, value))

    @staticmethod
    def build_frame(vx, vy, w):
        payload = struct.pack("<fff", vx, vy, w)
        checksum = sum(payload) & 0xFF
        return FRAME_HEAD + bytes([PAYLOAD_LEN]) + payload + bytes([checksum])

    @staticmethod
    def build_tuning_frame(command, values):
        payload = struct.pack("<Bfff", command, *values)
        return TUNING_CMD_HEAD + bytes([len(payload)]) + payload + bytes([sum(payload) & 0xFF])

    def chassis_is_stopped_for_tuning(self):
        values = self.latest_chassis_values
        if values is None:
            return False
        return (
            max(abs(float(value)) for value in values[0:6]) <= 0.005
            and max(abs(int(value)) for value in values[6:9]) == 0
        )

    def runtime_tuning_parameter_callback(self, parameters):
        tuning_updates = [parameter for parameter in parameters if parameter.name in TUNING_GROUPS]
        if not tuning_updates:
            return SetParametersResult(successful=True)
        if not self.allow_runtime_tuning:
            return SetParametersResult(
                successful=False,
                reason="runtime tuning is disabled outside manual open-field mode",
            )
        if not self.chassis_is_stopped_for_tuning():
            return SetParametersResult(
                successful=False,
                reason="runtime tuning requires fresh zero target/measured/PWM telemetry",
            )

        for parameter in tuning_updates:
            values = list(parameter.value)
            if len(values) != 3 or not all(math.isfinite(float(value)) for value in values):
                return SetParametersResult(
                    successful=False,
                    reason=f"{parameter.name} requires three finite values",
                )
            upper = 255.0 if "running_pwm" in parameter.name else 10.0
            if any(float(value) < 0.0 or float(value) > upper for value in values):
                return SetParametersResult(
                    successful=False,
                    reason=f"{parameter.name} values must be within [0, {upper}]",
                )

        try:
            with self.serial_lock:
                for parameter in tuning_updates:
                    values = tuple(float(value) for value in parameter.value)
                    self.serial.write(
                        self.build_tuning_frame(TUNING_GROUPS[parameter.name], values)
                    )
                    self.get_logger().warn(
                        f"RAM-only tuning request {parameter.name}={values}; NVS unchanged"
                    )
        except serial.SerialException as exc:
            return SetParametersResult(successful=False, reason=f"serial tuning write failed: {exc}")
        return SetParametersResult(successful=True)

    def send_velocity(self, vx, vy, w):
        frame = self.build_frame(vx, vy, w)
        with self.serial_lock:
            self.serial.write(frame)

    def send_twist(self, msg):
        vx = self.clamp(float(msg.linear.x), self.max_vx)
        vy = self.clamp(float(msg.linear.y), self.max_vy)
        w = self.clamp(float(msg.angular.z), self.max_w)

        try:
            self.send_velocity(vx, vy, w)
            self.last_cmd_time = time.monotonic()
            self.stopped = False
        except serial.SerialException as exc:
            self.get_logger().error(f"Serial write failed: {exc}")

    def twist_callback(self, msg):
        self.send_twist(msg)

    def twist_stamped_callback(self, msg):
        self.send_twist(msg.twist)

    def watchdog_update(self):
        if self.stopped:
            return
        if time.monotonic() - self.last_cmd_time < self.timeout_s:
            return

        try:
            self.send_velocity(0.0, 0.0, 0.0)
            self.stopped = True
            self.get_logger().warn("cmd_vel timeout, sent stop frame")
        except serial.SerialException as exc:
            self.get_logger().error(f"Serial stop failed: {exc}")

    def parse_rx_buffer(self):
        while len(self.rx_buffer) >= RX_MIN_FRAME_LEN:
            telemetry_index = self.rx_buffer.find(CHASSIS_TELEMETRY_HEAD)
            scan_index = self.rx_buffer.find(LIDAR_SCAN_HEAD)
            imu_index = self.rx_buffer.find(IMU_TELEMETRY_HEAD)
            tuning_index = self.rx_buffer.find(TUNING_RESPONSE_HEAD)
            head_candidates = [
                i for i in (telemetry_index, scan_index, imu_index, tuning_index) if i >= 0
            ]
            if not head_candidates:
                del self.rx_buffer[:-1]
                return
            head_index = min(head_candidates)
            if head_index > 0:
                del self.rx_buffer[:head_index]

            if self.rx_buffer.startswith(CHASSIS_TELEMETRY_HEAD):
                if not self.parse_chassis_telemetry_frame():
                    return
            elif self.rx_buffer.startswith(LIDAR_SCAN_HEAD):
                if not self.parse_lidar_scan_frame():
                    return
            elif self.rx_buffer.startswith(IMU_TELEMETRY_HEAD):
                if not self.parse_imu_telemetry_frame():
                    return
            elif self.rx_buffer.startswith(TUNING_RESPONSE_HEAD):
                if not self.parse_tuning_response_frame():
                    return
            else:
                del self.rx_buffer[0]

    def parse_chassis_telemetry_frame(self):
        if len(self.rx_buffer) < CHASSIS_TELEMETRY_FRAME_LEN:
            return False
        if self.rx_buffer[2] != CHASSIS_TELEMETRY_PAYLOAD_LEN:
            del self.rx_buffer[0]
            return True

        payload_start = 3
        payload_end = payload_start + CHASSIS_TELEMETRY_PAYLOAD_LEN
        payload = bytes(self.rx_buffer[payload_start:payload_end])
        checksum = self.rx_buffer[payload_end]
        del self.rx_buffer[:CHASSIS_TELEMETRY_FRAME_LEN]

        if (sum(payload) & 0xFF) != checksum:
            return True

        values = struct.unpack("<ffffffhhhiiifff", payload)
        self.latest_chassis_values = values
        measured = values[3:6]
        if self.publish_odom or self.publish_odom_tf:
            self.odom_queue.put_nowait((measured, time.monotonic()))
        if self.publish_chassis_debug and self.chassis_debug_publisher is not None:
            self.chassis_debug_queue.put_nowait(values)
        return True

    def flush_rx_publish_queues(self):
        while not self.odom_queue.empty():
            self.publish_chassis_odom(self.odom_queue.get_nowait())

        while not self.scan_queue.empty():
            self.publish_lidar_scan(self.scan_queue.get_nowait())

        while not self.imu_queue.empty():
            self.publish_imu_sample(self.imu_queue.get_nowait())

        while not self.chassis_debug_queue.empty():
            msg = Float64MultiArray()
            # target[0:3], measured[3:6], pwm[6:9], encoder_count[9:12],
            # wheel_distance_m[12:15]
            msg.data = [float(value) for value in self.chassis_debug_queue.get_nowait()]
            self.chassis_debug_publisher.publish(msg)

        while not self.tuning_response_queue.empty():
            command, success, values = self.tuning_response_queue.get_nowait()
            if self.tuning_response_publisher is not None:
                msg = Float64MultiArray()
                msg.data = [float(command), 1.0 if success else 0.0, *values]
                self.tuning_response_publisher.publish(msg)

    def parse_tuning_response_frame(self):
        if len(self.rx_buffer) < TUNING_RESPONSE_FRAME_LEN:
            return False
        if self.rx_buffer[2] != TUNING_RESPONSE_PAYLOAD_LEN:
            del self.rx_buffer[0]
            return True

        payload_start = 3
        payload_end = payload_start + TUNING_RESPONSE_PAYLOAD_LEN
        payload = bytes(self.rx_buffer[payload_start:payload_end])
        checksum = self.rx_buffer[payload_end]
        del self.rx_buffer[:TUNING_RESPONSE_FRAME_LEN]
        if (sum(payload) & 0xFF) != checksum:
            return True

        command, success, value_1, value_2, value_3 = struct.unpack("<BBfff", payload)
        values = (float(value_1), float(value_2), float(value_3))
        self.tuning_response_queue.put_nowait((command, bool(success), values))
        self.get_logger().warn(
            f"RAM-only tuning response command={command} success={bool(success)} values={values}"
        )
        return True

    def parse_imu_telemetry_frame(self):
        if len(self.rx_buffer) < IMU_TELEMETRY_FRAME_LEN:
            return False
        if self.rx_buffer[2] != IMU_TELEMETRY_PAYLOAD_LEN:
            del self.rx_buffer[0]
            return True

        payload_start = 3
        payload_end = payload_start + IMU_TELEMETRY_PAYLOAD_LEN
        payload = bytes(self.rx_buffer[payload_start:payload_end])
        checksum = self.rx_buffer[payload_end]
        del self.rx_buffer[:IMU_TELEMETRY_FRAME_LEN]

        if (sum(payload) & 0xFF) != checksum:
            return True

        values = struct.unpack("<BBIH14f", payload)
        status, who_am_i, timestamp_us, sequence = values[:4]
        ax, ay, az, gx, gy, gz, temperature_c = values[4:11]
        orientation = values[11:15]
        gyro_bias = values[15:18]
        required_status = IMU_STATUS_READY | IMU_STATUS_SAMPLE_VALID | IMU_STATUS_GYRO_CALIBRATED
        if (status & required_status) != required_status:
            return True
        if self.publish_imu and self.imu_publisher is not None:
            self.imu_queue.put_nowait((
                (ax, ay, az),
                (gx, gy, gz),
                orientation,
                bool(status & IMU_STATUS_ATTITUDE_VALID),
                timestamp_us,
                sequence,
                who_am_i,
                temperature_c,
                gyro_bias,
            ))
        return True

    def publish_imu_sample(self, imu_sample):
        if not self.publish_imu or self.imu_publisher is None:
            return

        acceleration, angular_velocity, orientation, attitude_valid, _, _, _, _, _ = imu_sample
        imu = Imu()
        imu.header.stamp = self.get_clock().now().to_msg()
        imu.header.frame_id = self.imu_frame_id

        if attitude_valid:
            imu.orientation.x = orientation[0]
            imu.orientation.y = orientation[1]
            imu.orientation.z = orientation[2]
            imu.orientation.w = orientation[3]
            imu.orientation_covariance[0] = 0.02
            imu.orientation_covariance[4] = 0.02
            # A six-axis filter has no absolute yaw reference.
            imu.orientation_covariance[8] = 999.0
        else:
            imu.orientation_covariance[0] = -1.0
        imu.angular_velocity.x = angular_velocity[0]
        imu.angular_velocity.y = angular_velocity[1]
        imu.angular_velocity.z = angular_velocity[2]
        imu.angular_velocity_covariance[0] = 0.0004
        imu.angular_velocity_covariance[4] = 0.0004
        imu.angular_velocity_covariance[8] = 0.0004
        imu.linear_acceleration.x = acceleration[0]
        imu.linear_acceleration.y = acceleration[1]
        imu.linear_acceleration.z = acceleration[2]
        imu.linear_acceleration_covariance[0] = 0.04
        imu.linear_acceleration_covariance[4] = 0.04
        imu.linear_acceleration_covariance[8] = 0.04
        self.imu_publisher.publish(imu)

    def publish_chassis_odom(self, odom_sample):
        if (not self.publish_odom or self.odom_publisher is None) and (
                not self.publish_odom_tf or self.odom_tf_broadcaster is None):
            return

        wheel_speed, frame_time = odom_sample
        now = frame_time
        if self.last_odom_time is None:
            dt = 0.0
        else:
            dt = now - self.last_odom_time
        self.last_odom_time = now

        v1, v2, v3 = wheel_speed
        vx = (v2 - v3) / (2.0 * SQRT3_OVER_2)
        vy = (v2 + v3 - 2.0 * v1) / 3.0
        wz = (
            -(v1 + v2 + v3)
            / (3.0 * WHEEL_BASE_RADIUS_M)
            * self.wheel_odom_angular_scale
        )

        if 0.0 < dt < 0.2:
            cos_yaw = math.cos(self.odom_yaw)
            sin_yaw = math.sin(self.odom_yaw)
            self.odom_x += (vx * cos_yaw - vy * sin_yaw) * dt
            self.odom_y += (vx * sin_yaw + vy * cos_yaw) * dt
            self.odom_yaw = math.atan2(
                math.sin(self.odom_yaw + wz * dt),
                math.cos(self.odom_yaw + wz * dt),
            )

        stamp = self.get_clock().now().to_msg()
        half_yaw = 0.5 * self.odom_yaw
        qz = math.sin(half_yaw)
        qw = math.cos(half_yaw)

        if self.publish_odom and self.odom_publisher is not None:
            odom = Odometry()
            odom.header.stamp = stamp
            odom.header.frame_id = self.odom_frame_id
            odom.child_frame_id = self.base_frame_id
            odom.pose.pose.position.x = self.odom_x
            odom.pose.pose.position.y = self.odom_y
            odom.pose.pose.position.z = 0.0
            odom.pose.pose.orientation.z = qz
            odom.pose.pose.orientation.w = qw
            odom.twist.twist.linear.x = vx
            odom.twist.twist.linear.y = vy
            odom.twist.twist.angular.z = wz
            odom.pose.covariance[0] = 0.02
            odom.pose.covariance[7] = 0.02
            odom.pose.covariance[35] = 0.05
            odom.twist.covariance[0] = 0.05
            odom.twist.covariance[7] = 0.05
            odom.twist.covariance[35] = 0.10
            self.odom_publisher.publish(odom)

        if self.publish_odom_tf and self.odom_tf_broadcaster is not None:
            transform = TransformStamped()
            transform.header.stamp = stamp
            transform.header.frame_id = self.odom_frame_id
            transform.child_frame_id = self.base_frame_id
            transform.transform.translation.x = self.odom_x
            transform.transform.translation.y = self.odom_y
            transform.transform.translation.z = 0.0
            transform.transform.rotation.z = qz
            transform.transform.rotation.w = qw
            self.odom_tf_broadcaster.sendTransform(transform)

    def parse_lidar_scan_frame(self):
        if len(self.rx_buffer) < LIDAR_SCAN_FRAME_LEN:
            return False
        if self.rx_buffer[2] != LIDAR_SCAN_TYPE:
            del self.rx_buffer[0]
            return True

        payload_len = self.rx_buffer[3] | (self.rx_buffer[4] << 8)
        if payload_len != LIDAR_SCAN_PAYLOAD_LEN:
            del self.rx_buffer[0]
            return True
        if self.rx_buffer[LIDAR_SCAN_FRAME_LEN - 2:LIDAR_SCAN_FRAME_LEN] != LIDAR_SCAN_TAIL:
            del self.rx_buffer[0]
            return True

        payload_start = 5
        payload_end = payload_start + LIDAR_SCAN_PAYLOAD_LEN
        payload = bytes(self.rx_buffer[payload_start:payload_end])
        checksum = self.rx_buffer[payload_end]
        del self.rx_buffer[:LIDAR_SCAN_FRAME_LEN]

        checksum_sum = LIDAR_SCAN_TYPE
        checksum_sum += payload_len & 0xFF
        checksum_sum += payload_len >> 8
        checksum_sum += sum(payload)
        if (checksum_sum & 0xFF) != checksum:
            return True
        if self.publish_scan and self.scan_publisher is not None:
            self.scan_queue.put_nowait((payload, time.monotonic()))
        return True

    def publish_lidar_scan(self, scan_sample):
        if not self.publish_scan or self.scan_publisher is None:
            return
        payload, frame_time = scan_sample

        scan_time = LIDAR_DEFAULT_SCAN_TIME_S
        measured_scan_time = float("nan")
        used_fallback_scan_time = True
        if self.last_scan_frame_time is not None:
            measured_scan_time = frame_time - self.last_scan_frame_time
            if 0.02 <= measured_scan_time <= 0.30:
                scan_time = measured_scan_time
                used_fallback_scan_time = False
        self.last_scan_frame_time = frame_time

        # The serial frame represents a complete revolution. LaserScan requires
        # header.stamp to identify the first ray, so subtract both the time the
        # completed frame waited in the ROS queue and the scan duration itself.
        queue_age = max(0.0, time.monotonic() - frame_time)
        stamp_compensation = queue_age + scan_time
        ros_now = self.get_clock().now()
        scan_stamp = ros_now - Duration(seconds=stamp_compensation)
        if self.last_scan_stamp_ns is not None and scan_stamp.nanoseconds <= self.last_scan_stamp_ns:
            scan_stamp = Time(
                nanoseconds=self.last_scan_stamp_ns + 1,
                clock_type=ros_now.clock_type,
            )
        stamp_delta = (
            float("nan")
            if self.last_scan_stamp_ns is None
            else (scan_stamp.nanoseconds - self.last_scan_stamp_ns) * 1e-9
        )
        self.last_scan_stamp_ns = scan_stamp.nanoseconds

        scan = LaserScan()
        scan.header.stamp = scan_stamp.to_msg()
        scan.header.frame_id = self.scan_frame_id
        scan.angle_min = 0.0
        scan.angle_increment = (2.0 * math.pi) / LIDAR_POINT_COUNT
        scan.angle_max = scan.angle_min + (LIDAR_POINT_COUNT - 1) * scan.angle_increment
        scan.time_increment = scan_time / LIDAR_POINT_COUNT
        scan.scan_time = scan_time
        scan.range_min = self.lidar_range_min
        scan.range_max = self.lidar_range_max

        ranges = []
        for i in range(LIDAR_POINT_COUNT):
            payload_index = (-i) % LIDAR_POINT_COUNT if LIDAR_REVERSE_SCAN_DIRECTION else i
            distance_mm = payload[payload_index * 2] | (payload[payload_index * 2 + 1] << 8)
            distance_m = distance_mm / 1000.0
            if distance_m < scan.range_min or distance_m > scan.range_max:
                ranges.append(float("inf"))
            else:
                ranges.append(distance_m)
        if self.lidar_spike_filter:
            ranges = self.filter_lidar_spikes(ranges)
        scan.ranges = ranges
        self.scan_publisher.publish(scan)
        if self.scan_timing_publisher is not None:
            timing = Float64MultiArray()
            # measured_scan_time, queue_age, total stamp compensation,
            # first-ray stamp delta, fallback-used flag
            timing.data = [
                measured_scan_time,
                queue_age,
                stamp_compensation,
                stamp_delta,
                1.0 if used_fallback_scan_time else 0.0,
            ]
            self.scan_timing_publisher.publish(timing)

    def filter_lidar_spikes(self, ranges):
        filtered = list(ranges)
        count = len(ranges)
        for i, distance in enumerate(ranges):
            if not math.isfinite(distance):
                continue

            neighbor_count = 0
            for offset in range(1, self.lidar_spike_filter_window + 1):
                for neighbor_index in ((i - offset) % count, (i + offset) % count):
                    neighbor = ranges[neighbor_index]
                    if math.isfinite(neighbor) and abs(distance - neighbor) <= self.lidar_spike_filter_max_delta:
                        neighbor_count += 1

            if neighbor_count < self.lidar_spike_filter_min_neighbors:
                filtered[i] = float("inf")
        return filtered

    def discard_rx_loop(self):
        while self.running and rclpy.ok():
            try:
                waiting = self.serial.in_waiting
                if waiting > 0:
                    data = self.serial.read(waiting)
                    if (self.publish_scan or self.publish_odom or
                            self.publish_odom_tf or self.publish_imu):
                        self.rx_buffer.extend(data)
                        self.parse_rx_buffer()
                        if len(self.rx_buffer) > 4096:
                            del self.rx_buffer[:-1]
                else:
                    time.sleep(0.01)
            except serial.SerialException as exc:
                self.get_logger().error(f"Serial read discard failed: {exc}")
                time.sleep(0.5)

    def destroy_node(self):
        self.running = False
        try:
            self.send_velocity(0.0, 0.0, 0.0)
        except Exception:
            pass
        try:
            self.serial.close()
        except Exception:
            pass
        super().destroy_node()


def main(args=None):
    rclpy.init(args=args)
    node = Esp32CmdVelBridge()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
