from air_governance.adapters.mock_navigation import MockNavigationAdapter
from air_governance.adapters.mock_purification import MockPurificationAdapter
from air_governance.safety.supervisor import SafetySupervisor
from air_governance.task.manager import TaskManager
from air_governance.common.types import CandidateTask,RobotStatus,Trend


def test_task_manager_executes_mock_cycle(tmp_path):
    nav=MockNavigationAdapter(); fan=MockPurificationAdapter(); manager=TaskManager(nav,fan,SafetySupervisor(),None)
    task=CandidateTask('d','B',60,70,Trend.RISING,100,100,0,4,8,1,80,1,1,True,task_score=2)
    robot=RobotStatus(battery_soc=80)
    selected=manager.execute_cycle([task],robot)
    assert selected is not None
    assert manager.completed_cycles==1
    assert robot.current_region=='B'
