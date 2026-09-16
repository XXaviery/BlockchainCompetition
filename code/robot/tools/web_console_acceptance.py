#!/usr/bin/env python3
"""Offline safety acceptance for the MOF Web console.

This tool never imports a ROS workspace, opens a serial device, or publishes a
robot command. It exercises ordering, watchdog/exit zeroing, the playback
allowlist, and a simulated process-group play/pause/resume/stop lifecycle.
"""

from __future__ import annotations

import argparse
import ast
import contextlib
from email.message import Message
import importlib.util
import io
import json
import os
from pathlib import Path
import signal
import stat
import subprocess
import sys
import tempfile
import time
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SERVER_PATH = PROJECT_ROOT / "web" / "server.py"
TASK_BEFORE_DIR: Path | None = None


def load_server():
    pollution_path = PROJECT_ROOT / "web" / "pollution_demo.py"
    pollution_spec = importlib.util.spec_from_file_location("pollution_demo", pollution_path)
    if pollution_spec is None or pollution_spec.loader is None:
        raise RuntimeError("unable to load web/pollution_demo.py")
    pollution_module = importlib.util.module_from_spec(pollution_spec)
    sys.modules["pollution_demo"] = pollution_module
    pollution_spec.loader.exec_module(pollution_module)
    spec = importlib.util.spec_from_file_location("mof_web_server_acceptance", SERVER_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("unable to load web/server.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class FakeMotion:
    def __init__(self) -> None:
        self.command = (0.0, 0.0, 0.0)
        self.timeline: list[dict[str, Any]] = []
        self.available = True
        self.error = ""
        self.suspended = False

    @property
    def safety_subscribers(self) -> int:
        return 1

    def _event(self, action: str) -> None:
        self.timeline.append(
            {
                "monotonic": round(time.monotonic(), 6),
                "action": action,
                "command": list(self.command),
            }
        )

    def set_command(self, vx: float, vy: float, wz: float) -> None:
        self.command = (vx, vy, wz)
        self._event("motion-accepted")

    def stop(self) -> None:
        self.command = (0.0, 0.0, 0.0)
        self._event("stop-accepted")

    def suspend(self) -> None:
        self.suspended = True
        self._event("publisher-suspended")

    def resume(self) -> None:
        self.suspended = False
        self.command = (0.0, 0.0, 0.0)
        self._event("publisher-resumed")

    def publish_zero_burst(self, source: str = "replay-zero") -> dict[str, Any]:
        self._event(source)
        return {"duration_seconds": 0.0, "zero_publish_count": 1, "timeline": []}


class FakePublisher:
    def __init__(self) -> None:
        self.messages: list[tuple[float, float, float]] = []

    def publish(self, message) -> None:
        self.messages.append((message.linear.x, message.linear.y, message.angular.z))

    def get_subscription_count(self) -> int:
        return 1


class FakeVector:
    def __init__(self) -> None:
        self.x = 0.0
        self.y = 0.0
        self.z = 0.0


class FakeTwist:
    def __init__(self) -> None:
        self.linear = FakeVector()
        self.angular = FakeVector()


def test_ast(server) -> dict[str, Any]:
    source = SERVER_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(SERVER_PATH))
    compile(tree, str(SERVER_PATH), "exec")
    return {"file": str(SERVER_PATH), "nodes": sum(1 for _ in ast.walk(tree))}


def test_browser_interlocks(_server) -> dict[str, Any]:
    main_js = (PROJECT_ROOT / "web" / "main.js").read_text(encoding="utf-8")
    required_fragments = [
        "commandSequence",
        "browserSessionId",
        "session_id: browserSessionId",
        "motionAbortController?.abort()",
        'window.setInterval(() => sendMotion(activeMotion), 50)',
        'key.addEventListener("pointerup"',
        'key.addEventListener("pointercancel"',
        'key.addEventListener("lostpointercapture"',
        'document.addEventListener("keyup"',
        'window.addEventListener("blur"',
        'document.addEventListener("visibilitychange"',
        'window.addEventListener("pagehide"',
        "function closeConsole() {\n  stopMotion();",
        "/api/bags/heartbeat",
        "Replaying recorded motion",
        "Live motion replay started",
        "motion replay ·",
        "Pause unavailable for motion replay",
    ]
    missing = [fragment for fragment in required_fragments if fragment not in main_js]
    if missing:
        raise AssertionError(f"browser safety interlocks missing: {missing}")
    return {"required_interlocks": len(required_fragments), "missing": []}


def test_request_ordering(server) -> dict[str, Any]:
    motion = FakeMotion()
    controller = server.SequencedMotionController(motion)
    session_id = "offline_sequence_session_0001"
    events: list[dict[str, Any]] = []

    accepted = controller.apply(session_id, 1, (0.2, 0.0, 0.0))
    events.append({"request": "motion", "seq": 1, "result": "accepted", **accepted})
    accepted = controller.apply(session_id, 2, None)
    events.append({"request": "stop", "seq": 2, "result": "accepted", **accepted})
    try:
        controller.apply(session_id, 1, (0.2, 0.0, 0.0))
    except server.StaleCommandError as exc:
        events.append({"request": "delayed-motion", "seq": 1, "result": "rejected", "error": str(exc)})
    else:
        raise AssertionError("delayed motion was not rejected")
    try:
        controller.apply(session_id, 2, None)
    except server.StaleCommandError as exc:
        events.append({"request": "duplicate-stop", "seq": 2, "result": "rejected", "error": str(exc)})
    else:
        raise AssertionError("duplicate sequence was not rejected")
    try:
        controller.apply("different_browser_session_0002", 3, (0.2, 0.0, 0.0))
    except server.StaleCommandError as exc:
        events.append({"request": "other-session-motion", "seq": 3, "result": "rejected", "error": str(exc)})
    else:
        raise AssertionError("a second browser session took over the command stream")

    accepted = controller.apply("different_browser_session_0002", 4, None)
    events.append({"request": "other-session-stop-takeover", "seq": 4, "result": "accepted", **accepted})
    try:
        controller.apply(session_id, 5, (0.2, 0.0, 0.0))
    except server.StaleCommandError as exc:
        events.append({"request": "old-session-motion", "seq": 5, "result": "rejected", "error": str(exc)})
    else:
        raise AssertionError("an old browser session regained motion control after safe takeover")

    if motion.command != (0.0, 0.0, 0.0):
        raise AssertionError(f"final command is not zero: {motion.command}")
    return {"requests": events, "motion_timeline": motion.timeline, "final_command": list(motion.command)}


def test_watchdog_and_shutdown_zero(server) -> dict[str, Any]:
    original_twist = server.Twist
    server.Twist = FakeTwist
    try:
        motion = server.MotionPublisher(enabled=False)
        publisher = FakePublisher()
        motion.available = True
        motion.error = ""
        motion.publisher = publisher
        with motion._lock:
            motion._command = (0.2, 0.0, 0.0)
            motion._last_refresh = time.monotonic() - server.WATCHDOG_SECONDS - 0.1
        motion._publish_tick()
        if motion.command != (0.0, 0.0, 0.0) or publisher.messages[-1] != (0.0, 0.0, 0.0):
            raise AssertionError("watchdog did not publish zero")
        report = motion.publish_shutdown_zero_burst()
        if report["duration_seconds"] <= server.WATCHDOG_SECONDS:
            raise AssertionError("shutdown zero burst did not exceed one watchdog period")
        if report["zero_publish_count"] < 2:
            raise AssertionError("shutdown zero burst published too few messages")
        if any(event["command"] != [0.0, 0.0, 0.0] for event in report["timeline"]):
            raise AssertionError("shutdown timeline contains a non-zero command")

        published_before = len(publisher.messages)
        motion.suspend()
        with motion._lock:
            motion._command = (0.2, 0.0, 0.0)
        motion._publish_tick()
        if len(publisher.messages) != published_before:
            raise AssertionError("suspended publisher emitted messages")
        motion.resume()
        motion._publish_tick()
        if motion.command != (0.0, 0.0, 0.0) or publisher.messages[-1] != (0.0, 0.0, 0.0):
            raise AssertionError("publisher resume did not restore zero publishing")
        return {
            "watchdog_seconds": server.WATCHDOG_SECONDS,
            "watchdog_publish": list(publisher.messages[0]),
            "shutdown_report": report,
            "total_fake_publishes": len(publisher.messages),
        }
    finally:
        server.Twist = original_twist


def metadata_text() -> str:
    topics = ["/cmd_vel_nav", "/cmd_vel", "/tf", "/chassis/debug", "/wheel/odom", "/navigate_to_pose/_action/goal"]
    rows = [
        "rosbag2_bagfile_information:",
        "  duration:",
        "    nanoseconds: 10000000000",
        "  starting_time:",
        "    nanoseconds_since_epoch: 1700000000000000000",
        "  message_count: 100",
        "  topics_with_message_count:",
    ]
    for topic in topics:
        rows.extend(["    - topic_metadata:", f"        name: {topic}"])
    return "\n".join(rows) + "\n"


def telemetry_metadata_text() -> str:
    topics = ["/tf", "/chassis/debug", "/wheel/odom", "/navigate_to_pose/_action/goal"]
    rows = [
        "rosbag2_bagfile_information:",
        "  duration:",
        "    nanoseconds: 10000000000",
        "  starting_time:",
        "    nanoseconds_since_epoch: 1700000000000000000",
        "  message_count: 100",
        "  topics_with_message_count:",
    ]
    for topic in topics:
        rows.extend(["    - topic_metadata:", f"        name: {topic}"])
    return "\n".join(rows) + "\n"


def make_fake_ros2(path: Path) -> None:
    path.write_text(
        """#!/usr/bin/env python3
import os
import signal
import sys
import time

def stop(_signum, _frame):
    print("fake-player-stop", flush=True)
    raise SystemExit(0)

signal.signal(signal.SIGINT, stop)
signal.signal(signal.SIGTERM, stop)
print("fake-player-start " + " ".join(sys.argv[1:]), flush=True)
print("fake-player-domain=" + os.environ.get("ROS_DOMAIN_ID", ""), flush=True)
while True:
    print("tick", flush=True)
    time.sleep(0.04)
""",
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def test_bag_allowlist_and_lifecycle(server) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="mof_web_acceptance_") as temporary:
        root = Path(temporary)
        bag = root / "bag"
        bag.mkdir()
        (bag / "metadata.yaml").write_text(telemetry_metadata_text(), encoding="utf-8")
        (bag / "bag_0.mcap").write_bytes(b"offline-simulation")
        fake_ros2 = root / "fake_ros2"
        make_fake_ros2(fake_ros2)
        evidence = root / "evidence"

        manager = server.BagManager(
            [root], playback_enabled=True, evidence_dir=evidence, ros2_executable=str(fake_ros2)
        )
        try:
            catalog = manager.scan()
            if len(catalog) != 1:
                raise AssertionError(f"expected one simulated bag, got {len(catalog)}")
            identifier = catalog[0]["id"]
            if catalog[0]["has_motion"] is not False:
                raise AssertionError("telemetry-only bag was classified as has_motion")
            internal = manager._catalog[identifier]
            expected = sorted(server.TELEMETRY_TOPIC_ALLOWLIST)
            if internal["allowed_topics"] != expected:
                raise AssertionError(f"allowlist mismatch: {internal['allowed_topics']} != {expected}")

            playing = manager.play(identifier)
            command = playing["command"]
            if playing["ros_domain_id"] != 97 or not playing["pid"] or playing["pgid"] != playing["pid"]:
                raise AssertionError(f"invalid isolated player identity: {playing}")
            if playing["replay_mode"] != "telemetry":
                raise AssertionError(f"telemetry bag got wrong replay mode: {playing}")
            if any("cmd_vel" in argument or "action" in argument for argument in command):
                raise AssertionError(f"unsafe topic entered play command: {command}")
            for topic in expected:
                if topic not in command:
                    raise AssertionError(f"allowlisted topic missing from command: {topic}")

            time.sleep(0.18)
            stdout_path = next(evidence.glob("*.stdout.log"))
            if "fake-player-domain=97" not in stdout_path.read_text(encoding="utf-8"):
                raise AssertionError("isolated player did not run in Domain 97")
            manager.pause()
            time.sleep(0.08)
            paused_size_1 = stdout_path.stat().st_size
            time.sleep(0.16)
            paused_size_2 = stdout_path.stat().st_size
            if paused_size_2 != paused_size_1:
                raise AssertionError("simulated player output changed while paused")
            manager.resume()
            time.sleep(0.16)
            resumed_size = stdout_path.stat().st_size
            if resumed_size <= paused_size_2:
                raise AssertionError("simulated player output did not resume")
            stopped = manager.stop()
            if stopped["state"] != "idle" or not stopped["last_stop"].get("confirmed_exited"):
                raise AssertionError(f"simulated player did not confirm exit: {stopped}")

            process_record = json.loads((evidence / "bag_play_process.json").read_text(encoding="utf-8"))
            if process_record.get("ros_domain_id") != 97 or process_record.get("replay_mode") != "telemetry":
                raise AssertionError(f"process record missing isolated fields: {process_record}")
            event_lines = (evidence / "bag_process_events.jsonl").read_text(encoding="utf-8").splitlines()
            events = [json.loads(line) for line in event_lines]
            return {
                "catalog": catalog,
                "allowed_topics": internal["allowed_topics"],
                "play_status": playing,
                "paused_stdout_sizes": [paused_size_1, paused_size_2],
                "resumed_stdout_size": resumed_size,
                "stop_status": stopped,
                "process_record": process_record,
                "events": events,
            }
        finally:
            manager.shutdown()


def _make_motion_fixture(server, root: Path, evidence: Path):
    bag = root / "point_one"
    bag.mkdir()
    (bag / "metadata.yaml").write_text(metadata_text(), encoding="utf-8")
    (bag / "point_one_0.mcap").write_bytes(b"offline-motion-simulation")
    fake_ros2 = root / "fake_ros2"
    make_fake_ros2(fake_ros2)
    motion = FakeMotion()
    manager = server.BagManager(
        [root],
        playback_enabled=True,
        evidence_dir=evidence,
        ros2_executable=str(fake_ros2),
        motion=motion,
    )
    catalog = manager.scan()
    identifier = catalog[0]["id"]
    return manager, motion, identifier, bag, fake_ros2, catalog


def test_motion_replay_lifecycle(server) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="mof_motion_acceptance_") as temporary:
        root = Path(temporary)
        evidence = root / "evidence"
        manager, motion, identifier, bag, fake_ros2, catalog = _make_motion_fixture(
            server, root, evidence
        )
        try:
            if catalog[0]["has_motion"] is not True:
                raise AssertionError("motion bag was not classified as has_motion")
            internal = manager._catalog[identifier]
            if internal["allowed_topics"] != sorted(server.TELEMETRY_TOPIC_ALLOWLIST):
                raise AssertionError("internal telemetry allowlist changed for mixed fixture")

            playing = manager.play(identifier)
            command = playing["command"]
            expected_command = [
                str(fake_ros2),
                "bag",
                "play",
                str(bag),
                "--topics",
                "/cmd_vel",
                "--remap",
                "/cmd_vel:=/cmd_vel_nav",
            ]
            if command != expected_command:
                raise AssertionError(f"motion replay command mismatch: {command}")
            if playing["ros_domain_id"] is not None:
                raise AssertionError("motion replay must not set an isolated ROS domain")
            if playing["replay_mode"] != "motion" or not playing["pid"] or playing["pgid"] != playing["pid"]:
                raise AssertionError(f"invalid motion player identity: {playing}")
            if motion.suspended is not True:
                raise AssertionError("motion publisher was not suspended before spawn")
            for forbidden in ("/tf", "/chassis/debug", "/wheel/odom"):
                if forbidden in command:
                    raise AssertionError(f"telemetry topic entered motion command: {command}")

            time.sleep(0.18)
            stdout_path = next(evidence.glob("*.stdout.log"))
            stdout_text = stdout_path.read_text(encoding="utf-8")
            domain_lines = [line for line in stdout_text.splitlines() if line.startswith("fake-player-domain=")]
            if not domain_lines or domain_lines[0].split("=", 1)[1] != "":
                raise AssertionError(f"motion player inherited a ROS domain: {domain_lines}")

            try:
                manager.pause()
            except RuntimeError:
                pass
            else:
                raise AssertionError("pause was allowed for a live motion replay")
            try:
                manager.play(identifier)
            except RuntimeError:
                pass
            else:
                raise AssertionError("a second play was allowed while replaying")

            stopped = manager.stop()
            if stopped["state"] != "idle" or not stopped["last_stop"].get("confirmed_exited"):
                raise AssertionError(f"motion player did not confirm exit: {stopped}")
            if motion.suspended:
                raise AssertionError("motion publisher stayed suspended after stop")
            actions = [event["action"] for event in motion.timeline]
            if "publisher-suspended" not in actions or "publisher-resumed" not in actions:
                raise AssertionError(f"suspend/resume missing from motion timeline: {actions}")
            if actions.index("publisher-suspended") > actions.index("publisher-resumed"):
                raise AssertionError("suspend did not precede resume")
            if "replay-zero" not in actions:
                raise AssertionError("zero burst missing after motion stop")

            process_record = json.loads((evidence / "bag_play_process.json").read_text(encoding="utf-8"))
            if process_record.get("replay_mode") != "motion" or process_record.get("ros_domain_id") is not None:
                raise AssertionError(f"process record missing motion fields: {process_record}")
            if process_record.get("remap") != "/cmd_vel:=/cmd_vel_nav":
                raise AssertionError(f"process record missing remap: {process_record}")
            event_lines = (evidence / "bag_process_events.jsonl").read_text(encoding="utf-8").splitlines()
            return {
                "catalog": catalog,
                "play_status": playing,
                "stop_status": stopped,
                "process_record": process_record,
                "motion_timeline": motion.timeline,
                "events": [json.loads(line) for line in event_lines],
            }
        finally:
            manager.shutdown()


