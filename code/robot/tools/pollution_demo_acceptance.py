"""Offline acceptance for the read-only pollution simulation preview."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile
import threading
from urllib.error import HTTPError
from urllib.request import Request, urlopen


ROBOT_ROOT = Path(__file__).resolve().parents[1]
SERVER_PATH = ROBOT_ROOT / "web" / "server.py"


def load_server():
    pollution_path = ROBOT_ROOT / "web" / "pollution_demo.py"
    pollution_spec = importlib.util.spec_from_file_location("pollution_demo", pollution_path)
    if pollution_spec is None or pollution_spec.loader is None:
        raise RuntimeError("unable to load web/pollution_demo.py")
    pollution_module = importlib.util.module_from_spec(pollution_spec)
    import sys

    sys.modules["pollution_demo"] = pollution_module
    pollution_spec.loader.exec_module(pollution_module)
    spec = importlib.util.spec_from_file_location("pollution_demo_acceptance_server", SERVER_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("unable to load web/server.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def request(base: str, path: str, method: str = "GET") -> tuple[int, bytes, str]:
    req = Request(base + path, method=method, data=b"{}" if method != "GET" else None)
    try:
        with urlopen(req, timeout=3) as response:
            return response.status, response.read(), response.headers.get("content-type", "")
    except HTTPError as exc:
        return exc.code, exc.read(), exc.headers.get("content-type", "")


def expect_json(base: str, path: str, status: int = 200) -> dict:
    actual, body, content_type = request(base, path)
    if actual != status or "application/json" not in content_type:
        raise AssertionError(f"{path}: expected JSON {status}, got {actual} {content_type}")
    return json.loads(body.decode("utf-8"))


def expect_rejected(base: str, path: str, status: int = 400) -> None:
    actual, _body, _content_type = request(base, path)
    if actual != status:
        raise AssertionError(f"{path}: expected HTTP {status}, got {actual}")


def main() -> int:
    server_module = load_server()
    with tempfile.TemporaryDirectory(prefix="pollution_demo_acceptance_") as temp_dir:
        motion = server_module.MotionPublisher(enabled=False)
        sequencer = server_module.SequencedMotionController(motion)
        bags = server_module.BagManager([], playback_enabled=False, evidence_dir=Path(temp_dir), motion=motion)
        httpd = server_module.ControlServer(
            ("127.0.0.1", 0), server_module.ControlHandler, "pollution", motion, sequencer, bags,
            pollution_demo=True,
        )
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{httpd.server_address[1]}"
        try:
            catalog = expect_json(base, "/api/pollution/catalog")
            if catalog["seed"] != 20260999 or catalog["real_measurement"] is not False:
                raise AssertionError("catalog evidence metadata mismatch")
            if {item["scenario_id"] for item in catalog["scenarios"]} != {
                "scenario_1_pm25_spike", "scenario_2_voc_rise", "scenario_3_multi_region"
            }:
                raise AssertionError("catalog scenario whitelist mismatch")
            expect_rejected(base, "/api/pollution/catalog?unexpected=1")

            snapshot = expect_json(
                base,
                "/api/pollution/snapshot?scenario_id=scenario_1_pm25_spike&step=5&metric=current_risk",
            )
            if snapshot["step"] != 5 or len(snapshot["regions"]) != 4:
                raise AssertionError("snapshot shape mismatch")
            required = {"pm25", "voc", "co2", "temperature", "humidity", "current_risk", "predicted_risk", "x", "y", "step"}
            if not required.issubset(snapshot["regions"][0]):
                raise AssertionError("snapshot fields are incomplete")

            status, body, content_type = request(base, "/assets/pollution/previews/pollution_map_demo.svg")
            if status != 200 or "image/svg+xml" not in content_type or b"SIMULATION" not in body:
                raise AssertionError("pollution static preview is unavailable")

            expect_rejected(base, "/api/pollution/snapshot?scenario_id=../secret&step=0&metric=pm25")
            expect_rejected(base, "/api/pollution/snapshot?scenario_id=scenario_1_pm25_spike&step=999&metric=pm25")
            expect_rejected(base, "/api/pollution/snapshot?scenario_id=scenario_1_pm25_spike&step=NaN&metric=pm25")
            expect_rejected(base, "/api/pollution/snapshot?scenario_id=scenario_1_pm25_spike&step=0&metric=Infinity")
            expect_rejected(base, "/api/pollution/snapshot?scenario_id=scenario_1_pm25_spike&step=0&metric=pm25&extra=1")
            expect_rejected(base, "/api/pollution/snapshot?scenario_id=scenario_1_pm25_spike&step=0&metric=" + ("x" * 600))
            status, _body, _content_type = request(base, "/api/pollution/catalog", method="POST")
            if status != 405:
                raise AssertionError(f"pollution POST was not rejected: {status}")
            if motion.audit_snapshot() or motion.available:
                raise AssertionError("pollution requests touched motion control")
            try:
                server_module.parse_args(["--pollution-demo", "--host", "0.0.0.0"])
            except SystemExit as exc:
                if exc.code == 0:
                    raise AssertionError("pollution demo accepted a non-loopback bind")
            else:
                raise AssertionError("pollution demo accepted a non-loopback bind")
            print(json.dumps({
                "success": True,
                "catalog_scenarios": len(catalog["scenarios"]),
                "snapshot_regions": len(snapshot["regions"]),
                "static_preview": "PASS",
                "invalid_input_rejection": "PASS",
                "post_rejection": "PASS",
                "motion_isolation": "PASS",
            }, ensure_ascii=False, indent=2))
            return 0
        finally:
            httpd.shutdown()
            httpd.server_close()
            bags.shutdown()
            bags.stop()
            motion.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
