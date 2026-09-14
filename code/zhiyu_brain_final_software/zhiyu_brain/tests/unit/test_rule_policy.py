from zhiyu_brain.common.types import CandidateTask,Trend
from zhiyu_brain.decision.rule_policy import RuleBasedPolicy


def mk(risk,dist,region):
    return CandidateTask('d',region,risk,risk,Trend.STABLE,0,0,0,dist,dist,dist,80,0,1,True)


def test_rule_prefers_high_risk_when_cost_close():
    ranked=RuleBasedPolicy().rank([mk(20,1,'A'),mk(70,1,'B')])
    assert ranked[0].region_id=='B'
