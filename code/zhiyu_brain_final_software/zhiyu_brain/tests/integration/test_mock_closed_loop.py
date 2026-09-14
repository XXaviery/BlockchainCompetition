from zhiyu_brain.adapters.mock_navigation import MockNavigationAdapter
from zhiyu_brain.adapters.mock_purification import MockPurificationAdapter
from zhiyu_brain.safety.supervisor import SafetySupervisor
from zhiyu_brain.task.manager import TaskManager
from zhiyu_brain.common.types import CandidateTask,RobotStatus,Trend


def test_task_manager_executes_mock_cycle(tmp_path):
    nav=MockNavigationAdapter(); fan=MockPurificationAdapter(); manager=TaskManager(nav,fan,SafetySupervisor(),None)
    task=CandidateTask('d','B',60,70,Trend.RISING,100,100,0,4,8,1,80,1,1,True,task_score=2)
    robot=RobotStatus(battery_soc=80)
    selected=manager.execute_cycle([task],robot)
    assert selected is not None
    assert manager.completed_cycles==1
    assert robot.current_region=='B'
