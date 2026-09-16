"""Deterministic, read-only pollution demo data for the Web preview.

The production HTTP path only reads the checked-in files below this module's
``assets/pollution`` directory through fixed filename mappings.  The optional
asset writer is an offline, deterministic convenience for refreshing the demo
fixtures; it never reads project reports, user paths, ROS data or sensors.
"""

from __future__ import annotations

import argparse
import json
import math
import re
from pathlib import Path
from typing import Any


WEB_ROOT = Path(__file__).resolve().parent
POLLUTION_ROOT = WEB_ROOT / "assets" / "pollution"
SCENARIO_ROOT = POLLUTION_ROOT / "scenarios"
SEED = 20260999
MAX_SCENARIO_BYTES = 256 * 1024
MAX_QUERY_CHARS = 512
SCENARIO_FILES = {
    "scenario_1_pm25_spike": "scenario_1_pm25_spike.json",
    "scenario_2_voc_rise": "scenario_2_voc_rise.json",
    "scenario_3_multi_region": "scenario_3_multi_region.json",
}
METRICS = (
    "pm25",
    "voc",
    "co2",
    "temperature",
    "humidity",
    "current_risk",
    "predicted_risk",
)
REGIONS = (
    ("A", "客厅", 0.0, 0.0),
    ("B", "卧室", 4.0, 0.0),
    ("C", "厨房", 0.0, 4.0),
    ("D", "工作区", 4.0, 4.0),
)
STEPS = 10
_STEP_RE = re.compile(r"^(?:0|[1-9][0-9]{0,2})$")
_SCENARIO_ID_RE = re.compile(r"^[a-z0-9_]{1,64}$")


class PollutionRequestError(ValueError):
    """A rejected pollution catalog/snapshot request or invalid fixture."""


def _finite(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PollutionRequestError(f"{label} must be a finite number")
    converted = float(value)
    if not math.isfinite(converted):
        raise PollutionRequestError(f"{label} must be a finite number")
    return converted


def _reject_json_constant(value: str) -> None:
    raise PollutionRequestError(f"non-finite JSON constant is not allowed: {value}")


def _read_json(path: Path) -> dict[str, Any]:
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise PollutionRequestError("pollution demo fixture is unavailable") from exc
    if size > MAX_SCENARIO_BYTES:
        raise PollutionRequestError("pollution demo fixture is too large")
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"),
            parse_constant=_reject_json_constant,
        )
    except PollutionRequestError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PollutionRequestError("pollution demo fixture is invalid") from exc
    if not isinstance(value, dict):
        raise PollutionRequestError("pollution demo fixture must be a JSON object")
    return value


def _validate_common_metadata(payload: dict[str, Any]) -> None:
    expected = {
        "source_type": "SIMULATION",
        "evidence_class": "DEMO_ONLY",
        "real_measurement": False,
        "seed": SEED,
    }
    for key, expected_value in expected.items():
        if payload.get(key) != expected_value:
            raise PollutionRequestError(f"pollution fixture metadata mismatch: {key}")


def _validate_scenario(payload: dict[str, Any], scenario_id: str) -> None:
    _validate_common_metadata(payload)
    if payload.get("scenario_id") != scenario_id:
        raise PollutionRequestError("pollution fixture scenario_id mismatch")
    regions = payload.get("regions")
    if not isinstance(regions, list) or [item.get("region_id") for item in regions] != [r[0] for r in REGIONS]:
        raise PollutionRequestError("pollution fixture regions are invalid")
    for item, expected in zip(regions, REGIONS):
        if not isinstance(item, dict) or item.get("region_id") != expected[0]:
            raise PollutionRequestError("pollution fixture region is invalid")
        if item.get("name") != expected[1]:
            raise PollutionRequestError("pollution fixture region name is invalid")
        if _finite(item.get("x"), "region.x") != expected[2] or _finite(item.get("y"), "region.y") != expected[3]:
            raise PollutionRequestError("pollution fixture region coordinates are invalid")

    snapshots = payload.get("snapshots")
    if not isinstance(snapshots, list) or len(snapshots) != STEPS:
        raise PollutionRequestError("pollution fixture time steps are invalid")
    for expected_step, snapshot in enumerate(snapshots):
        if not isinstance(snapshot, dict) or snapshot.get("step") != expected_step:
            raise PollutionRequestError("pollution fixture step is invalid")
        records = snapshot.get("regions")
        if not isinstance(records, list) or len(records) != len(REGIONS):
            raise PollutionRequestError("pollution fixture snapshot regions are invalid")
        for record, expected_region in zip(records, REGIONS):
            if not isinstance(record, dict) or record.get("region_id") != expected_region[0]:
                raise PollutionRequestError("pollution fixture snapshot region is invalid")
            if record.get("step") != expected_step:
                raise PollutionRequestError("pollution fixture record step is invalid")
            _finite(record.get("x"), "region.x")
            _finite(record.get("y"), "region.y")
            for metric in METRICS:
                _finite(record.get(metric), metric)


