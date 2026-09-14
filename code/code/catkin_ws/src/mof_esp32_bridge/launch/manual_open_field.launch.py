import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription, LogInfo
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    package_share = get_package_share_directory("mof_esp32_bridge")

    port = LaunchConfiguration("port")
    baud = LaunchConfiguration("baud")
    input_topic = LaunchConfiguration("input_topic")
    params_file = LaunchConfiguration("params_file")

    bridge_ekf = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(
            os.path.join(package_share, "launch", "esp32_ekf.launch.py")
        ),
        launch_arguments={
            "port": port,
            "baud": baud,
            "topic": "/cmd_vel",
            "allow_runtime_tuning": "true",
        }.items(),
    )

    # The manual open-field chain deliberately omits Collision Monitor:
    # cmd_vel_nav -> velocity_smoother -> cmd_vel -> bridge. The production
    # safe_velocity.launch.py remains unchanged and must be restored outside
    # supervised empty-field chassis tests.
    velocity_smoother = Node(
        package="nav2_velocity_smoother",
        executable="velocity_smoother",
        name="velocity_smoother",
        output="screen",
        parameters=[params_file],
        remappings=[
            ("cmd_vel", input_topic),
            ("cmd_vel_smoothed", "/cmd_vel"),
        ],
    )

    lifecycle_manager = Node(
        package="nav2_lifecycle_manager",
        executable="lifecycle_manager",
        name="lifecycle_manager_manual_open_field",
        output="screen",
        parameters=[{
            "autostart": True,
            "node_names": ["velocity_smoother"],
        }],
    )

    return LaunchDescription([
        LogInfo(msg="MANUAL OPEN-FIELD MODE: COLLISION MONITOR BYPASSED"),
        DeclareLaunchArgument("port", default_value="/dev/mof_esp32"),
        DeclareLaunchArgument("baud", default_value="921600"),
        DeclareLaunchArgument("input_topic", default_value="/cmd_vel_nav"),
        DeclareLaunchArgument(
            "params_file",
            default_value=os.path.join(
                package_share, "config", "mof_manual_open_field_params.yaml"
            ),
        ),
        bridge_ekf,
        velocity_smoother,
        lifecycle_manager,
    ])
