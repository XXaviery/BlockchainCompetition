from datetime import datetime
from zhiyu_brain.common.types import RegionState
from zhiyu_brain.labels.risk_teacher import RiskTeacher


def state(pm=10,voc=100,co2=600,slope=0,valid=True):
    return RegionState(datetime.now(),'A',pm,voc,co2,23,50,pm,voc,co2,pm,voc,co2,0,0,0,slope,0,0,0,0,0,30,valid,'GOOD',datetime.now(),80,10,1)


def test_risk_increases_with_pollution():
    t=RiskTeacher()
    assert t.current_risk(state(100,700,1800)) > t.current_risk(state(10,100,600))


def test_co2_not_purifiable_driver():
    t=RiskTeacher()
    low=t.purifiable_fraction(state(10,100,600))
    high_co2=t.purifiable_fraction(state(10,100,2500))
    assert abs(low-high_co2) < 1e-9