def test_motion_replay_natural_exit_resume(server) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="mof_motion_exit_") as temporary:
        root = Path(temporary)
        evidence = root / "evidence"
        manager, motion, identifier, _bag, _fake_ros2, _catalog = _make_motion_fixture(
            server, root, evidence
        )
        try:
            manager.play(identifier)
            time.sleep(0.20)  # let the fake player install its signal handlers
            pgid = manager._pgid
            os.killpg(pgid, signal.SIGTERM)
            deadline = time.monotonic() + 5.0
            status = None
            while time.monotonic() < deadline:
                status = manager.status()
                if status["state"] == "finished":
                    break
                time.sleep(0.05)
            if status is None or status["state"] != "finished":
                raise AssertionError(f"motion player did not finish naturally: {status}")
            if motion.suspended:
                raise AssertionError("publisher stayed suspended after natural exit")
            actions = [event["action"] for event in motion.timeline]
            if "publisher-resumed" not in actions or "replay-zero" not in actions:
                raise AssertionError(f"resume/zero missing after natural exit: {actions}")
            event_lines = (evidence / "bag_process_events.jsonl").read_text(encoding="utf-8").splitlines()
            return {
                "final_status": status,
                "motion_timeline": motion.timeline,
                "events": [json.loads(line) for line in event_lines],
            }
        finally:
            manager.shutdown()