def _load_scenario(scenario_id: str) -> dict[str, Any]:
    if not isinstance(scenario_id, str) or not _SCENARIO_ID_RE.fullmatch(scenario_id):
        raise PollutionRequestError("scenario_id is not allowed")
    if any(token in scenario_id for token in ("..", "/", "\\", ":")):
        raise PollutionRequestError("scenario_id is not allowed")
    filename = SCENARIO_FILES.get(scenario_id)
    if filename is None:
        raise PollutionRequestError("unknown scenario_id")
    payload = _read_json(SCENARIO_ROOT / filename)
    _validate_scenario(payload, scenario_id)
    return payload


def catalog_payload() -> dict[str, Any]:
    manifest = _read_json(POLLUTION_ROOT / "manifest.json")
    _validate_common_metadata(manifest)
    scenarios = manifest.get("scenarios")
    if not isinstance(scenarios, list) or {item.get("scenario_id") for item in scenarios if isinstance(item, dict)} != set(SCENARIO_FILES):
        raise PollutionRequestError("pollution catalog is invalid")
    return {
        "source_type": "SIMULATION",
        "evidence_class": "DEMO_ONLY",
        "real_measurement": False,
        "seed": SEED,
        "metrics": list(METRICS),
        "scenarios": scenarios,
    }


def _validate_query_text(value: str | None, label: str) -> str:
    if value is None or not isinstance(value, str) or not value:
        raise PollutionRequestError(f"{label} is required")
    if len(value) > 128 or any(token in value for token in ("..", "/", "\\", "\x00")):
        raise PollutionRequestError(f"{label} is not allowed")
    return value


def snapshot_payload(scenario_id: str | None, step_text: str | None, metric: str | None) -> dict[str, Any]:
    scenario_id = _validate_query_text(scenario_id, "scenario_id")
    payload = _load_scenario(scenario_id)
    if step_text is None or not _STEP_RE.fullmatch(step_text):
        raise PollutionRequestError("step must be a non-negative integer")
    step = int(step_text)
    if step < 0 or step >= STEPS:
        raise PollutionRequestError("step is outside the scenario range")
    if metric is not None:
        metric = _validate_query_text(metric, "metric")
        if metric not in METRICS:
            raise PollutionRequestError("metric is not allowed")

    selected = next(item for item in payload["snapshots"] if item["step"] == step)
    records: list[dict[str, Any]] = []
    for item in selected["regions"]:
        record = dict(item)
        if metric is not None:
            record["metric_value"] = record[metric]
        records.append(record)
    return {
        "source_type": "SIMULATION",
        "evidence_class": "DEMO_ONLY",
        "real_measurement": False,
        "seed": SEED,
        "scenario_id": scenario_id,
        "title": payload["title"],
        "step": step,
        "metric": metric,
        "regions": records,
    }


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def _round(value: float, digits: int = 2) -> float:
    return round(float(value), digits)


