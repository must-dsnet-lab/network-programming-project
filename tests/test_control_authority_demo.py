from contextlib import redirect_stdout
from io import StringIO
import unittest
from unittest.mock import patch

from examples.demo_control_authority import main


class TestControlAuthorityDemo(unittest.TestCase):
    def run_demo(self, choices: str) -> str:
        output = StringIO()
        with patch("sys.stdin", StringIO(choices)), redirect_stdout(output):
            main()
        return output.getvalue()

    def test_handoff_waits_for_zero_and_a_separate_enable(self):
        output = self.run_demo("\n1\n\n")
        expected = (
            "A 已应用目标｜状态=controlling 持有者=A 代次=1 速度=1.0 m/s",
            "B 在 A 控制期间申请：authority_busy",
            "等待归零｜状态=stopping 持有者=无 代次=无 速度=1.0 m/s",
            "B 提前使能：vehicle_stopping",
            "已归零，B 待使能｜状态=held 持有者=B 代次=2 速度=0.0 m/s",
            "A 用旧授权使能：not_authority_holder",
            "B 已使能并应用目标｜状态=controlling 持有者=B 代次=2 速度=1.0 m/s",
        )
        last = -1
        for line in expected:
            self.assertIn(line, output)
            position = output.index(line)
            self.assertGreater(position, last)
            last = position

    def test_recipient_disconnect_leaves_vehicle_unowned_at_zero(self):
        output = self.run_demo("\n2\n")
        self.assertIn(
            "B 断开后已归零｜状态=idle 持有者=无 代次=无 速度=0.0 m/s",
            output,
        )

    def test_fault_waits_for_reset_and_leaves_vehicle_unowned(self):
        output = self.run_demo("\n3\n\n")
        self.assertIn(
            "故障锁存，已归零｜状态=fault_latched 持有者=无 代次=无 速度=0.0 m/s",
            output,
        )
        self.assertIn("B 在锁存期间申请：fault_latched", output)
        self.assertIn(
            "显式复位完成｜状态=idle 持有者=无 代次=无 速度=0.0 m/s",
            output,
        )
