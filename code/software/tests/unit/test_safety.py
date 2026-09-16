from air_governance.common.types import CandidateTask,RobotStatus,Trend,SafetyAction
from air_governance.safety.supervisor import SafetySupervisor


def c(region='A'):
    return CandidateTask('d',region,50,60,Trend.RISING,10,10,0,1,2,1,80,0,1,True)


def test_emergency_stop_preempts_model():
    r=RobotStatus(emergency_stop=True)
    assert SafetySupervisor().check(c(),r).action == SafetyAction.SAFE_STOP


def test_forbidden_zone_rejected():
    r=RobotStatus(forbidden_regions={'B'})
    assert SafetySupervisor().check(c('B'),r).action == SafetyAction.REJECT
