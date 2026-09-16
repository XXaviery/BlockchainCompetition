#!/usr/bin/env python3
"""Serve the MOF web console with mutually exclusive safe operating modes."""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import math
import os
from pathlib import Path
import re
import secrets
import shutil
import signal
import subprocess
import threading
import time
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

try:
    from pollution_demo import PollutionRequestError, catalog_payload, snapshot_payload
except ImportError:  # Loading this file through the repository-level acceptance tool.
    from web.pollution_demo import PollutionRequestError, catalog_payload, snapshot_payload


WEB_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = WEB_ROOT.parent
SAFE_TOPIC = "/cmd_vel_nav"
TELEMETRY_TOPIC_ALLOWLIST = frozenset({"/chassis/debug", "/tf", "/wheel/odom"})
PLAYBACK_ROS_DOMAIN_ID = 97
MOTION_REPLAY_TOPIC = "/cmd_vel"
MOTION_REPLAY_ENABLED = True
MOTION_REPLAY_DEADMAN_SECONDS = 2.5
MOTION_WATCHDOG_POLL_SECONDS = 0.10
MAX_VX = 0.50
MAX_VY = 0.60
MAX_WZ = 1.00
WATCHDOG_SECONDS = 0.30
PUBLISH_RATE_HZ = 20.0
SHUTDOWN_ZERO_SECONDS = WATCHDOG_SECONDS + 0.20
MAX_REQUEST_BYTES = 16 * 1024
SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_-]{16,128}$")
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}
MOTION_MODES = frozenset({"motion", "console"})
PLAYBACK_MODES = frozenset({"playback", "console"})


try:
    import rclpy
    from geometry_msgs.msg import Twist
    from rclpy.node import Node
except ImportError as exc:  # Static preview and local tests do not need ROS.
    rclpy = None
    Twist = None
    Node = object
    RCLPY_IMPORT_ERROR = str(exc)
else:
    RCLPY_IMPORT_ERROR = ""


class StaleCommandError(RuntimeError):
    """A command sequence is not newer than the accepted sequence."""