def test_motion_replay_deadman(server) -> dict[str, Any]:
    original = server.MOTION_REPLAY_DEADMAN_SECONDS
    server.MOTION_REPLAY_DEADMAN_SECONDS = 0.5
    try:
        with tempfile.TemporaryDirectory(prefix="mof_motion_deadman_") as temporary:
            root = Path(temporary)
            evidence = root / "evidence"
            manager, motion, identifier, _bag, _fake_ros2, _catalog = _make_motion_fixture(
                server, root, evidence
            )
            try:
                manager.play(identifier)
                for _ in range(3):
                    manager.heartbeat()
                    time.sleep(0.1)
                deadline = time.monotonic() + 3.0
                status = None
                while time.monotonic() < deadline:
                    status = manager.status()
                    if status["state"] == "idle":
                        break
                    time.sleep(0.05)
                if status is None or status["state"] != "idle":
                    raise AssertionError(f"dead-man did not stop the motion replay: {status}")
                if motion.suspended:
                    raise AssertionError("publisher stayed suspended after dead-man stop")
                event_lines = (evidence / "bag_process_events.jsonl").read_text(encoding="utf-8").splitlines()
                events = [json.loads(line) for line in event_lines]
                if not any(event.get("event") == "motion-replay-deadman-expired" for event in events):
                    raise AssertionError("dead-man expiry event missing from evidence")
                actions = [event["action"] for event in motion.timeline]
                return {
                    "final_status": status,
                    "motion_timeline": motion.timeline,
                    "events": events,
                    "resumed": "publisher-resumed" in actions,
                }
            finally:
                manager.shutdown()
    finally:
        server.MOTION_REPLAY_DEADMAN_SECONDS = original


