import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription
from launch.conditions import IfCondition
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import EnvironmentVariable, LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    package_share = get_package_share_directory("mof_esp32_bridge")
    nav2_share = get_package_share_directory("nav2_bringup")

    map_file = LaunchConfiguration("map")
    params_file = LaunchConfiguration("params_file")
    use_sim_time = LaunchConfiguration("use_sim_time")
    use_rviz = LaunchConfiguration("use_rviz")
    rviz_config = LaunchConfiguration("rviz_config")

    return LaunchDescription([
        DeclareLaunchArgument(
            "map",
            description=(
                "Required validated map YAML. Do not use the legacy invalid room map."
            ),
        ),
        DeclareLaunchArgument(
            "params_file",
            default_value=os.path.join(package_share, "config", "mof_nav2_params.yaml"),
            description="Full path to the Nav2 parameter file.",
        ),
        DeclareLaunchArgument("use_sim_time", default_value="false"),
        DeclareLaunchArgument(
            "use_rviz",
            default_value="true",
            description="Whether to start RViz with Nav2.",
        ),
        DeclareLaunchArgument(
            "rviz_config",
            default_value=EnvironmentVariable("MOF_RVIZ_CONFIG", default_value=""),
            description="Deployment-provided RViz config path (MOF_RVIZ_CONFIG).",
        ),
        IncludeLaunchDescription(
            PythonLaunchDescriptionSource(
                os.path.join(nav2_share, "launch", "bringup_launch.py")
            ),
            launch_arguments={
                "map": map_file,
                "use_sim_time": use_sim_time,
                "params_file": params_file,
            }.items(),
        ),
        Node(
            condition=IfCondition(use_rviz),
            package="rviz2",
            executable="rviz2",
            name="rviz2",
            output="screen",
            arguments=["-d", rviz_config],
        ),
    ])
