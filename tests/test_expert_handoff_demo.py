import io
import json
import unittest
from collections import deque
from contextlib import contextmanager, redirect_stdout
from pathlib import Path
from unittest.mock import MagicMock, Mock, call, patch

from examples import demo_expert_handoff as demo
from rover.core.vehicle_model import VehicleState
from rover.protocol.control_auth import verify_control_mac
from rover.protocol.telemetry_codec import TelemetrySnapshot, encode_telemetry


class TestExpertHandoffDemo(unittest.TestCase):
    def setUp(self):
        self.generation_a = {"epoch": "demo-epoch", "number": 1}
        self.generation_b = {"epoch": "demo-epoch", "number": 2}
        self.telemetry = deque()
        self.events = []
        self.a = Mock()
        self.a.open_session.return_value = "session-a"
        self.a.acquire_authority.return_value = {"accepted": True, "generation": self.generation_a}
        self.a.set_authority_enabled.return_value = {"accepted": True, "control_key": "01" * 32}
        self.a.issue_control_window.side_effect = [
            {"accepted": True, "window_id": "window-a", "lifetime_s": 0.25},
            {"accepted": False, "reason": "not_authority_holder"},
        ]
        self.a.transfer_authority.side_effect = self.transfer

        self.b = Mock()
        self.b.open_session.return_value = "session-b"
        self.b.acquire_authority.return_value = {"accepted": False, "reason": "authority_busy"}
        self.b.query_authority.side_effect = [
            {"owns_authority": False, "phase": "stopping"},
            {"owns_authority": True, "phase": "held", "generation": self.generation_b},
        ]
        self.b.set_authority_enabled.return_value = {"accepted": True, "control_key": "02" * 32}
        self.b.issue_control_window.return_value = {
            "accepted": True, "window_id": "window-b", "lifetime_s": 0.25,
        }
        self.b.release_authority.return_value = {"accepted": True}
        self.control_socket = MagicMock()
        self.telemetry_socket = MagicMock()
        for sock in (self.control_socket, self.telemetry_socket):
            sock.__enter__.return_value = sock
        self.control_socket.sendto.side_effect = self.send_control
        self.telemetry_socket.recvfrom.side_effect = self.recv_telemetry

    @contextmanager
    def connect(self, address, **options):
        self.events.append(("connect", options["client_id"]))
        client = self.a if len(self.events) == 1 else self.b
        yield client

    def transfer(self, *args):
        self.events.append(("transfer",))
        self.telemetry.append(0.0)
        return {"accepted": True, "phase": "stopping"}

    def send_control(self, packet, address):
        message = json.loads(packet)
        self.events.append(("send", message["session_id"]))
        self.telemetry.append(message["speed_mps"])
        return len(packet)

    def recv_telemetry(self, *args):
        if not self.telemetry:
            raise TimeoutError("no more telemetry")
        speed = self.telemetry.popleft()
        self.events.append(("telemetry", speed))
        snapshot = TelemetrySnapshot(VehicleState(0.0, 0.0, 0.0, speed), False)
        return encode_telemetry(snapshot), ("127.0.0.1", 50123)

    def run_demo(self):
        output = io.StringIO()
        with (
            patch.object(demo.secure_connection, "connect_expert_management", side_effect=self.connect) as connect,
            patch.object(demo, "getpass", return_value="local-demo-credential") as prompt,
            patch.object(demo.socket, "socket", side_effect=(self.telemetry_socket, self.control_socket)),
            patch.object(demo.socket, "gethostbyname", return_value="127.0.0.1"),
            patch.object(demo, "sleep"),
            redirect_stdout(output),
        ):
            result = demo.main([])
        return result, output.getvalue(), connect, self.control_socket.sendto, prompt

    def test_switches_moving_authority_at_zero_and_b_reenables(self):
        result, output, connect, send, prompt = self.run_demo()

        self.assertEqual(result, 0)
        self.assertEqual(self.events, [
            ("connect", "desktop-a"), ("connect", "desktop-a"),
            ("send", "session-a"), ("telemetry", 1.0), ("transfer",), ("telemetry", 0.0),
            ("send", "session-b"), ("telemetry", 1.0),
        ])
        self.assertEqual(connect.call_args_list, [call(
            ("127.0.0.1", 19177), ca_file=Path("local/certs/ca.pem"), server_hostname="localhost",
            client_id="desktop-a", credential="local-demo-credential",
        )] * 2)
        prompt.assert_called_once()
        self.telemetry_socket.bind.assert_called_once_with(("127.0.0.1", 10000))
        self.a.transfer_authority.assert_called_once_with("session-a", self.generation_a, "session-b")
        self.assertEqual(self.a.issue_control_window.call_count, 2)
        self.b.set_authority_enabled.assert_called_once_with("session-b", self.generation_b, True)
        self.b.release_authority.assert_called_once_with("session-b", self.generation_b)
        expected_commands = (
            ("session-a", "window-a", self.generation_a, b"\x01" * 32),
            ("session-b", "window-b", self.generation_b, b"\x02" * 32),
        )
        for sent, (session_id, window_id, generation, key) in zip(
            send.call_args_list, expected_commands, strict=True
        ):
            with self.subTest(session=session_id):
                packet, address = sent.args
                self.assertEqual(address, ("127.0.0.1", 9999))
                message = json.loads(packet)
                mac = message.pop("mac")
                self.assertEqual(message, {
                    "version": 3,
                    "type": "authenticated_control_command",
                    "session_id": session_id,
                    "seq": 0,
                    "speed_mps": 1.0,
                    "heading_rad": 0.0,
                    "window_id": window_id,
                    "generation": generation,
                })
                fields = json.dumps(message, sort_keys=True, separators=(",", ":")).encode("utf-8")
                self.assertTrue(verify_control_mac(fields, key, mac))
        self.assertIn("speed=0.0 m/s", output)
        self.assertNotIn("local-demo-credential", output)

    def test_unexpected_competing_grant_fails_before_publishing_control(self):
        self.b.acquire_authority.return_value = {"accepted": True}

        result, output, _, send, _ = self.run_demo()

        self.assertEqual(result, 1)
        self.assertIn("competing request from B", output)
        send.assert_not_called()

    def test_missing_zero_speed_telemetry_keeps_b_disabled(self):
        self.a.transfer_authority.side_effect = None
        self.a.transfer_authority.return_value = {"accepted": True, "phase": "stopping"}

        result, output, _, send, _ = self.run_demo()

        self.assertEqual(result, 1)
        self.assertIn("speed=0.0 m/s", output)
        send.assert_called_once()
        self.b.set_authority_enabled.assert_not_called()
