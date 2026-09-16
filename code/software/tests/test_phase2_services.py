from __future__ import annotations
import csv
import math
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

from services.gui_backend import GuiBackend
from services.replay_service import ReplayService
from services.report_exporter import ReportExporter
from air_governance.common.types import EnvSample, QualityCode, SourceType, RegionState, CandidateTask, RobotStatus, SystemSnapshot


@pytest.fixture
def backend(tmp_path):
    b=GuiBackend(ROOT, db_path=tmp_path/'test.sqlite', seed=20260999)
    yield b
    b.db.close()


def advance_to_deciding(b):
    b.step(force=True)
    b.step(force=True)
    return b.snapshot()


def test_01_contract_envsample_serializes_phase1_type():
    e=EnvSample(datetime(2026,1,1),'A',0,0,0,1,2,3,4,5,0,80,'','','IDLE',True,QualityCode.GOOD,SourceType.SIMULATION)
    d=e.to_dict()
    assert d['region_id']=='A' and d['source_type']=='SIMULATION'


def test_02_gui_backend_loads_phase1_xgboost_models(backend):
    assert backend.models.status=='READY'
    assert backend.models.risk_path.exists()
    assert backend.models.ranker_path.exists()


def test_03_sqlite_schema_is_available(backend):
    assert backend.db.table_count('env_samples')==0
    assert backend.db.table_count('candidate_tasks')==0


def test_04_spatial_state_contains_phase1_regionstates(backend):
    snap=backend.snapshot()
    assert set(snap.regions)=={'A','B','C','D'}
    assert all(isinstance(x,RegionState) for x in snap.regions.values())


def test_05_sensing_writes_environment_samples(backend):
    backend.step(force=True)
    assert backend.robot.state=='SENSING'
    assert backend.db.table_count('env_samples')==4


def test_06_candidate_table_is_generated_from_phase1_candidates(backend):
    snap=advance_to_deciding(backend)
    assert snap.robot.state=='DECIDING'
    assert len(snap.candidates)==4
    assert all(isinstance(c,CandidateTask) for c in snap.candidates)
    assert sum(c.selected for c in snap.candidates)==1


def test_07_candidate_table_has_required_fields(backend):
    advance_to_deciding(backend)
    row=backend.candidate_rows()[0]
    required={'region_id','current_risk','predicted_risk','trend','waiting_time','distance','estimated_move_time','estimated_energy','battery_soc','switch_cost','task_score','valid_flag','safety_result'}
    assert required.issubset(row)


def test_08_rule_and_ranker_can_switch_using_phase1_policy(backend):
    backend.set_policy('rule')
    snap=advance_to_deciding(backend)
    assert snap.robot.policy_mode=='rule'
    assert backend.policy_comparison()['rule_selection'] is not None


def test_09_model_error_falls_back_to_phase1_rule_policy(backend):
    backend.inject_safety_event('MODEL_ERROR')
    snap=advance_to_deciding(backend)
    assert snap.robot.policy_mode=='rule_fallback'
    assert snap.robot.model_status=='MODEL_ERROR'
    events=backend.db.query("SELECT * FROM safety_events WHERE event_type='MODEL_ERROR'")
    assert events and events[-1]['fallback']=='RuleBasedPolicy'


def test_10_low_battery_safety_is_phase1_supervisor_input(backend):
    backend.inject_safety_event('LOW_BATTERY')
    advance_to_deciding(backend)
    events=backend.db.query("SELECT * FROM safety_events WHERE event_type='LOW_BATTERY'")
    assert events and backend.robot.battery_soc<=12.0


def test_11_forbidden_zone_invalidates_region_c_candidate(backend):
    backend.inject_safety_event('FORBIDDEN_ZONE')
    advance_to_deciding(backend)
    c=[x for x in backend.candidates if x.region_id=='C'][0]
    assert c.valid_flag==0 and c.safety_result=='forbidden_zone'


def test_12_localization_error_rejects_movement(backend):
    current=backend.robot.current_region
    backend.inject_safety_event('LOCALIZATION_ERROR')
    advance_to_deciding(backend)
    assert backend.robot.target_region==current
    evt=backend.db.query("SELECT * FROM safety_events WHERE event_type='LOCALIZATION_ERROR'")[-1]
    assert evt['result']=='REJECT_MOVEMENT'


def test_13_demo_completes_three_cycles(backend):
    snap=backend.run_demo(3)
    assert snap.completed_cycles==3 and snap.final_state=='COMPLETED'
    assert isinstance(snap,SystemSnapshot)


def test_14_demo_writes_phase1_adapter_events(backend):
    backend.run_demo(3)
    assert backend.db.table_count('navigation_events')==6
    assert backend.db.table_count('purification_events')==3


def test_15_replay_service_can_replay_task(backend):
    backend.run_demo(3)
    replay=ReplayService(backend.db.db_path)
    ids=replay.task_ids(); assert len(ids)>=3
    timeline=replay.task_timeline(ids[0])
    assert any(r['state']=='NAVIGATING' for r in timeline)


def test_16_pause_and_single_step_semantics(backend):
    backend.pause(); before=backend.robot.state
    backend.step(force=False); assert backend.robot.state==before
    backend.step(force=True); assert backend.robot.state=='SENSING'


def test_17_reset_clears_unified_log(backend):
    backend.run_demo(3); backend.reset(clear_db=True)
    assert backend.completed_cycles==0 and backend.robot.state=='IDLE'
    assert backend.db.table_count('task_state_events')==0


def test_18_report_tables_export_and_relative_improvement(backend):
    backend.run_demo(3)
    paths=ReportExporter(ROOT,backend).export_tables()
    assert len(paths)==5 and all(p.exists() for p in paths)
    with open(ROOT/'outputs/report_tables/rank_metrics.csv',newline='',encoding='utf-8') as f:
        row=next(csv.DictReader(f))
    ranker=float(row['mean_selected_utility_ranker']); rule=float(row['mean_selected_utility_rule']); rel=float(row['relative_improvement_percent'])
    assert math.isclose(rel,(ranker-rule)/rule*100.0,rel_tol=1e-12)


def test_19_report_figure_export_creates_required_pngs(backend):
    backend.run_demo(3)
    paths=ReportExporter(ROOT,backend).export_figures()
    names={p.name for p in paths}
    required={f'{i:02d}_' for i in range(1,15)}
    assert len(paths)==14
    assert all(any(name.startswith(prefix) for name in names) for prefix in required)
    assert all(p.stat().st_size>10_000 for p in paths)


def test_20_report_png_resolution_exact_1600x900(backend):
    backend.run_demo(3)
    path=ReportExporter(ROOT,backend).export_figures()[0]
    from PIL import Image
    with Image.open(path) as im:
        assert (im.width,im.height)==(1600,900)
