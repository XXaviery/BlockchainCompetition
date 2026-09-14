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
    params_file = LaunchConfiguration("params_file")
    ekf_params = LaunchConfiguration("ekf_params")
    slam_params = LaunchConfiguration("slam_params")
    bias_params = LaunchConfiguration("bias_params")

    mapping = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(
            os.path.join(package_share, "launch", "mof_mapping_only.launch.py")
        ),
        launch_arguments={
            "port": port,
            "baud": baud,
            "ekf_params": ekf_params,
            "slam_params": slam_params,
            "bias_params": bias_params,
        }.items(),
    )
    smoother = Node(
        package="nav2_velocity_smoother",
        executable="velocity_smoother",
        name="velocity_smoother",
        output="screen",
        parameters=[params_file],
        remappings=[("cmd_vel", "/cmd_vel_nav")],
    )
    monitor = Node(
        package="nav2_collision_monitor",
        executable="collision_monitor",
        name="collision_monitor",
        output="screen",
        parameters=[params_file],
    )
    lifecycle = Node(
        package="nav2_lifecycle_manager",
        executable="lifecycle_manager",
        name="lifecycle_manager_mapping_teleop",
        output="screen",
        parameters=[{
            "autostart": True,
            "node_names": ["velocity_smoother", "collision_monitor"],
        }],
    )
    return LaunchDescription([
        DeclareLaunchArgument("port", default_value="/dev/mof_esp32"),
        DeclareLaunchArgument("baud", default_value="921600"),
        DeclareLaunchArgument(
            "params_file", default_value=os.path.join(package_share, "config", "mof_nav2_params.yaml")
        ),
        DeclareLaunchArgument(
            "ekf_params", default_value=os.path.join(package_share, "config", "mof_ekf.yaml")
        ),
        DeclareLaunchArgument(
            "slam_params", default_value=os.path.join(package_share, "config", "mof_slam_diagnostic_params.yaml")
        ),
        DeclareLaunchArgument(
            "bias_params", default_value=os.path.join(package_share, "config", "mof_imu_bias.yaml")
        ),
        mapping,
        smoother,
        monitor,
        lifecycle,
    ])