class MotionPublisher:
    """Publish the current command at 20 Hz and force zero on timeout/exit."""

    def __init__(self, enabled: bool = False) -> None:
        self._lock = threading.Lock()
        self._command = (0.0, 0.0, 0.0)
        self._suspended = False
        self._last_refresh = 0.0
        self._audit: list[dict[str, Any]] = []
        self._shutdown_report: dict[str, Any] = {
            "duration_seconds": 0.0,
            "zero_publish_count": 0,
            "timeline": [],
        }
        self.node = None
        self.publisher = None
        self.spin_thread = None
        self.available = False
        self.error = RCLPY_IMPORT_ERROR if enabled else "motion permission is disabled"

        if not enabled or rclpy is None:
            return

        try:
            rclpy.init(args=None)
            self.node = Node("mof_web_control")
            self.publisher = self.node.create_publisher(Twist, SAFE_TOPIC, 10)
            self.node.create_timer(1.0 / PUBLISH_RATE_HZ, self._publish_tick)
            self.spin_thread = threading.Thread(target=rclpy.spin, args=(self.node,), daemon=True)
            self.spin_thread.start()
            self.available = True
            self.error = ""
        except Exception as exc:  # Keep the static console available on failure.
            self.error = str(exc)

    @staticmethod
    def _clamp(value: float, maximum: float) -> float:
        return max(-maximum, min(maximum, value))

    def _record(self, source: str, command: tuple[float, float, float]) -> dict[str, Any]:
        event = {
            "monotonic": round(time.monotonic(), 6),
            "wall_time": datetime.now().astimezone().isoformat(),
            "source": source,
            "command": [round(value, 6) for value in command],
        }
        with self._lock:
            self._audit.append(event)
            if len(self._audit) > 1000:
                del self._audit[:-1000]
        return event

    @property
    def safety_subscribers(self) -> int:
        if not self.available or self.publisher is None:
            return 0
        try:
            return int(self.publisher.get_subscription_count())
        except Exception:
            return 0

    @property
    def command(self) -> tuple[float, float, float]:
        with self._lock:
            return self._command

    @property
    def shutdown_report(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._shutdown_report)

    def audit_snapshot(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._audit)

    def set_command(self, vx: float, vy: float, wz: float) -> None:
        if not self.available:
            raise RuntimeError(self.error or "ROS 2 is unavailable")
        if not all(math.isfinite(value) for value in (vx, vy, wz)):
            raise ValueError("velocity values must be finite numbers")

        bounded = (
            self._clamp(float(vx), MAX_VX),
            self._clamp(float(vy), MAX_VY),
            self._clamp(float(wz), MAX_WZ),
        )
        moving = any(abs(value) > 1e-9 for value in bounded)
        if moving and self.safety_subscribers < 1:
            raise RuntimeError("/cmd_vel_nav has no safety-chain subscriber")

        with self._lock:
            self._command = bounded
            self._last_refresh = time.monotonic()
        self._record("accepted-command", bounded)

    def stop(self) -> None:
        zero = (0.0, 0.0, 0.0)
        with self._lock:
            self._command = zero
            self._last_refresh = time.monotonic()
        self._record("accepted-stop", zero)

    def suspend(self) -> None:
        """Silence this publisher while a live motion replay owns /cmd_vel_nav.

        Lock ordering invariant: the only nested acquisition anywhere is
        BagManager._lock -> MotionPublisher._lock (BagManager.play calls
        suspend while holding its own lock). No code path ever acquires
        MotionPublisher._lock and then BagManager._lock.
        """
        with self._lock:
            self._suspended = True
        self._record("publisher-suspended", self._command)

    def resume(self) -> None:
        """Resume publishing and reset the command to zero."""
        with self._lock:
            self._suspended = False
            self._command = (0.0, 0.0, 0.0)
            self._last_refresh = time.monotonic()
        self._record("publisher-resumed", (0.0, 0.0, 0.0))

    def _publish_values(self, command: tuple[float, float, float], source: str) -> None:
        if not self.available or self.publisher is None or Twist is None:
            return
        message = Twist()
        message.linear.x = command[0]
        message.linear.y = command[1]
        message.angular.z = command[2]
        self.publisher.publish(message)
        self._record(source, command)

    def _publish_tick(self) -> None:
        if not self.available or self.publisher is None:
            return

        with self._lock:
            if self._suspended:
                return

        watchdog_forced = False
        with self._lock:
            if time.monotonic() - self._last_refresh > WATCHDOG_SECONDS:
                if any(abs(value) > 1e-9 for value in self._command):
                    watchdog_forced = True
                self._command = (0.0, 0.0, 0.0)
            command = self._command
        self._publish_values(command, "watchdog-zero" if watchdog_forced else "timer")

    def publish_zero_burst(self, source: str = "replay-zero") -> dict[str, Any]:
        """Explicitly publish zero for longer than one browser watchdog period.

        Callers (live-replay stop/exit and shutdown) ensure the command is
        zero before invoking this; the burst itself only publishes.
        """
        if not self.available or self.publisher is None:
            return self.shutdown_report

        started = time.monotonic()
        deadline = started + SHUTDOWN_ZERO_SECONDS
        interval = 1.0 / PUBLISH_RATE_HZ
        timeline: list[dict[str, Any]] = []
        while True:
            timeline.append(self._record(source, (0.0, 0.0, 0.0)))
            if Twist is not None:
                message = Twist()
                self.publisher.publish(message)
            if time.monotonic() >= deadline:
                break
            time.sleep(interval)
        report = {
            "duration_seconds": round(time.monotonic() - started, 6),
            "zero_publish_count": len(timeline),
            "timeline": timeline,
        }
        with self._lock:
            self._shutdown_report = report
        return report

    def publish_shutdown_zero_burst(self) -> dict[str, Any]:
        """Explicitly publish zero for longer than one browser watchdog period."""
        self.stop()
        return self.publish_zero_burst("shutdown-zero")

    def shutdown(self) -> dict[str, Any]:
        report = self.publish_shutdown_zero_burst()
        if not self.available or rclpy is None:
            return report
        try:
            self.node.destroy_node()
        finally:
            if rclpy.ok():
                rclpy.shutdown()
        if self.spin_thread is not None:
            self.spin_thread.join(timeout=1.0)
        return report


class SequencedMotionController:
    """Atomically reject stale/repeated browser commands per browser session."""

    def __init__(self, motion: MotionPublisher) -> None:
        self.motion = motion
        self._lock = threading.Lock()
        self._last_sequences: dict[str, int] = {}
        self._active_session_id: str | None = None

    @staticmethod
    def validate_identity(session_id: Any, sequence: Any) -> tuple[str, int]:
        if not isinstance(session_id, str) or not SESSION_ID_RE.fullmatch(session_id):
            raise ValueError("session_id is invalid")
        if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 1:
            raise ValueError("seq must be a positive integer")
        return session_id, sequence

    def apply(
        self,
        session_id: Any,
        sequence: Any,
        command: tuple[float, float, float] | None,
    ) -> dict[str, Any]:
        session_id, sequence = self.validate_identity(session_id, sequence)
        with self._lock:
            if self._active_session_id is None:
                self._active_session_id = session_id
            elif session_id != self._active_session_id:
                if command is None:
                    # A freshly loaded page may safely claim control only by
                    # sending zero. Non-zero commands from old tabs remain
                    # rejected after the takeover.
                    self._active_session_id = session_id
                else:
                    raise StaleCommandError("another browser session owns the motion command stream")
            previous = self._last_sequences.get(session_id, 0)
            if sequence <= previous:
                raise StaleCommandError(
                    f"stale command rejected: seq={sequence}, last_accepted_seq={previous}"
                )
            if command is None:
                self.motion.stop()
            else:
                self.motion.set_command(*command)
            self._last_sequences[session_id] = sequence
            if len(self._last_sequences) > 128:
                oldest = next(iter(self._last_sequences))
                if oldest != session_id:
                    self._last_sequences.pop(oldest, None)
        return {"accepted_seq": sequence, "previous_seq": previous}