def _scenario_values(scenario_id: str, step: int, region_index: int) -> dict[str, float]:
    """Generate one fixture record using only fixed arithmetic and ``SEED``."""
    phase = (SEED % 31) / 100.0
    pm25 = 18.0 + region_index * 2.5 + phase + step * 0.25
    voc = 0.24 + region_index * 0.035 + (step % 3) * 0.01
    co2 = 585.0 + region_index * 27.0 + step * 4.0
    temperature = 22.4 + region_index * 0.35 + step * 0.04
    humidity = 45.0 + region_index * 2.2 + ((step + region_index) % 4) * 0.6

    if scenario_id == "scenario_1_pm25_spike":
        spike = max(0, 5 - abs(step - 5)) * (8.5 if region_index == 2 else 1.3)
        pm25 += spike
        co2 += max(0, step - 3) * (5.0 if region_index == 2 else 1.0)
    elif scenario_id == "scenario_2_voc_rise":
        rise = max(0, step - 1) * (0.22 if region_index == 1 else 0.025)
        voc += rise
        pm25 += max(0, step - 5) * (1.2 if region_index == 1 else 0.2)
    elif scenario_id == "scenario_3_multi_region":
        pm25 += step * (1.1 + region_index * 0.15)
        voc += step * (0.07 + region_index * 0.01)
        co2 += step * (16.0 + region_index * 2.0)
    else:
        raise ValueError(f"unsupported generator scenario: {scenario_id}")

    current_risk = _clamp(
        0.62 * pm25 + 12.0 * voc + 0.018 * max(co2 - 400.0, 0.0)
        + 0.8 * max(temperature - 24.0, 0.0) + 0.12 * max(humidity - 55.0, 0.0)
    )
    predicted_risk = _clamp(current_risk + (2.0 + step * 0.15 if scenario_id != "scenario_1_pm25_spike" else 3.2))
    return {
        "pm25": _round(pm25),
        "voc": _round(voc, 3),
        "co2": _round(co2),
        "temperature": _round(temperature),
        "humidity": _round(humidity),
        "current_risk": _round(current_risk),
        "predicted_risk": _round(predicted_risk),
    }


def generate_scenario(scenario_id: str) -> dict[str, Any]:
    titles = {
        "scenario_1_pm25_spike": "场景 1 · PM2.5 突增",
        "scenario_2_voc_rise": "场景 2 · VOC 持续上升",
        "scenario_3_multi_region": "场景 3 · 多区域复合污染",
    }
    descriptions = {
        "scenario_1_pm25_spike": "固定种子下，厨房区域出现 PM2.5 峰值并向邻域扩散。",
        "scenario_2_voc_rise": "固定种子下，卧室 VOC 按时间步上升，其他区域保持低幅波动。",
        "scenario_3_multi_region": "固定种子下，四个区域按不同梯度同步变化。",
    }
    snapshots = []
    for step in range(STEPS):
        records = []
        for index, (region_id, _name, x, y) in enumerate(REGIONS):
            records.append(
                {
                    "region_id": region_id,
                    "step": step,
                    "x": x,
                    "y": y,
                    **_scenario_values(scenario_id, step, index),
                }
            )
        snapshots.append({"step": step, "regions": records})
    return {
        "scenario_id": scenario_id,
        "title": titles[scenario_id],
        "description": descriptions[scenario_id],
        "source_type": "SIMULATION",
        "evidence_class": "DEMO_ONLY",
        "real_measurement": False,
        "seed": SEED,
        "metrics": list(METRICS),
        "regions": [
            {"region_id": rid, "name": name, "x": x, "y": y}
            for rid, name, x, y in REGIONS
        ],
        "snapshots": snapshots,
    }


def write_demo_assets() -> None:
    """Write the deterministic fixtures; intended for offline maintenance only."""
    SCENARIO_ROOT.mkdir(parents=True, exist_ok=True)
    manifest = {
        "source_type": "SIMULATION",
        "evidence_class": "DEMO_ONLY",
        "real_measurement": False,
        "seed": SEED,
        "generator": "pollution_demo._scenario_values; deterministic arithmetic",
        "metrics": list(METRICS),
        "scenarios": [
            {
                "scenario_id": scenario_id,
                "title": generate_scenario(scenario_id)["title"],
                "description": generate_scenario(scenario_id)["description"],
                "file": filename,
                "steps": STEPS,
                "regions": [item[0] for item in REGIONS],
            }
            for scenario_id, filename in SCENARIO_FILES.items()
        ],
    }
    (POLLUTION_ROOT / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    for scenario_id, filename in SCENARIO_FILES.items():
        payload = generate_scenario(scenario_id)
        (SCENARIO_ROOT / filename).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
            encoding="utf-8",
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Write deterministic pollution demo fixtures.")
    parser.add_argument("--write-assets", action="store_true")
    args = parser.parse_args()
    if not args.write_assets:
        parser.error("pass --write-assets to refresh the checked-in demo fixtures")
    write_demo_assets()
