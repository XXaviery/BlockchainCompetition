from datetime import datetime,timedelta
from air_governance.common.types import EnvSample,QualityCode,SourceType
from air_governance.state.region_state import RegionStateBuilder


def sample(t,pm):
    return EnvSample(t,'A',0,0,0,pm,100,600,23,50,0,80,'','','IDLE',True,QualityCode.GOOD,SourceType.SIMULATION)


def test_state_slope_and_rolling():
    b=RegionStateBuilder(); t=datetime(2026,1,1)
    for i in range(5): st=b.update(sample(t+timedelta(seconds=30*i),10+i*2))
    assert st.slope_pm25 > 0
    assert st.rolling_max_pm25 >= st.current_pm25
