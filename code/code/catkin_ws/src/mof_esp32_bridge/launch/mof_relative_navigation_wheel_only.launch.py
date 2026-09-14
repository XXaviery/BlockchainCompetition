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
    safety_params = LaunchConfiguration("safety_params")
    relative_params = LaunchConfiguration("relative_params")
    wheel_ekf_params = os.path.join(
        package_share, "config", "mof_ekf_nav_wheel_only.yaml"
    )

    bridge_ekf = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(
            os.path.join(package_share, "launch", "esp32_ekf.launch.py")
        ),
        launch_arguments={
            "port": port,
            "baud": baud,
            "topic": "/cmd_vel",
            "message_type": "twist",
            "ekf_params": wheel_ekf_params,
            "use_imu_bias_corrector": "false",
            "allow_runtime_tuning": "false",
        }.items(),
    )

    velocity_smoother = Node(
        package="nav2_velocity_smoother",
        executable="velocity_smoother",
        name="velocity_smoother",
        output="screen",
        parameters=[safety_params],
        remappings=[
            ("cmd_vel", "/cmd_vel_nav"),
            ("cmd_vel_smoothed", "/cmd_vel_smoothed"),
        ],
    )

    collision_monitor = Node(
        package="nav2_collision_monitor",
        executable="collision_monitor",
        name="collision_monitor",
        output="screen",
        parameters=[safety_params],
        remappings=[
            ("cmd_vel_smoothed", "/cmd_vel_smoothed"),
            ("cmd_vel", "/cmd_vel"),
        ],
    )

    lifecycle_manager = Node(
        package="nav2_lifecycle_manager",
        executable="lifecycle_manager",
        name="lifecycle_manager_relative_navigation",
        output="screen",
        parameters=[{
            "autostart": True,
            "node_names": ["velocity_smoother", "collision_monitor"],
        }],
    )

    relative_navigation = Node(
        package="mof_esp32_bridge",
        executable="relative_navigation_node",
        name="relative_navigation_node",
        output="screen",
        parameters=[relative_params],
    )

    return LaunchDescription([
        DeclareLaunchArgument("port", default_value="/dev/mof_esp32"),
        DeclareLaunchArgument("baud", default_value="921600"),
        DeclareLaunchArgument(
            "safety_params",
            default_value=os.path.join(
                package_share, "config", "mof_nav2_params.yaml"
            ),
        ),
        DeclareLaunchArgument(
            "relative_params",
            default_value=os.path.join(
                package_share,
                "config",
                "mof_relative_navigation_wheel_only.yaml",
            ),
        ),
        bridge_ekf,
        velocity_smoother,
        collision_monitor,
        lifecycle_manager,
        relative_navigation,
    ])
