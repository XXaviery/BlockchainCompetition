import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription
from launch.conditions import IfCondition
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    package_share = get_package_share_directory("mof_esp32_bridge")

    port = LaunchConfiguration("port")
    baud = LaunchConfiguration("baud")
    topic = LaunchConfiguration("topic")
    message_type = LaunchConfiguration("message_type")
    ekf_params = LaunchConfiguration("ekf_params")
    allow_runtime_tuning = LaunchConfiguration("allow_runtime_tuning")
    bias_params = LaunchConfiguration("bias_params")
    use_imu_bias_corrector = LaunchConfiguration("use_imu_bias_corrector")

    bridge_launch = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(
            os.path.join(package_share, "launch", "esp32_bridge.launch.py")
        ),
        launch_arguments={
            "port": port,
            "baud": baud,
            "topic": topic,
            "message_type": message_type,
            "publish_odom": "true",
            "odom_topic": "/wheel/odom",
            "publish_odom_tf": "false",
            "publish_imu": "true",
            "imu_topic": "/imu",
            "allow_runtime_tuning": allow_runtime_tuning,
        }.items(),
    )

    ekf_node = Node(
        package="robot_localization",
        executable="ekf_node",
        name="ekf_filter_node",
        output="screen",
        parameters=[ekf_params],
        remappings=[("odometry/filtered", "/odom")],
    )

    bias_node = Node(
        condition=IfCondition(use_imu_bias_corrector),
        package="mof_esp32_bridge",
        executable="imu_bias_corrector",
        name="imu_bias_corrector",
        output="screen",
        parameters=[bias_params],
    )

    return LaunchDescription([
        DeclareLaunchArgument("port", default_value="/dev/mof_esp32"),
        DeclareLaunchArgument("baud", default_value="921600"),
        DeclareLaunchArgument("topic", default_value="/cmd_vel"),
        DeclareLaunchArgument("message_type", default_value="twist"),
        DeclareLaunchArgument("allow_runtime_tuning", default_value="false"),
        DeclareLaunchArgument("use_imu_bias_corrector", default_value="false"),
        DeclareLaunchArgument(
            "bias_params",
            default_value=os.path.join(package_share, "config", "mof_imu_bias.yaml"),
        ),
        DeclareLaunchArgument(
            "ekf_params",
            default_value=os.path.join(package_share, "config", "mof_ekf.yaml"),
        ),
        bridge_launch,
        bias_node,
        ekf_node,
    ])
