import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription
from launch.conditions import IfCondition
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import EnvironmentVariable, LaunchConfiguration
from launch_ros.actions import Node
from nav2_common.launch import RewrittenYaml


def generate_launch_description():
    package_share = get_package_share_directory("mof_esp32_bridge")
    nav2_share = get_package_share_directory("nav2_bringup")

    port = LaunchConfiguration("port")
    baud = LaunchConfiguration("baud")
    map_file = LaunchConfiguration("map")
    params_file = LaunchConfiguration("params_file")
    ekf_params = LaunchConfiguration("ekf_params")
    use_sim_time = LaunchConfiguration("use_sim_time")
    use_rviz = LaunchConfiguration("use_rviz")
    rviz_config = LaunchConfiguration("rviz_config")

    # Explicitly inject the required map into map_server parameters. This is
    # retained in addition to the Nav2 launch argument because nested Jazzy
    # bringup otherwise left yaml_filename empty in real deployment.
    configured_nav2_params = RewrittenYaml(
        source_file=params_file,
        root_key="",
        param_rewrites={"yaml_filename": map_file},
        convert_types=True,
    )

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
        }.items(),
    )

    localization = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(
            os.path.join(nav2_share, "launch", "localization_launch.py")
        ),
        launch_arguments={
            "namespace": "",
            "map": map_file,
            "use_sim_time": use_sim_time,
            "params_file": configured_nav2_params,
            "autostart": "True",
            "use_composition": "False",
            "use_respawn": "False",
            "container_name": "nav2_container",
            "log_level": "info",
        }.items(),
    )

    # This local navigation launch intentionally omits Jazzy's route and
    # docking servers. The standard docking server publishes on final cmd_vel,
    # bypassing Collision Monitor. All retained motion producers enter
    # cmd_vel_nav before the single smoother and Collision Monitor.
    navigation = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(
            os.path.join(package_share, "launch", "mof_nav2_navigation.launch.py")
        ),
        launch_arguments={
            "use_sim_time": use_sim_time,
            "autostart": "True",
            "params_file": configured_nav2_params,
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
            "map",
            description=(
                "Required validated map YAML used by map_server and AMCL; "
                "do not use the legacy invalid room map."
            ),
        ),
        DeclareLaunchArgument(
            "params_file",
            default_value=os.path.join(package_share, "config", "mof_nav2_params.yaml"),
        ),
        DeclareLaunchArgument(
            "ekf_params",
            default_value=os.path.join(package_share, "config", "mof_ekf.yaml"),
        ),
        DeclareLaunchArgument("use_sim_time", default_value="false"),
        DeclareLaunchArgument("use_rviz", default_value="false"),
        DeclareLaunchArgument(
            "rviz_config",
            default_value=EnvironmentVariable("MOF_RVIZ_CONFIG", default_value=""),
            description="Deployment-provided RViz config path (MOF_RVIZ_CONFIG).",
        ),
        robot_and_ekf,
        localization,
        navigation,
        rviz,
    ])
