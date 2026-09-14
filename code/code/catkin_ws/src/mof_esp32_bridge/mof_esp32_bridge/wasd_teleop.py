import argparse
import select
import signal
import sys
import termios
import time
import tty

import rclpy
from geometry_msgs.msg import Twist
from rclpy.node import Node
from rclpy.signals import SignalHandlerOptions


SAFE_TOPIC = "/cmd_vel_nav"
MIN_LINEAR_SPEED = 0.10
MAX_FORWARD_SPEED = 0.25
MAX_LATERAL_SPEED = 0.30
DEFAULT_LINEAR_SPEED = 0.20
DEFAULT_LATERAL_SPEED = 0.30
MAX_ANGULAR_SPEED = 0.35
MIN_PUBLISH_RATE = 5.0
MAX_PUBLISH_RATE = 50.0
ZERO_TAIL_SECONDS = 1.5


HELP_TEXT = """
WASD teleop:
  w: forward     s: backward
  a: left        d: right
  q: rotate CCW  e: rotate CW
  A motion key selects a command that is then published at a fixed rate.
  space: stop
  Ctrl-C: stop and exit
"""


class WasdTeleop(Node):
    def __init__(self):
        super().__init__("wasd_teleop")
        self.publisher = self.create_publisher(Twist, SAFE_TOPIC, 10)

    def publish_velocity(self, vx, vy, wz):
        msg = Twist()
        msg.linear.x = vx
        msg.linear.y = vy
        msg.angular.z = wz
        self.publisher.publish(msg)


def bounded_float(name, minimum, maximum):
    def parse(value):
        parsed = float(value)
        if not minimum <= parsed <= maximum:
            raise argparse.ArgumentTypeError(
                f"{name} must be in [{minimum}, {maximum}]"
            )
        return parsed

    return parse


def read_key(timeout):
    readable, _, _ = select.select([sys.stdin], [], [], timeout)
    return sys.stdin.read(1).lower() if readable else None


def zero_tail(node, publish_rate, seconds=ZERO_TAIL_SECONDS):
    period = 1.0 / publish_rate
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        node.publish_velocity(0.0, 0.0, 0.0)
        rclpy.spin_once(node, timeout_sec=0.0)
        time.sleep(period)


def main(args=None):
    cli_args = sys.argv[1:] if args is None else args
    parser = argparse.ArgumentParser(
        description="Safely publish fixed-rate WASD commands through /cmd_vel_nav."
    )
    parser.add_argument(
        "--topic",
        default=SAFE_TOPIC,
        choices=[SAFE_TOPIC],
        help="Safety-chain input; other topics are deliberately rejected.",
    )
    parser.add_argument(
        "--linear-speed",
        type=bounded_float("--linear-speed", MIN_LINEAR_SPEED, MAX_FORWARD_SPEED),
        default=DEFAULT_LINEAR_SPEED,
        help="Forward/backward speed in m/s; defaults to 0.20 in manual open-field mode.",
    )
    parser.add_argument(
        "--lateral-speed",
        type=bounded_float("--lateral-speed", MIN_LINEAR_SPEED, MAX_LATERAL_SPEED),
        default=DEFAULT_LATERAL_SPEED,
        help="Left/right speed in m/s; defaults to 0.30 so split wheel targets are 0.15 m/s.",
    )
    parser.add_argument(
        "--angular-speed",
        type=bounded_float("--angular-speed", 0.0, MAX_ANGULAR_SPEED),
        default=0.35,
    )
    parser.add_argument(
        "--publish-rate",
        type=bounded_float("--publish-rate", MIN_PUBLISH_RATE, MAX_PUBLISH_RATE),
        default=20.0,
    )
    parsed_args, ros_args = parser.parse_known_args(cli_args)

    if not sys.stdin.isatty():
        parser.error("WASD teleop requires an interactive terminal")

    rclpy.init(args=ros_args, signal_handler_options=SignalHandlerOptions.NO)
    node = WasdTeleop()
    old_settings = termios.tcgetattr(sys.stdin.fileno())
    stop_requested = False

    def request_stop(_signum, _frame):
        nonlocal stop_requested
        stop_requested = True

    old_handlers = {}
    for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        old_handlers[signum] = signal.signal(signum, request_stop)

    linear = parsed_args.linear_speed
    lateral = parsed_args.lateral_speed
    angular = parsed_args.angular_speed
    bindings = {
        "w": (linear, 0.0, 0.0),
        "s": (-linear, 0.0, 0.0),
        "a": (0.0, lateral, 0.0),
        "d": (0.0, -lateral, 0.0),
        "q": (0.0, 0.0, angular),
        "e": (0.0, 0.0, -angular),
    }
    command = (0.0, 0.0, 0.0)
    period = 1.0 / parsed_args.publish_rate

    print(HELP_TEXT)
    try:
        tty.setcbreak(sys.stdin.fileno())
        while rclpy.ok() and not stop_requested:
            started = time.monotonic()
            key = read_key(period)
            if key in bindings:
                command = bindings[key]
            elif key == " ":
                command = (0.0, 0.0, 0.0)
                node.publish_velocity(*command)
            elif key == "\x03":
                break
            node.publish_velocity(*command)
            rclpy.spin_once(node, timeout_sec=0.0)
            remaining = period - (time.monotonic() - started)
            if remaining > 0:
                time.sleep(remaining)
    finally:
        try:
            zero_tail(node, parsed_args.publish_rate)
        finally:
            termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, old_settings)
            for signum, old_handler in old_handlers.items():
                signal.signal(signum, old_handler)
            node.destroy_node()
            rclpy.shutdown()


if __name__ == "__main__":
    main()
