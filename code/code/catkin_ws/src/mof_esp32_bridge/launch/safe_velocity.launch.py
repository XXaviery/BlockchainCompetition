import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription
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
        }.items(),
    )

    velocity_smoother = Node(
        package="nav2_velocity_smoother",
        executable="velocity_smoother",
        name="velocity_smoother",
        output="screen",
        parameters=[params_file],
        remappings=[("cmd_vel", input_topic)],
    )

    collision_monitor = Node(
        package="nav2_collision_monitor",
        executable="collision_monitor",
        name="collision_monitor",
        output="screen",
        parameters=[params_file],
    )

    lifecycle_manager = Node(
        package="nav2_lifecycle_manager",
        executable="lifecycle_manager",
        name="lifecycle_manager_safe_velocity",
        output="screen",
        parameters=[{
            "autostart": True,
            "node_names": ["velocity_smoother", "collision_monitor"],
        }],
    )

    return LaunchDescription([
        DeclareLaunchArgument("port", default_value="/dev/mof_esp32"),
        DeclareLaunchArgument("baud", default_value="921600"),
        DeclareLaunchArgument("input_topic", default_value="/cmd_vel_nav"),
        DeclareLaunchArgument(
            "params_file",
            default_value=os.path.join(package_share, "config", "mof_nav2_params.yaml"),
        ),
        bridge_ekf,
        velocity_smoother,
        collision_monitor,
        lifecycle_manager,
    ])
