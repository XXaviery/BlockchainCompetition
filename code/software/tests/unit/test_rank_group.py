import pandas as pd
from air_governance.models.ranker_model import _prepare_grouped


def test_decision_group_is_atomic():
    df=pd.DataFrame({'decision_id':['d2','d1','d1','d2'],'region_id':['B','A','B','A']})
    ordered,sizes=_prepare_grouped(df)
    assert sizes.tolist()==[2,2]
    assert ordered.iloc[:2].decision_id.nunique()==1
