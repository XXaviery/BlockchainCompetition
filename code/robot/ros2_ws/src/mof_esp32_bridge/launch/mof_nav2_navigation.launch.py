import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, GroupAction, SetEnvironmentVariable
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node, SetParameter
from launch_ros.descriptions import ParameterFile
from nav2_common.launch import RewrittenYaml


def generate_launch_description():
    package_share = get_package_share_directory("mof_esp32_bridge")

    use_sim_time = LaunchConfiguration("use_sim_time")
    autostart = LaunchConfiguration("autostart")
    params_file = LaunchConfiguration("params_file")
    log_level = LaunchConfiguration("log_level")

    configured_params = ParameterFile(
        RewrittenYaml(
            source_file=params_file,
            root_key="",
            param_rewrites={"autostart": autostart},
            convert_types=True,
        ),
        allow_substs=True,
    )
    tf_remappings = [("/tf", "tf"), ("/tf_static", "tf_static")]
    upstream_remappings = tf_remappings + [("cmd_vel", "cmd_vel_nav")]
    lifecycle_nodes = [
        "controller_server",
        "smoother_server",
        "planner_server",
        "behavior_server",
        "velocity_smoother",
        "collision_monitor",
        "bt_navigator",
        "waypoint_follower",
    ]

    nodes = GroupAction([
        SetParameter("use_sim_time", use_sim_time),
        Node(
            package="nav2_controller",
            executable="controller_server",
            name="controller_server",
            output="screen",
            parameters=[configured_params],
            arguments=["--ros-args", "--log-level", log_level],
            remappings=upstream_remappings,
        ),
        Node(
            package="nav2_smoother",
            executable="smoother_server",
            name="smoother_server",
            output="screen",
            parameters=[configured_params],
            arguments=["--ros-args", "--log-level", log_level],
            remappings=tf_remappings,
        ),
        Node(
            package="nav2_planner",
            executable="planner_server",
            name="planner_server",
            output="screen",
            parameters=[configured_params],
            arguments=["--ros-args", "--log-level", log_level],
            remappings=tf_remappings,
        ),
        Node(
            package="nav2_behaviors",
            executable="behavior_server",
            name="behavior_server",
            output="screen",
            parameters=[configured_params],
            arguments=["--ros-args", "--log-level", log_level],
            remappings=upstream_remappings,
        ),
        Node(
            package="nav2_velocity_smoother",
            executable="velocity_smoother",
            name="velocity_smoother",
            output="screen",
            parameters=[configured_params],
            arguments=["--ros-args", "--log-level", log_level],
            remappings=upstream_remappings,
        ),
        Node(
            package="nav2_collision_monitor",
            executable="collision_monitor",
            name="collision_monitor",
            output="screen",
            parameters=[configured_params],
            arguments=["--ros-args", "--log-level", log_level],
            remappings=tf_remappings,
        ),
        Node(
            package="nav2_bt_navigator",
            executable="bt_navigator",
            name="bt_navigator",
            output="screen",
            parameters=[configured_params],
            arguments=["--ros-args", "--log-level", log_level],
            remappings=tf_remappings,
        ),
        Node(
            package="nav2_waypoint_follower",
            executable="waypoint_follower",
            name="waypoint_follower",
            output="screen",
            parameters=[configured_params],
            arguments=["--ros-args", "--log-level", log_level],
            remappings=tf_remappings,
        ),
        Node(
            package="nav2_lifecycle_manager",
            executable="lifecycle_manager",
            name="lifecycle_manager_navigation",
            output="screen",
            parameters=[{"autostart": autostart, "node_names": lifecycle_nodes}],
            arguments=["--ros-args", "--log-level", log_level],
        ),
    ])

    return LaunchDescription([
        SetEnvironmentVariable("RCUTILS_LOGGING_BUFFERED_STREAM", "1"),
        DeclareLaunchArgument("use_sim_time", default_value="False"),
        DeclareLaunchArgument("autostart", default_value="True"),
        DeclareLaunchArgument(
            "params_file",
            default_value=os.path.join(package_share, "config", "mof_nav2_params.yaml"),
        ),
        DeclareLaunchArgument("log_level", default_value="info"),
        nodes,
    ])