class ControlSession:
    """An ephemeral per-process CSRF token; it is never printed or persisted."""

    def __init__(self) -> None:
        self._token = secrets.token_urlsafe(32)

    @property
    def token(self) -> str:
        return self._token

    def validate(self, candidate: str | None) -> bool:
        return isinstance(candidate, str) and hmac.compare_digest(candidate, self._token)


class BagManager:
    """Replay bags: telemetry-only in an isolated domain, or /cmd_vel live motion.

    Telemetry bags keep the original isolated Domain-97 path. Bags whose
    metadata contains /cmd_vel replay live into the default domain with
    --remap /cmd_vel:=/cmd_vel_nav; during such a replay the MotionPublisher
    is suspended and a browser heartbeat feeds a dead-man watchdog.
    """

    def __init__(
        self,
        roots: list[Path],
        playback_enabled: bool = False,
        evidence_dir: Path | None = None,
        ros2_executable: str | None = None,
        motion: Any | None = None,
    ) -> None:
        self.roots = [path.resolve() for path in roots if path.exists() and path.is_dir()]
        self._lock = threading.Lock()
        self._catalog: dict[str, dict[str, Any]] = {}
        self._process: subprocess.Popen | None = None
        self._pgid: int | None = None
        self._state = "idle"
        self._replay_mode: str | None = None
        self._active_id: str | None = None
        self._active_name = ""
        self._active_command: list[str] = []
        self._active_domain: int | None = None
        self._error = ""
        self._started_at = 0.0
        self._stdout_handle = None
        self._stderr_handle = None
        self._last_stop: dict[str, Any] = {}
        self.playback_enabled = playback_enabled
        self.evidence_dir = (evidence_dir or Path("/tmp") / f"mof_web_bag_{os.getpid()}").resolve()
        self.ros2_executable = ros2_executable
        self.motion = motion
        self._motion_deadline = 0.0
        self._pending_resume = False
        self._stop_event = threading.Event()
        self._watchdog_thread: threading.Thread | None = None
        if playback_enabled:
            self.evidence_dir.mkdir(parents=True, exist_ok=True)
            self._watchdog_thread = threading.Thread(
                target=self._watchdog_loop, name="mof-bag-watchdog", daemon=True
            )
            self._watchdog_thread.start()

    @staticmethod
    def _is_within(path: Path, root: Path) -> bool:
        try:
            path.relative_to(root)
            return True
        except ValueError:
            return False

    @staticmethod
    def _first_integer(pattern: str, text: str) -> int:
        match = re.search(pattern, text, flags=re.MULTILINE)
        return int(match.group(1)) if match else 0

    def _read_metadata(self, metadata_path: Path, root: Path) -> dict[str, Any] | None:
        bag_path = metadata_path.parent.resolve()
        if not self._is_within(bag_path, root) or metadata_path.stat().st_size > 2_000_000:
            return None
        try:
            text = metadata_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None

        topics = sorted(set(re.findall(r"^\s+name:\s*(/\S+)\s*$", text, flags=re.MULTILINE)))
        allowed_topics = sorted(TELEMETRY_TOPIC_ALLOWLIST.intersection(topics))
        has_motion = MOTION_REPLAY_TOPIC in topics
        duration_ns = self._first_integer(r"^\s*duration:\s*\n\s*nanoseconds:\s*(\d+)", text)
        start_ns = self._first_integer(
            r"^\s*starting_time:\s*\n\s*nanoseconds_since_epoch:\s*(\d+)", text
        )
        message_count = self._first_integer(r"^\s*message_count:\s*(\d+)", text)
        relative = bag_path.relative_to(root)
        identifier = hashlib.sha256(str(bag_path).encode("utf-8")).hexdigest()[:18]
        display_parts = relative.parts[-3:] if relative.parts else (bag_path.name,)
        name = " / ".join(display_parts)

        if start_ns:
            try:
                recorded_at = datetime.fromtimestamp(start_ns / 1e9, tz=timezone.utc).astimezone().isoformat()
            except (OSError, OverflowError, ValueError):
                recorded_at = ""
        else:
            recorded_at = datetime.fromtimestamp(metadata_path.stat().st_mtime).astimezone().isoformat()

        return {
            "id": identifier,
            "name": name,
            "path": bag_path,
            "duration_seconds": round(duration_ns / 1e9, 3),
            "message_count": message_count,
            "topic_count": len(allowed_topics),
            "allowed_topics": allowed_topics,
            "has_motion": has_motion,
            "recorded_at": recorded_at,
        }

    def scan(self) -> list[dict[str, Any]]:
        catalog: dict[str, dict[str, Any]] = {}
        for root in self.roots:
            try:
                metadata_files = list(root.rglob("metadata.yaml"))[:200]
            except OSError:
                continue
            for metadata_path in metadata_files:
                item = self._read_metadata(metadata_path, root)
                if item is not None:
                    catalog[item["id"]] = item
        with self._lock:
            self._catalog = catalog
        public_items = [
            {key: value for key, value in item.items() if key not in {"path", "allowed_topics"}}
            for item in catalog.values()
        ]
        public_items.sort(key=lambda item: item["recorded_at"], reverse=True)
        return public_items

    def _append_event_locked(self, event: dict[str, Any]) -> None:
        if not self.playback_enabled:
            return
        payload = {
            "wall_time": datetime.now().astimezone().isoformat(),
            "monotonic": round(time.monotonic(), 6),
            **event,
        }
        event_path = self.evidence_dir / "bag_process_events.jsonl"
        with event_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")

    def _write_start_record_locked(self, record: dict[str, Any]) -> None:
        record_path = self.evidence_dir / "bag_play_process.json"
        record_path.write_text(
            json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def _close_logs_locked(self) -> None:
        for handle_name in ("_stdout_handle", "_stderr_handle"):
            handle = getattr(self, handle_name)
            if handle is not None:
                try:
                    handle.flush()
                    handle.close()
                finally:
                    setattr(self, handle_name, None)

    def _update_process_state_locked(self) -> None:
        if self._process is None:
            return
        return_code = self._process.poll()
        if return_code is None:
            return
        self._append_event_locked({"event": "process-exited", "return_code": return_code})
        if self._state not in {"idle", "error", "stopping"}:
            self._state = "finished" if return_code == 0 else "error"
            if return_code != 0:
                self._error = f"ros2 bag play exited with status {return_code}"
        if self._replay_mode == "motion":
            self._pending_resume = True
        self._replay_mode = None
        self._motion_deadline = 0.0
        self._close_logs_locked()
        self._process = None
        self._pgid = None

    @property
    def is_active(self) -> bool:
        with self._lock:
            self._update_process_state_locked()
            active = self._process is not None and self._state in {"playing", "paused", "stopping"}
        self._drain_pending_resume()
        return active

    def status(self) -> dict[str, Any]:
        with self._lock:
            self._update_process_state_locked()
            status = {
                "state": self._state,
                "id": self._active_id,
                "name": self._active_name,
                "error": self._error,
                "elapsed_seconds": round(time.monotonic() - self._started_at, 1)
                if self._process is not None
                else 0,
                "pid": self._process.pid if self._process is not None else None,
                "pgid": self._pgid,
                "command": list(self._active_command),
                "ros_domain_id": self._active_domain,
                "replay_mode": self._replay_mode,
                "last_stop": dict(self._last_stop),
            }
        self._drain_pending_resume()
        return status

    def play(self, identifier: str) -> dict[str, Any]:
        if not self.playback_enabled:
            raise RuntimeError("rosbag playback permission is disabled")
        ros2 = self.ros2_executable or shutil.which("ros2")
        if ros2 is None:
            raise RuntimeError("ros2 CLI is unavailable in the server environment")

        self.scan()
        with self._lock:
            self._update_process_state_locked()
            if self._process is not None:
                raise RuntimeError("another rosbag is already playing")
            item = self._catalog.get(identifier)
            if item is None:
                raise ValueError("unknown rosbag selection")
            topics = list(item["allowed_topics"])
            has_motion = bool(item.get("has_motion", False))
            if not topics and not has_motion:
                raise RuntimeError("this rosbag has no replayable topics")
            if not set(topics).issubset(TELEMETRY_TOPIC_ALLOWLIST):
                raise RuntimeError("internal playback allowlist violation")

            if has_motion:
                if not MOTION_REPLAY_ENABLED:
                    raise RuntimeError("motion replay is disabled in this build")
                if self.motion is None or not self.motion.available:
                    raise RuntimeError("live motion replay requires the ROS motion publisher")
                if self.motion.safety_subscribers < 1:
                    raise RuntimeError("no safety-chain subscriber on /cmd_vel_nav; live motion replay refused")

            if has_motion:
                command = [
                    ros2,
                    "bag",
                    "play",
                    str(item["path"]),
                    "--topics",
                    MOTION_REPLAY_TOPIC,
                    "--remap",
                    f"{MOTION_REPLAY_TOPIC}:={SAFE_TOPIC}",
                ]
                environment = os.environ.copy()
                record_domain = None
            else:
                command = [ros2, "bag", "play", str(item["path"]), "--topics", *topics]
                environment = os.environ.copy()
                environment["ROS_DOMAIN_ID"] = str(PLAYBACK_ROS_DOMAIN_ID)
                record_domain = PLAYBACK_ROS_DOMAIN_ID
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            stdout_path = self.evidence_dir / f"bag_play_{stamp}.stdout.log"
            stderr_path = self.evidence_dir / f"bag_play_{stamp}.stderr.log"
            self._stdout_handle = stdout_path.open("ab", buffering=0)
            self._stderr_handle = stderr_path.open("ab", buffering=0)
            if has_motion:
                self.motion.suspend()
            try:
                process = subprocess.Popen(
                    command,
                    stdin=subprocess.DEVNULL,
                    stdout=self._stdout_handle,
                    stderr=self._stderr_handle,
                    env=environment,
                    start_new_session=True,
                )
            except OSError as exc:
                self._close_logs_locked()
                if has_motion:
                    self.motion.resume()
                raise RuntimeError(f"unable to start rosbag replay: {exc}") from exc

            self._process = process
            self._pgid = os.getpgid(process.pid)
            self._state = "playing"
            self._replay_mode = "motion" if has_motion else "telemetry"
            self._motion_deadline = time.monotonic() if has_motion else 0.0
            self._active_id = identifier
            self._active_name = item["name"]
            self._active_command = command
            self._active_domain = record_domain
            self._error = ""
            self._last_stop = {}
            self._started_at = time.monotonic()
            record = {
                "wall_time": datetime.now().astimezone().isoformat(),
                "command": command,
                "pid": process.pid,
                "pgid": self._pgid,
                "ros_domain_id": record_domain,
                "replay_mode": self._replay_mode,
                "remap": f"{MOTION_REPLAY_TOPIC}:={SAFE_TOPIC}" if has_motion else None,
                "topics": topics,
                "stdout": str(stdout_path),
                "stderr": str(stderr_path),
            }
            self._write_start_record_locked(record)
            self._append_event_locked({"event": "play", **record})
        return self.status()

    def _signal_process(self, sig: signal.Signals, target_state: str) -> dict[str, Any]:
        with self._lock:
            self._update_process_state_locked()
            if self._process is None or self._pgid is None:
                raise RuntimeError("no rosbag replay is active")
            try:
                os.killpg(self._pgid, sig)
            except (OSError, ProcessLookupError) as exc:
                raise RuntimeError(f"unable to control rosbag replay: {exc}") from exc
            self._state = target_state
            self._append_event_locked({"event": target_state, "signal": int(sig), "pgid": self._pgid})
        return self.status()

    def pause(self) -> dict[str, Any]:
        with self._lock:
            if self._replay_mode == "motion" and self._process is not None:
                raise RuntimeError("pausing a live motion replay is not allowed; press Stop")
        return self._signal_process(signal.SIGSTOP, "paused")

    def resume(self) -> dict[str, Any]:
        return self._signal_process(signal.SIGCONT, "playing")

    def stop(self) -> dict[str, Any]:
        try:
            with self._lock:
                self._update_process_state_locked()
                process = self._process
                pgid = self._pgid
                if process is None or pgid is None:
                    self._state = "idle"
                    self._active_id = None
                    self._active_name = ""
                    self._replay_mode = None
                    self._motion_deadline = 0.0
                else:
                    self._stop_active_locked(process, pgid)
        finally:
            self._drain_pending_resume()
        return self.status()

    def _stop_active_locked(self, process: subprocess.Popen, pgid: int) -> None:
        self._state = "stopping"
        attempts: list[dict[str, Any]] = []
        if process.poll() is None:
            try:
                os.killpg(pgid, signal.SIGCONT)
            except ProcessLookupError:
                pass
        for sig, timeout_seconds in ((signal.SIGINT, 5.0), (signal.SIGTERM, 3.0)):
            if process.poll() is not None:
                break
            attempt = {"signal": sig.name, "timeout_seconds": timeout_seconds}
            try:
                os.killpg(pgid, sig)
                process.wait(timeout=timeout_seconds)
                attempt["exited"] = True
                attempt["return_code"] = process.returncode
            except subprocess.TimeoutExpired:
                attempt["exited"] = False
            except ProcessLookupError:
                attempt["exited"] = True
            attempts.append(attempt)

        forced_kill = False
        if process.poll() is None:
            forced_kill = True
            self._error = "ros2 bag play ignored SIGINT and SIGTERM; cleaned only its owned PGID"
            try:
                os.killpg(pgid, signal.SIGKILL)
                process.wait(timeout=2.0)
            except (ProcessLookupError, subprocess.TimeoutExpired):
                pass
            attempts.append(
                {"signal": "SIGKILL", "timeout_seconds": 2.0, "exited": process.poll() is not None}
            )

        confirmed_exited = process.poll() is not None
        self._last_stop = {
            "pgid": pgid,
            "attempts": attempts,
            "forced_kill": forced_kill,
            "confirmed_exited": confirmed_exited,
            "return_code": process.returncode,
        }
        self._append_event_locked({"event": "stop", **self._last_stop})
        if self._replay_mode == "motion":
            self._pending_resume = True
        self._replay_mode = None
        self._motion_deadline = 0.0
        if not confirmed_exited:
            self._state = "error"
            self._error = "rosbag player PGID still exists after bounded stop escalation"
            raise RuntimeError("rosbag player PGID still exists after bounded stop escalation")
        self._close_logs_locked()
        self._process = None
        self._pgid = None
        self._state = "idle"
        self._active_id = None
        self._active_name = ""

    def heartbeat(self) -> None:
        """Refresh the motion-replay dead-man deadline (no-op for telemetry)."""
        with self._lock:
            if self._replay_mode == "motion" and self._process is not None:
                self._motion_deadline = time.monotonic()

    def _watchdog_loop(self) -> None:
        while not self._stop_event.wait(MOTION_WATCHDOG_POLL_SECONDS):
            self._watchdog_tick()

    def _watchdog_tick(self) -> None:
        expired = False
        with self._lock:
            self._update_process_state_locked()
            expired = (
                self._replay_mode == "motion"
                and self._state == "playing"
                and self._motion_deadline > 0.0
                and time.monotonic() - self._motion_deadline > MOTION_REPLAY_DEADMAN_SECONDS
            )
            if expired:
                self._append_event_locked({"event": "motion-replay-deadman-expired"})
        if expired:
            try:
                self.stop()
            except RuntimeError:
                pass
        else:
            self._drain_pending_resume()

    def _drain_pending_resume(self) -> None:
        """Resume the motion publisher after a live replay ended.

        The flag is cleared under our lock; the motion calls run outside it
        (motion never calls back into BagManager, so no lock inversion).
        """
        with self._lock:
            if not self._pending_resume or self.motion is None:
                return
            self._pending_resume = False
        if not self.motion.available:
            return
        self.motion.resume()
        self.motion.publish_zero_burst()

    def shutdown(self) -> None:
        """Stop the watchdog thread; call before the ROS node is torn down."""
        self._stop_event.set()
        if self._watchdog_thread is not None:
            self._watchdog_thread.join(timeout=1.0)


class ControlServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        address,
        handler,
        mode: str,
        motion: MotionPublisher,
        sequencer: SequencedMotionController,
        bags: BagManager,
        pollution_demo: bool = False,
    ) -> None:
        super().__init__(address, handler)
        self.mode = mode
        self.motion = motion
        self.sequencer = sequencer
        self.bags = bags
        self.control_session = ControlSession()
        self.operation_lock = threading.Lock()
        self.pollution_demo = pollution_demo