def test_motion_replay_rejected_without_publisher(server) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="mof_motion_nopub_") as temporary:
        root = Path(temporary)
        evidence = root / "evidence"
        motion_bag = root / "point_one"
        motion_bag.mkdir()
        (motion_bag / "metadata.yaml").write_text(metadata_text(), encoding="utf-8")
        (motion_bag / "point_one_0.mcap").write_bytes(b"offline-motion-simulation")
        telemetry_bag = root / "telemetry"
        telemetry_bag.mkdir()
        (telemetry_bag / "metadata.yaml").write_text(telemetry_metadata_text(), encoding="utf-8")
        (telemetry_bag / "telemetry_0.mcap").write_bytes(b"offline-telemetry-simulation")
        fake_ros2 = root / "fake_ros2"
        make_fake_ros2(fake_ros2)

        manager = server.BagManager(
            [root], playback_enabled=True, evidence_dir=evidence, ros2_executable=str(fake_ros2)
        )
        try:
            catalog = manager.scan()
            by_name = {item["name"]: item["id"] for item in catalog}
            try:
                manager.play(by_name["point_one"])
            except RuntimeError as exc:
                if "motion publisher" not in str(exc):
                    raise AssertionError(f"unexpected rejection reason: {exc}")
            else:
                raise AssertionError("motion replay started without a motion publisher")
            playing = manager.play(by_name["telemetry"])
            if playing["replay_mode"] != "telemetry":
                raise AssertionError("telemetry bag was blocked by missing motion publisher")
            stopped = manager.stop()
            return {
                "catalog": catalog,
                "telemetry_play": playing,
                "stop_status": stopped,
            }
        finally:
            manager.shutdown()


