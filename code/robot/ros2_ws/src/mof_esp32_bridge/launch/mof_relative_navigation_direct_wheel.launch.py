import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, EmitEvent, RegisterEventHandler, TimerAction
from launch.conditions import IfCondition
from launch.event_handlers import OnProcessStart
from launch.events import matches_action
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import LifecycleNode, Node
from launch_ros.event_handlers import OnStateTransition
from launch_ros.events.lifecycle import ChangeState
from lifecycle_msgs.msg import Transition


def generate_launch_description():
    package_share = get_package_share_directory("mof_esp32_bridge")
    port = LaunchConfiguration("port")
    params = LaunchConfiguration("params")
    enable_relative_navigation = LaunchConfiguration("enable_relative_navigation")

    bridge = Node(
        package="mof_esp32_bridge",
        executable="esp32_cmd_vel_bridge",
        name="esp32_cmd_vel_bridge",
        output="screen",
        parameters=[{
            "port": port,
            "baud": 921600,
            "topic": "/cmd_vel",
            "message_type": "twist",
            "timeout_s": 0.5,
            "max_vx": 0.20,
            "max_vy": 0.20,
            "max_w": 0.35,
            "discard_rx": True,
            "publish_scan": False,
            "publish_imu": False,
            "publish_odom": True,
            "odom_topic": "/wheel/odom",
            "odom_frame_id": "odom",
            "base_frame_id": "base_footprint",
            "publish_odom_tf": True,
            "wheel_odom_angular_scale": 1.0,
            "publish_chassis_debug": True,
            "chassis_debug_topic": "/chassis/debug",
            "allow_runtime_tuning": False,
        }],
    )

    smoother = LifecycleNode(
        package="nav2_velocity_smoother",
        executable="velocity_smoother",
        name="velocity_smoother",
        namespace="",
        output="screen",
        parameters=[params],
        remappings=[
            ("cmd_vel", "/cmd_vel_nav"),
            ("cmd_vel_smoothed", "/cmd_vel"),
        ],
    )

    configure_smoother = RegisterEventHandler(
        OnProcessStart(
            target_action=smoother,
            on_start=[TimerAction(
                period=0.5,
                actions=[EmitEvent(event=ChangeState(
                    lifecycle_node_matcher=matches_action(smoother),
                    transition_id=Transition.TRANSITION_CONFIGURE,
                ))],
            )],
        )
    )
    activate_smoother = RegisterEventHandler(
        OnStateTransition(
            target_lifecycle_node=smoother,
            goal_state="inactive",
            entities=[EmitEvent(event=ChangeState(
                lifecycle_node_matcher=matches_action(smoother),
                transition_id=Transition.TRANSITION_ACTIVATE,
            ))],
        )
    )

    relative_navigation = Node(
        package="mof_esp32_bridge",
        executable="relative_navigation_node",
        name="relative_navigation_node",
        output="screen",
        parameters=[params],
        remappings=[("/odom", "/wheel/odom")],
        condition=IfCondition(enable_relative_navigation),
    )

    return LaunchDescription([
        DeclareLaunchArgument("port", default_value="/dev/mof_esp32"),
        DeclareLaunchArgument(
            "params",
            default_value=os.path.join(
                package_share, "config", "mof_relative_navigation_direct_wheel.yaml"
            ),
        ),
        DeclareLaunchArgument("enable_relative_navigation", default_value="true"),
        configure_smoother,
        activate_smoother,
        bridge,
        smoother,
        relative_navigation,
    ])