class ControlHandler(SimpleHTTPRequestHandler):
    server: ControlServer

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(WEB_ROOT), **kwargs)

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", "frame-ancestors 'none'; base-uri 'self'")
        super().end_headers()

    def _send_json(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status.value)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def _read_json(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise ValueError("invalid Content-Length") from exc
        if length < 0 or length > MAX_REQUEST_BYTES:
            raise ValueError("request body is too large")
        raw = self.rfile.read(length) if length else b"{}"
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("request body must be valid JSON") from exc
        if not isinstance(value, dict):
            raise ValueError("request body must be a JSON object")
        return value

    def _require_write_authorization(self) -> None:
        host = self.headers.get("Host", "")
        origin = self.headers.get("Origin", "")
        parsed = urlparse(origin)
        if not host or parsed.scheme not in {"http", "https"} or parsed.netloc != host:
            raise PermissionError("same-origin POST required")
        fetch_site = self.headers.get("Sec-Fetch-Site")
        if fetch_site and fetch_site != "same-origin":
            raise PermissionError("cross-site POST rejected")
        referer = self.headers.get("Referer")
        if referer:
            parsed_referer = urlparse(referer)
            if parsed_referer.scheme != parsed.scheme or parsed_referer.netloc != host:
                raise PermissionError("cross-origin Referer rejected")
        token = self.headers.get("X-MOF-Control-Token")
        if not self.server.control_session.validate(token):
            raise PermissionError("missing or invalid control session token")

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/api/pollution/catalog":
            if not getattr(self.server, "pollution_demo", False):
                self._send_json({"error": "pollution demo is disabled"}, HTTPStatus.NOT_FOUND)
            else:
                try:
                    if len(self.path) > 512:
                        raise PollutionRequestError("pollution query is too large")
                    if parse_qs(parsed.query, keep_blank_values=True):
                        raise PollutionRequestError("catalog does not accept query parameters")
                    self._send_json(catalog_payload())
                except PollutionRequestError as exc:
                    self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return
        if path == "/api/pollution/snapshot":
            if not getattr(self.server, "pollution_demo", False):
                self._send_json({"error": "pollution demo is disabled"}, HTTPStatus.NOT_FOUND)
                return
            if len(self.path) > 512:
                self._send_json({"error": "pollution query is too large"}, HTTPStatus.BAD_REQUEST)
                return
            try:
                query = parse_qs(parsed.query, keep_blank_values=True)
                if set(query) - {"scenario_id", "step", "metric"}:
                    raise PollutionRequestError("unsupported pollution query parameter")

                def one(name: str, required: bool = False) -> str | None:
                    values = query.get(name, [])
                    if len(values) > 1 or (required and len(values) != 1):
                        raise PollutionRequestError(f"{name} must have one value")
                    return values[0] if values else None

                self._send_json(
                    snapshot_payload(
                        one("scenario_id", required=True),
                        one("step", required=True),
                        one("metric"),
                    )
                )
            except PollutionRequestError as exc:
                self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return
        if path == "/api/session":
            self._send_json(
                {
                    "token": self.server.control_session.token,
                    "mode": self.server.mode,
                    "permissions": {
                        "motion": self.server.mode in MOTION_MODES,
                        "playback": self.server.mode in PLAYBACK_MODES,
                    },
                }
            )
            return
        if path == "/api/status":
            vx, vy, wz = self.server.motion.command
            self._send_json(
                {
                    "mode": self.server.mode,
                    "permissions": {
                        "motion": self.server.mode in MOTION_MODES,
                        "playback": self.server.mode in PLAYBACK_MODES,
                    },
                    "ros_available": self.server.motion.available,
                    "ros_error": self.server.motion.error,
                    "safety_topic": SAFE_TOPIC,
                    "safety_subscribers": self.server.motion.safety_subscribers,
                    "command": {"vx": vx, "vy": vy, "wz": wz},
                    "player": self.server.bags.status(),
                }
            )
            return
        if path == "/api/bags":
            if self.server.mode not in PLAYBACK_MODES:
                self._send_json({"error": "rosbag browsing is disabled outside playback mode"}, HTTPStatus.FORBIDDEN)
            else:
                self._send_json({"bags": self.server.bags.scan()})
            return
        super().do_GET()

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path.startswith("/api/pollution/"):
            self._send_json({"error": "pollution demo is read-only; use GET"}, HTTPStatus.METHOD_NOT_ALLOWED)
            return
        try:
            self._require_write_authorization()
            body = self._read_json()
            if path == "/api/motion":
                if self.server.mode not in MOTION_MODES:
                    raise PermissionError("motion permission is disabled in this mode")
                try:
                    command = (
                        float(body.get("vx", 0.0)),
                        float(body.get("vy", 0.0)),
                        float(body.get("wz", 0.0)),
                    )
                except (TypeError, ValueError) as exc:
                    raise ValueError("vx, vy and wz must be numbers") from exc
                with self.server.operation_lock:
                    if any(abs(value) > 1e-9 for value in command) and self.server.bags.is_active:
                        raise RuntimeError("stop rosbag replay before moving the robot")
                    sequence = self.server.sequencer.apply(body.get("session_id"), body.get("seq"), command)
                self._send_json({"ok": True, **sequence, "command": list(command)})
                return
            if path == "/api/stop":
                if self.server.mode not in MOTION_MODES:
                    raise PermissionError("motion permission is disabled in this mode")
                with self.server.operation_lock:
                    sequence = self.server.sequencer.apply(body.get("session_id"), body.get("seq"), None)
                self._send_json({"ok": True, **sequence})
                return
            if path == "/api/bags/play":
                if self.server.mode not in PLAYBACK_MODES:
                    raise PermissionError("playback permission is disabled in this mode")
                identifier = body.get("id")
                if not isinstance(identifier, str) or not identifier:
                    raise ValueError("a rosbag id is required")
                with self.server.operation_lock:
                    if any(abs(value) > 1e-9 for value in self.server.motion.command):
                        raise RuntimeError("release motion controls before starting rosbag replay")
                    self.server.motion.stop()
                    player = self.server.bags.play(identifier)
                self._send_json({"ok": True, "player": player})
                return
            if path == "/api/bags/pause":
                if self.server.mode not in PLAYBACK_MODES:
                    raise PermissionError("playback permission is disabled in this mode")
                self._send_json({"ok": True, "player": self.server.bags.pause()})
                return
            if path == "/api/bags/resume":
                if self.server.mode not in PLAYBACK_MODES:
                    raise PermissionError("playback permission is disabled in this mode")
                self._send_json({"ok": True, "player": self.server.bags.resume()})
                return
            if path == "/api/bags/stop":
                if self.server.mode not in PLAYBACK_MODES:
                    raise PermissionError("playback permission is disabled in this mode")
                self._send_json({"ok": True, "player": self.server.bags.stop()})
                return
            if path == "/api/bags/heartbeat":
                if self.server.mode not in PLAYBACK_MODES:
                    raise PermissionError("playback permission is disabled in this mode")
                self.server.bags.heartbeat()
                self._send_json({"ok": True})
                return
            self._send_json({"error": "unknown API endpoint"}, HTTPStatus.NOT_FOUND)
        except PermissionError as exc:
            self._send_json({"error": str(exc)}, HTTPStatus.FORBIDDEN)
        except StaleCommandError as exc:
            self._send_json({"error": str(exc)}, HTTPStatus.CONFLICT)
        except ValueError as exc:
            self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        except RuntimeError as exc:
            self._send_json({"error": str(exc)}, HTTPStatus.SERVICE_UNAVAILABLE)
        except Exception as exc:
            self._send_json({"error": f"internal server error: {exc}"}, HTTPStatus.INTERNAL_SERVER_ERROR)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Serve the MOF robot web control console.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=4173)
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument("--motion-only", action="store_true", help="Enable only ROS motion publishing.")
    mode_group.add_argument(
        "--playback-only", action="store_true", help="Enable only isolated telemetry rosbag playback."
    )
    mode_group.add_argument(
        "--console",
        action="store_true",
        help="Enable manual motion and isolated telemetry playback with a runtime interlock.",
    )
    parser.add_argument(
        "--pollution-demo",
        action="store_true",
        help="Enable the read-only deterministic pollution simulation preview.",
    )
    parser.add_argument(
        "--bag-root",
        action="append",
        type=Path,
        help="Directory to scan recursively for rosbag metadata.yaml files; may be repeated.",
    )
    parser.add_argument(
        "--evidence-dir",
        type=Path,
        help="Directory for rosbag process command, PID/PGID, stdout and stderr evidence.",
    )
    args = parser.parse_args(argv)
    operational = args.motion_only or args.playback_only or args.console
    if args.pollution_demo and operational:
        parser.error("--pollution-demo cannot be combined with an operational control mode")
    if args.pollution_demo and args.host not in LOOPBACK_HOSTS:
        parser.error("pollution demo must bind to loopback")
    if operational and args.host not in LOOPBACK_HOSTS:
        parser.error("operational modes must bind to loopback; use an SSH port forward")
    return args


