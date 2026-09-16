from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    port = LaunchConfiguration("port")
    baud = LaunchConfiguration("baud")
    topic = LaunchConfiguration("topic")
    message_type = LaunchConfiguration("message_type")
    publish_scan = LaunchConfiguration("publish_scan")
    scan_topic = LaunchConfiguration("scan_topic")
    scan_frame_id = LaunchConfiguration("scan_frame_id")
    lidar_range_min = LaunchConfiguration("lidar_range_min")
    lidar_range_max = LaunchConfiguration("lidar_range_max")
    lidar_spike_filter = LaunchConfiguration("lidar_spike_filter")
    lidar_spike_filter_window = LaunchConfiguration("lidar_spike_filter_window")
    lidar_spike_filter_min_neighbors = LaunchConfiguration("lidar_spike_filter_min_neighbors")
    lidar_spike_filter_max_delta = LaunchConfiguration("lidar_spike_filter_max_delta")
    publish_odom = LaunchConfiguration("publish_odom")
    odom_topic = LaunchConfiguration("odom_topic")
    odom_frame_id = LaunchConfiguration("odom_frame_id")
    base_frame_id = LaunchConfiguration("base_frame_id")
    publish_odom_tf = LaunchConfiguration("publish_odom_tf")
    wheel_odom_angular_scale = LaunchConfiguration("wheel_odom_angular_scale")
    publish_imu = LaunchConfiguration("publish_imu")
    imu_topic = LaunchConfiguration("imu_topic")
    imu_frame_id = LaunchConfiguration("imu_frame_id")
    allow_runtime_tuning = LaunchConfiguration("allow_runtime_tuning")
    laser_x = LaunchConfiguration("laser_x")
    laser_y = LaunchConfiguration("laser_y")
    laser_z = LaunchConfiguration("laser_z")
    laser_roll = LaunchConfiguration("laser_roll")
    laser_pitch = LaunchConfiguration("laser_pitch")
    laser_yaw = LaunchConfiguration("laser_yaw")

    return LaunchDescription([
        DeclareLaunchArgument("port", default_value="/dev/mof_esp32"),
        DeclareLaunchArgument("baud", default_value="921600"),
        DeclareLaunchArgument("topic", default_value="/cmd_vel"),
        DeclareLaunchArgument("message_type", default_value="twist"),
        DeclareLaunchArgument("publish_scan", default_value="true"),
        DeclareLaunchArgument("scan_topic", default_value="/scan"),
        DeclareLaunchArgument("scan_frame_id", default_value="laser"),
        DeclareLaunchArgument("lidar_range_min", default_value="0.02"),
        DeclareLaunchArgument("lidar_range_max", default_value="12.0"),
        DeclareLaunchArgument("lidar_spike_filter", default_value="true"),
        DeclareLaunchArgument("lidar_spike_filter_window", default_value="2"),
        DeclareLaunchArgument("lidar_spike_filter_min_neighbors", default_value="2"),
        DeclareLaunchArgument("lidar_spike_filter_max_delta", default_value="0.45"),
        DeclareLaunchArgument("publish_odom", default_value="true"),
        DeclareLaunchArgument("odom_topic", default_value="/odom"),
        DeclareLaunchArgument("odom_frame_id", default_value="odom"),
        DeclareLaunchArgument("base_frame_id", default_value="base_footprint"),
        DeclareLaunchArgument("publish_odom_tf", default_value="true"),
        DeclareLaunchArgument("wheel_odom_angular_scale", default_value="1.0"),
        DeclareLaunchArgument("publish_imu", default_value="true"),
        DeclareLaunchArgument("imu_topic", default_value="/imu"),
        DeclareLaunchArgument("imu_frame_id", default_value="imu_link"),
        DeclareLaunchArgument("allow_runtime_tuning", default_value="false"),
        DeclareLaunchArgument("laser_x", default_value="0.0"),
        DeclareLaunchArgument("laser_y", default_value="0.0"),
        DeclareLaunchArgument("laser_z", default_value="0.12"),
        DeclareLaunchArgument("laser_roll", default_value="0.0"),
        DeclareLaunchArgument("laser_pitch", default_value="0.0"),
        DeclareLaunchArgument("laser_yaw", default_value="1.5707963"),
        Node(
            package="mof_esp32_bridge",
            executable="esp32_cmd_vel_bridge",
            name="esp32_cmd_vel_bridge",
            output="screen",
            parameters=[{
                "port": port,
                "baud": baud,
                "topic": topic,
                "message_type": message_type,
                "publish_scan": publish_scan,
                "scan_topic": scan_topic,
                "scan_frame_id": scan_frame_id,
                "lidar_range_min": lidar_range_min,
                "lidar_range_max": lidar_range_max,
                "lidar_spike_filter": lidar_spike_filter,
                "lidar_spike_filter_window": lidar_spike_filter_window,
                "lidar_spike_filter_min_neighbors": lidar_spike_filter_min_neighbors,
                "lidar_spike_filter_max_delta": lidar_spike_filter_max_delta,
                "publish_odom": publish_odom,
                "odom_topic": odom_topic,
                "odom_frame_id": odom_frame_id,
                "base_frame_id": base_frame_id,
                "publish_odom_tf": publish_odom_tf,
                "wheel_odom_angular_scale": wheel_odom_angular_scale,
                "publish_imu": publish_imu,
                "imu_topic": imu_topic,
                "imu_frame_id": imu_frame_id,
                "allow_runtime_tuning": allow_runtime_tuning,
            }],
        ),
        Node(
            package="tf2_ros",
            executable="static_transform_publisher",
            name="base_to_imu_tf",
            arguments=[
                "--x", "0",
                "--y", "0",
                "--z", "0",
                "--yaw", "0",
                "--pitch", "0",
                "--roll", "0",
                "--frame-id", "base_link",
                "--child-frame-id", imu_frame_id,
            ],
        ),
        Node(
            package="tf2_ros",
            executable="static_transform_publisher",
            name="footprint_to_link_tf",
            arguments=[
                "--x", "0",
                "--y", "0",
                "--z", "0",
                "--yaw", "0",
                "--pitch", "0",
                "--roll", "0",
                "--frame-id", base_frame_id,
                "--child-frame-id", "base_link",
            ],
        ),
        Node(
            package="tf2_ros",
            executable="static_transform_publisher",
            name="base_to_laser_tf",
            arguments=[
                "--x", laser_x,
                "--y", laser_y,
                "--z", laser_z,
                "--yaw", laser_yaw,
                "--pitch", laser_pitch,
                "--roll", laser_roll,
                "--frame-id", base_frame_id,
                "--child-frame-id", scan_frame_id,
            ],
        ),
    ])