def test_mixed_bag_motion_wins(server) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="mof_mixed_bag_") as temporary:
        root = Path(temporary)
        evidence = root / "evidence"
        manager, motion, identifier, bag, fake_ros2, catalog = _make_motion_fixture(
            server, root, evidence
        )
        try:
            playing = manager.play(identifier)
            command = playing["command"]
            if playing["replay_mode"] != "motion":
                raise AssertionError("motion did not win for a mixed bag")
            if f"--topics" not in command or "/cmd_vel" not in command or f"--remap" not in command:
                raise AssertionError(f"mixed bag replay command missing motion flags: {command}")
            for forbidden in ("/tf", "/chassis/debug", "/wheel/odom"):
                if forbidden in command:
                    raise AssertionError(f"telemetry topic entered mixed-bag motion command: {command}")
            internal = manager._catalog[identifier]
            if internal["allowed_topics"] != sorted(server.TELEMETRY_TOPIC_ALLOWLIST):
                raise AssertionError("mixed bag lost its internal telemetry classification")
            stopped = manager.stop()
            return {
                "catalog": catalog,
                "play_status": playing,
                "stop_status": stopped,
            }
        finally:
            manager.shutdown()


def test_ephemeral_token(server) -> dict[str, Any]:
    first = server.ControlSession()
    second = server.ControlSession()
    if not first.validate(first.token) or first.validate(second.token) or first.token == second.token:
        raise AssertionError("ephemeral control token validation failed")
    return {"token_length": len(first.token), "different_per_process_instance": True, "token_value_saved": False}


