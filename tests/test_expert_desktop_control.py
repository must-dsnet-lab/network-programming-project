import unittest
from io import StringIO
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PySide6.QtWidgets import QApplication

from rover.core.control_target import ControlTarget
from rover.core.vehicle_model import VehicleState
from rover.desktop.app import DesktopConsole, main
from rover.protocol.control_codec import AuthorityGeneration
from rover.protocol.telemetry_codec import TelemetrySnapshot
from tests.qt_fixture import dispose_widget
from tests.test_desktop_control import FakeSocket


class TestExpertDesktopControl(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.worker = Mock()
        self.worker.apply_target.return_value = True
        self.worker.wait_closed.return_value = True
        self.set_status(phase="idle", allowed=("acquire_authority",))
        socket_patch = patch("rover.desktop.app.socket.socket", return_value=FakeSocket([]))
        socket_patch.start()
        self.addCleanup(socket_patch.stop)
        self.window = DesktopConsole(secure_worker=self.worker, expert_mode=True, operator_id="desk-a")
        self.addCleanup(dispose_widget, self.window)

    def set_status(self, *, phase, allowed=(), owns=False, number=None, holder=None,
                   state="ready", pending=False, last_result=None):
        self.worker.status.return_value = SimpleNamespace(
            state=state,
            detail="",
            pending=pending,
            authority=SimpleNamespace(
                phase=phase,
                holder_client_id=holder,
                generation=AuthorityGeneration("test-epoch", number) if number is not None else None,
                owns_authority=owns,
                lease_remaining_s=2.4 if owns else None,
                allowed_actions=frozenset(allowed),
                last_result=last_result,
            ),
        )

    def report_telemetry(self, *, fault=False):
        packet = TelemetrySnapshot(state=VehicleState(), fault_latched=fault), ("127.0.0.1", 9999)
        with patch("rover.desktop.app.try_receive_telemetry", side_effect=[packet, None]):
            self.window.poll_telemetry()

    def test_waiting_console_shows_holder_but_cannot_apply(self):
        self.assertTrue(self.window.acquire_authority_button.isEnabled())
        self.window.acquire_authority_button.click()
        self.worker.acquire_authority.assert_called_once_with()

        self.report_telemetry()
        self.set_status(phase="controlling", holder="desk-b", number=4)
        self.window.poll_secure_status()

        self.assertIn("desk-b", self.window.authority_holder.text())
        self.assertIn("4", self.window.authority_generation.text())
        self.assertFalse(self.window.acquire_authority_button.isEnabled())
        self.assertFalse(self.window.start_button.isEnabled())
        self.assertFalse(self.window.emergency_stop_button.isEnabled())
        self.window.start_control()
        self.worker.apply_target.assert_not_called()

    def test_new_generation_requires_explicit_apply_and_fresh_telemetry(self):
        self.set_status(phase="held", allowed=("set_authority_enabled",), owns=True, number=1, holder="desk-a")
        self.window.poll_secure_status()
        self.assertFalse(self.window.start_button.isEnabled())
        self.report_telemetry()
        self.assertTrue(self.window.start_button.isEnabled())
        self.window.speed_input.setValue(1.0)
        self.window.start_button.click()
        self.assertEqual(self.window.current_target, ControlTarget(1.0, 0.0))

        self.set_status(phase="held", allowed=("set_authority_enabled",), owns=True, number=2, holder="desk-a")
        self.window.poll_secure_status()

        self.assertIsNone(self.window.current_target)
        self.assertFalse(self.window.stop_button.isEnabled())
        self.worker.apply_target.assert_called_once_with(ControlTarget(1.0, 0.0))

    def test_renewal_pending_leaves_local_stop_available(self):
        self.report_telemetry()
        self.set_status(phase="controlling", allowed=("set_authority_enabled", "renew_authority"),
                        owns=True, number=1, holder="desk-a", state="sending")
        self.window.poll_secure_status()
        self.window.start_button.click()

        self.set_status(phase="controlling", allowed=("set_authority_enabled", "renew_authority"),
                        owns=True, number=1, holder="desk-a", state="sending", pending=True)
        self.window.poll_secure_status()

        self.assertFalse(self.window.renew_authority_button.isEnabled())
        self.assertFalse(self.window.start_button.isEnabled())
        self.assertTrue(self.window.stop_sending_button.isEnabled())
        self.window.stop_sending_button.click()
        self.worker.stop_sending.assert_called_once_with()

    def test_authority_actions_and_feedback_use_fresh_capabilities(self):
        self.assertFalse(self.window.auto_renew_checkbox.isChecked())
        self.report_telemetry()
        self.set_status(phase="held", allowed=("renew_authority", "release_authority", "transfer_authority"),
                        owns=True, number=2, holder="desk-a")
        self.window.poll_secure_status()
        self.assertIn("2.4", self.window.authority_lease.text())
        self.window.auto_renew_checkbox.click()
        self.worker.set_auto_renew.assert_called_once_with(True)
        self.window.renew_authority_button.click()
        self.window.release_authority_button.click()
        self.window.transfer_authority_button.click()
        for action in ("renew_authority", "release_authority", "transfer_authority"):
            getattr(self.worker, action).assert_called_once_with()

        result = SimpleNamespace(action="renew_authority", accepted=False, reason="stale_generation")
        self.set_status(phase="held", owns=True, number=2, holder="desk-a", last_result=result)
        self.window.poll_secure_status()
        self.assertIn("续期", self.window.authority_result.text())
        self.assertIn("代次", self.window.authority_result.text())
        self.assertFalse(self.window.transfer_authority_button.isEnabled())

    def test_reset_requires_server_permission_then_fresh_unlocked_telemetry(self):
        self.report_telemetry(fault=True)
        self.set_status(phase="fault_latched", allowed=("reset_control",), state="latched")
        self.window.poll_secure_status()
        self.assertTrue(self.window.reset_button.isEnabled())
        self.assertFalse(self.window.acquire_authority_button.isEnabled())

        def queue_reset():
            self.set_status(phase="fault_latched", state="resetting", pending=True)
            return True

        with patch.object(self.worker.reset_control, "side_effect", queue_reset):
            self.window.reset_button.click()
        reset = SimpleNamespace(action="reset_control", accepted=True, reason=None)
        self.set_status(phase="idle", allowed=("acquire_authority",), last_result=reset)
        self.window.poll_secure_status()
        self.assertFalse(self.window.acquire_authority_button.isEnabled())
        self.report_telemetry(fault=False)
        self.assertTrue(self.window.acquire_authority_button.isEnabled())
        self.assertFalse(self.window.start_button.isEnabled())


class TestExpertDesktopCli(unittest.TestCase):
    def test_cli_routes_expert_connection_and_separate_telemetry_port(self):
        with (
            patch("rover.desktop.app.getpass", return_value="test-only"),
            patch("rover.desktop.app.connect_expert_management", create=True) as connect_expert,
            patch("rover.desktop.app.ExpertControlWorker", create=True) as create_worker,
            patch("rover.desktop.app.QApplication") as create_app,
            patch("rover.desktop.app.DesktopConsole") as create_window,
        ):
            create_app.return_value.exec.return_value = 0
            code = main(["--secure", "--expert-management", "--client-id", "desk-b", "--telemetry-port", "10001"])
            self.assertEqual(code, 0)
            connect, destination = create_worker.call_args.args
            self.assertEqual(destination, ("127.0.0.1", 9999))
            connect()
            self.assertEqual(connect_expert.call_args.kwargs["client_id"], "desk-b")
            create_window.assert_called_once_with(
                secure_worker=create_worker.return_value,
                telemetry_address=("127.0.0.1", 10001),
                operator_id="desk-b",
                expert_mode=True,
            )

    def test_cli_expert_mode_requires_secure_before_prompting(self):
        with (
            patch("rover.desktop.app.getpass") as prompt,
            patch("sys.stderr", new_callable=StringIO) as stderr,
            self.assertRaises(SystemExit) as exit_info,
        ):
            main(["--expert-management"])
        self.assertEqual(exit_info.exception.code, 2)
        self.assertIn("需要 --secure", stderr.getvalue())
        prompt.assert_not_called()
