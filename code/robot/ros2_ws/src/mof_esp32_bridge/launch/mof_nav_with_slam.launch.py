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
    slam_share = get_package_share_directory("slam_toolbox")

    port = LaunchConfiguration("port")
    baud = LaunchConfiguration("baud")
    params_file = LaunchConfiguration("params_file")
    ekf_params = LaunchConfiguration("ekf_params")
    slam_params = LaunchConfiguration("slam_params")
    use_sim_time = LaunchConfiguration("use_sim_time")
    use_rviz = LaunchConfiguration("use_rviz")
    rviz_config = LaunchConfiguration("rviz_config")
    use_imu_bias_corrector = LaunchConfiguration("use_imu_bias_corrector")

    robot_and_ekf = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(
            os.path.join(package_share, "launch", "esp32_ekf.launch.py")
        ),
        launch_arguments={
            "port": port,
            "baud": baud,
            "topic": "/cmd_vel",
            "message_type": "twist",
            "ekf_params": ekf_params,
            "use_imu_bias_corrector": use_imu_bias_corrector,
        }.items(),
    )

    # Mapping mode is deliberate: the legacy room map is invalid and a
    # stationary single-scan map is not a valid AMCL reference. SLAM Toolbox
    # is the sole map->odom publisher for this entry.
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

    navigation = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(
            os.path.join(package_share, "launch", "mof_nav2_navigation.launch.py")
        ),
        launch_arguments={
            "use_sim_time": use_sim_time,
            "autostart": "True",
            "params_file": params_file,
            "log_level": "info",
        }.items(),
    )

    rviz = Node(
        condition=IfCondition(use_rviz),
        package="rviz2",
        executable="rviz2",
        name="rviz2",
        output="screen",
        arguments=["-d", rviz_config],
    )

    return LaunchDescription([
        DeclareLaunchArgument("port", default_value="/dev/mof_esp32"),
        DeclareLaunchArgument("baud", default_value="921600"),
        DeclareLaunchArgument(
            "params_file",
            default_value=os.path.join(package_share, "config", "mof_nav2_params.yaml"),
        ),
        DeclareLaunchArgument(
            "ekf_params",
            default_value=os.path.join(package_share, "config", "mof_ekf.yaml"),
        ),
        DeclareLaunchArgument(
            "slam_params",
            default_value=os.path.join(package_share, "config", "mof_slam_params.yaml"),
        ),
        DeclareLaunchArgument("use_sim_time", default_value="false"),
        DeclareLaunchArgument("use_imu_bias_corrector", default_value="false"),
        DeclareLaunchArgument("use_rviz", default_value="false"),
        DeclareLaunchArgument(
            "rviz_config",
            default_value=EnvironmentVariable("MOF_RVIZ_CONFIG", default_value=""),
            description="Deployment-provided RViz config path (MOF_RVIZ_CONFIG).",
        ),
        robot_and_ekf,
        slam,
        navigation,
        rviz,
    ])
