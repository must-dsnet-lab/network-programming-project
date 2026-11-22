import io
import unittest
from contextlib import contextmanager, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from examples import demo_expert_management as demo


class TestExpertManagementDemo(unittest.TestCase):
    def setUp(self):
        self.online = set()
        self.events = []
        self.connections = []
        self.session_ids = ["session-a", "session-b"]
        self.unreported_disconnect = 1

    @contextmanager
    def connect(self, address, **options):
        index = len(self.connections)
        self.connections.append((address, options))
        self.online.add(index)
        self.events.append(("connect", index))

        def open_session():
            self.assertIn(index, self.online)
            self.events.append(("open", index))
            return self.session_ids[index]

        def query_authority(session_id):
            self.assertIn(index, self.online)
            self.assertEqual(session_id, self.session_ids[index])
            self.events.append(("query", index, len(self.online)))
            online_sessions = len(self.online)
            if online_sessions == 1 and self.unreported_disconnect:
                online_sessions = 2
                self.unreported_disconnect -= 1
            return {
                "online_sessions": online_sessions,
                "phase": "idle",
                "owns_authority": False,
                "queue_wait_ms": 0.0,
                "tick_interval_ms": 20.0,
            }

        try:
            yield SimpleNamespace(open_session=open_session, query_authority=query_authority)
        finally:
            self.online.remove(index)
            self.events.append(("close", index))

    def run_demo(self, options=(), clock=(0.0, 0.05), credentials=("hidden-a",)):
        stdout = io.StringIO()
        with (
            patch.object(demo.secure_connection, "connect_expert_management", self.connect),
            patch.object(demo, "getpass", side_effect=credentials) as prompt,
            patch.object(demo, "monotonic", side_effect=clock),
            patch.object(demo, "sleep") as sleep,
            redirect_stdout(stdout),
        ):
            result = demo.main(list(options))
        return result, stdout.getvalue(), prompt.call_count, sleep.call_count

    def test_observes_two_sessions_and_keeps_b_open_after_a_closes(self):
        result, output, prompts, sleeps = self.run_demo()

        self.assertEqual(result, 0)
        self.assertEqual((prompts, sleeps), (1, 1))
        self.assertEqual(self.events, [
            ("connect", 0), ("open", 0), ("connect", 1), ("open", 1),
            ("query", 0, 2), ("query", 1, 2), ("close", 0),
            ("query", 1, 1), ("query", 1, 1), ("open", 1), ("close", 1),
        ])
        self.assertEqual(self.connections, [
            (("127.0.0.1", 19177), {
                "ca_file": Path("local/certs/ca.pem"),
                "server_hostname": "localhost",
                "client_id": "desktop-a",
                "credential": "hidden-a",
            }),
        ] * 2)
        self.assertIn("A=2 B=2", output)
        self.assertIn("B session unchanged", output)
        self.assertNotIn("hidden-a", output)

    def test_closes_both_connections_if_two_connections_return_same_session(self):
        self.session_ids[1] = self.session_ids[0]

        result, output, _, _ = self.run_demo()

        self.assertEqual(result, 1)
        self.assertIn("different session IDs", output)
        self.assertEqual(self.events[-2:], [("close", 1), ("close", 0)])

    def test_reports_timeout_when_a_disconnect_is_not_observed(self):
        self.unreported_disconnect = 10

        result, output, _, _ = self.run_demo(clock=(0.0, 4.0))

        self.assertEqual(result, 1)
        self.assertIn("timed out", output)
        self.assertFalse(self.online)

    def test_uses_interface_options_and_accepts_two_accounts(self):
        result, output, prompts, _ = self.run_demo(
            options=(
                "--host", "127.0.0.2", "--port", "19443", "--server-name", "vehicle.test",
                "--ca-file", "ca.pem", "--client-id", "desktop-a", "--client-id-b", "desktop-b",
            ),
            credentials=("hidden-a", "hidden-b"),
        )

        self.assertEqual((result, prompts), (0, 2))
        for connection, client_id, credential in zip(
            self.connections, ("desktop-a", "desktop-b"), ("hidden-a", "hidden-b")
        ):
            self.assertEqual(connection, (
                ("127.0.0.2", 19443),
                {"ca_file": Path("ca.pem"), "server_hostname": "vehicle.test",
                 "client_id": client_id, "credential": credential},
            ))
            self.assertNotIn(credential, output)