def test_task_scoped_diff_whitespace(_server) -> dict[str, Any]:
    if TASK_BEFORE_DIR is None or not TASK_BEFORE_DIR.is_dir():
        raise AssertionError("--before-dir is required for task-scoped diff checking")
    before = TASK_BEFORE_DIR
    pairs = [
        (before / "web" / "server.py", PROJECT_ROOT / "web" / "server.py"),
        (before / "web" / "main.js", PROJECT_ROOT / "web" / "main.js"),
        (before / "web" / "index.html", PROJECT_ROOT / "web" / "index.html"),
        (before / "web" / "styles.css", PROJECT_ROOT / "web" / "styles.css"),
        (Path("/dev/null"), PROJECT_ROOT / "web" / "README.md"),
        (Path("/dev/null"), PROJECT_ROOT / "tools" / "web_console_acceptance.py"),
        (Path("/dev/null"), PROJECT_ROOT / "tools" / "start_mof_web_console.sh"),
    ]
    checks: list[dict[str, Any]] = []
    for old, new in pairs:
        completed = subprocess.run(
            ["git", "diff", "--no-index", "--check", "--", str(old), str(new)],
            cwd=PROJECT_ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
        # no-index returns 1 when content differs; --check reports whitespace errors in stdout.
        if completed.returncode not in {0, 1} or completed.stdout:
            raise AssertionError(
                f"git diff --no-index --check failed for {new}: rc={completed.returncode} output={completed.stdout!r}"
            )
        checks.append({"file": str(new.relative_to(PROJECT_ROOT)), "raw_return_code": completed.returncode, "output": ""})
    return {"checks": checks, "meaning_of_raw_rc_1": "content differs; no whitespace error output"}


def test_modes_and_http_protection(server) -> dict[str, Any]:
    rejected_args: list[str] = []
    for argv, label in (
        (["--motion-only", "--playback-only"], "conflicting-mode-flags"),
        (["--motion-only", "--host", "0.0.0.0"], "non-loopback-motion"),
        (["--playback-only", "--host", "0.0.0.0"], "non-loopback-playback"),
        (["--console", "--host", "0.0.0.0"], "non-loopback-console"),
        (["--enable-control"], "removed-combined-flag"),
    ):
        try:
            with contextlib.redirect_stderr(io.StringIO()):
                server.parse_args(argv)
        except SystemExit:
            rejected_args.append(label)
        else:
            raise AssertionError(f"unsafe CLI arguments were accepted: {label}")

    control_session = server.ControlSession()

    class DummyServer:
        pass

    dummy_server = DummyServer()
    dummy_server.control_session = control_session
    handler = object.__new__(server.ControlHandler)
    handler.server = dummy_server
    authority = "127.0.0.1:4173"

    def authorization_result(values: dict[str, str]) -> str:
        headers = Message()
        for key, value in values.items():
            headers[key] = value
        handler.headers = headers
        try:
            handler._require_write_authorization()
        except PermissionError:
            return "rejected"
        return "accepted"

    authorization = {
        "missing_origin_and_token": authorization_result({"Host": authority}),
        "cross_origin": authorization_result(
            {
                "Host": authority,
                "Origin": "https://cross-site.invalid",
                "X-MOF-Control-Token": control_session.token,
            }
        ),
        "missing_token": authorization_result({"Host": authority, "Origin": f"http://{authority}"}),
        "cross_site_fetch": authorization_result(
            {
                "Host": authority,
                "Origin": f"http://{authority}",
                "Sec-Fetch-Site": "cross-site",
                "X-MOF-Control-Token": control_session.token,
            }
        ),
        "same_origin_valid_token": authorization_result(
            {
                "Host": authority,
                "Origin": f"http://{authority}",
                "Sec-Fetch-Site": "same-origin",
                "X-MOF-Control-Token": control_session.token,
            }
        ),
    }
    expected_authorization = {
        "missing_origin_and_token": "rejected",
        "cross_origin": "rejected",
        "missing_token": "rejected",
        "cross_site_fetch": "rejected",
        "same_origin_valid_token": "accepted",
    }
    if authorization != expected_authorization:
        raise AssertionError(
            f"HTTP protection mismatch: {authorization} != {expected_authorization}"
        )
    return {
        "rejected_cli_configurations": rejected_args,
        "authorization_results": authorization,
        "token_value_saved": False,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence-dir", type=Path)
    parser.add_argument("--before-dir", type=Path)
    return parser.parse_args()


def main() -> int:
    global TASK_BEFORE_DIR
    args = parse_args()
    TASK_BEFORE_DIR = args.before_dir
    if TASK_BEFORE_DIR is None and args.evidence_dir is not None:
        TASK_BEFORE_DIR = args.evidence_dir.parent / "before"
    server = load_server()
    tests = [
        ("python_ast", test_ast),
        ("browser_stop_and_sequence_interlocks", test_browser_interlocks),
        ("request_sequence_ordering", test_request_ordering),
        ("watchdog_and_shutdown_zero", test_watchdog_and_shutdown_zero),
        ("bag_allowlist_play_pause_resume_stop", test_bag_allowlist_and_lifecycle),
        ("motion_replay_lifecycle", test_motion_replay_lifecycle),
        ("motion_replay_natural_exit_resume", test_motion_replay_natural_exit_resume),
        ("motion_replay_deadman", test_motion_replay_deadman),
        ("motion_replay_rejected_without_publisher", test_motion_replay_rejected_without_publisher),
        ("mixed_bag_motion_wins", test_mixed_bag_motion_wins),
        ("ephemeral_control_token", test_ephemeral_token),
        ("task_scoped_git_diff_check", test_task_scoped_diff_whitespace),
        ("mutually_exclusive_modes_and_http_protection", test_modes_and_http_protection),
    ]
    results: dict[str, Any] = {"success": False, "tests": {}}
    try:
        for name, test in tests:
            started = time.monotonic()
            detail = test(server)
            results["tests"][name] = {
                "status": "PASS",
                "elapsed_seconds": round(time.monotonic() - started, 6),
                "detail": detail,
            }
            print(f"PASS {name}")
        results["success"] = True
    except Exception as exc:
        results["failure"] = {"type": type(exc).__name__, "message": str(exc)}
        print(f"FAIL {type(exc).__name__}: {exc}", file=sys.stderr)
    finally:
        rendered = json.dumps(results, ensure_ascii=False, indent=2, sort_keys=True)
        print(rendered)
        if args.evidence_dir:
            args.evidence_dir.mkdir(parents=True, exist_ok=True)
            (args.evidence_dir / "offline_acceptance_results.json").write_text(rendered + "\n", encoding="utf-8")
            ordering = results.get("tests", {}).get("request_sequence_ordering", {}).get("detail")
            if ordering is not None:
                (args.evidence_dir / "request_ordering_timeline.json").write_text(
                    json.dumps(ordering, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8",
                )
    return 0 if results["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
