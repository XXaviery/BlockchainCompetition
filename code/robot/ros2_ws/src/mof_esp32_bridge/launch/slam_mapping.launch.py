import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration


def generate_launch_description():
    package_share = get_package_share_directory("mof_esp32_bridge")
    slam_share = get_package_share_directory("slam_toolbox")

    params_file = LaunchConfiguration("params_file")
    use_sim_time = LaunchConfiguration("use_sim_time")

    return LaunchDescription([
        DeclareLaunchArgument(
            "params_file",
            default_value=os.path.join(package_share, "config", "mof_slam_toolbox.yaml"),
            description="Full path to the slam_toolbox parameter file.",
        ),
        DeclareLaunchArgument("use_sim_time", default_value="false"),
        IncludeLaunchDescription(
            PythonLaunchDescriptionSource(
                os.path.join(slam_share, "launch", "online_async_launch.py")
            ),
            launch_arguments={
                "use_sim_time": use_sim_time,
                "slam_params_file": params_file,
            }.items(),
        ),
    ])
