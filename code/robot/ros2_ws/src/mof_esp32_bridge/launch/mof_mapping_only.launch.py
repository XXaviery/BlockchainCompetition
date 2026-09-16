import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration


def generate_launch_description():
    package_share = get_package_share_directory("mof_esp32_bridge")
    slam_share = get_package_share_directory("slam_toolbox")

    port = LaunchConfiguration("port")
    baud = LaunchConfiguration("baud")
    ekf_params = LaunchConfiguration("ekf_params")
    slam_params = LaunchConfiguration("slam_params")
    bias_params = LaunchConfiguration("bias_params")
    use_sim_time = LaunchConfiguration("use_sim_time")

    robot_and_ekf = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(
            os.path.join(package_share, "launch", "esp32_ekf.launch.py")
        ),
        launch_arguments={
            "port": port,
            "baud": baud,
            # Mapping-only never owns or publishes a motion command. The bridge
            # remains subscribed to the final interface for telemetry and its
            # own command watchdog, but no controller is launched here.
            "topic": "/cmd_vel",
            "message_type": "twist",
            "ekf_params": ekf_params,
            "bias_params": bias_params,
        }.items(),
    )

    slam = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(
            os.path.join(slam_share, "launch", "online_async_launch.py")
        ),
        launch_arguments={
            "autostart": "true",
            "use_lifecycle_manager": "false",
            "use_sim_time": use_sim_time,
            "slam_params_file": slam_params,
        }.items(),
    )

    return LaunchDescription([
        DeclareLaunchArgument("port", default_value="/dev/mof_esp32"),
        DeclareLaunchArgument("baud", default_value="921600"),
        DeclareLaunchArgument(
            "ekf_params",
            default_value=os.path.join(package_share, "config", "mof_ekf.yaml"),
        ),
        DeclareLaunchArgument(
            "slam_params",
            default_value=os.path.join(
                package_share, "config", "mof_slam_diagnostic_params.yaml"
            ),
        ),
        DeclareLaunchArgument("use_sim_time", default_value="false"),
        DeclareLaunchArgument(
            "bias_params",
            default_value=os.path.join(package_share, "config", "mof_imu_bias.yaml"),
        ),
        robot_and_ekf,
        slam,
    ])