def main() -> None:
    args = parse_args()
    mode = (
        "motion"
        if args.motion_only
        else "playback"
        if args.playback_only
        else "console"
        if args.console
        else "pollution"
        if args.pollution_demo
        else "preview"
    )
    configured_roots = []
    if mode != "pollution":
        configured_roots = args.bag_root
        if not configured_roots:
            environment_roots = os.environ.get("MOF_BAG_ROOTS", "")
            configured_roots = [Path(item) for item in environment_roots.split(os.pathsep) if item]
        if not configured_roots:
            configured_roots = [PROJECT_ROOT / "recovery"]

    motion = MotionPublisher(enabled=mode in MOTION_MODES)
    sequencer = SequencedMotionController(motion)
    bags = BagManager(
        configured_roots,
        playback_enabled=mode in PLAYBACK_MODES,
        evidence_dir=args.evidence_dir,
        motion=motion,
    )
    server = ControlServer(
        (args.host, args.port), ControlHandler, mode, motion, sequencer, bags,
        pollution_demo=args.pollution_demo,
    )

    print(f"MOF web console: http://{args.host}:{args.port}", flush=True)
    print(f"MODE={mode}", flush=True)
    print(f"ROS motion: {'ready' if motion.available else 'unavailable'}", flush=True)
    print(f"POLLUTION_DEMO={'enabled' if args.pollution_demo else 'disabled'}", flush=True)
    if mode in PLAYBACK_MODES:
        print(f"PLAYBACK_ROS_DOMAIN_ID={PLAYBACK_ROS_DOMAIN_ID}", flush=True)
        print(f"MOTION_REPLAY_DEADMAN_SECONDS={MOTION_REPLAY_DEADMAN_SECONDS}", flush=True)
        print(f"PLAYBACK_EVIDENCE_DIR={bags.evidence_dir}", flush=True)
    print("Bag roots:", flush=True)
    for root in bags.roots:
        print(f"  {root}", flush=True)

    stop_requested = threading.Event()

    def request_stop(_signum=None, _frame=None):
        if stop_requested.is_set():
            return
        stop_requested.set()
        threading.Thread(target=server.shutdown, daemon=True).start()

    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, request_stop)

    try:
        server.serve_forever(poll_interval=0.25)
    finally:
        try:
            bags.shutdown()
        finally:
            try:
                bags.stop()
            finally:
                zero_report = motion.shutdown()
                print("SHUTDOWN_ZERO_REPORT=" + json.dumps(zero_report, ensure_ascii=False), flush=True)
                server.server_close()


if __name__ == "__main__":
    main()
