import csv
import io
import unittest
from contextlib import contextmanager, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from examples import demo_management_metrics as metrics
from rover.protocol.management_codec import SessionOpened


class FakeManagementConnections:
    def __init__(self, configurations):
        self.configurations = iter(configurations)
        self.elapsed_ms = 0
        self.active = False
        self.connection_count = 0

    def clock_ns(self):
        return self.elapsed_ms * 1_000_000

    @contextmanager
    def connect(self):
        if self.active:
            raise AssertionError("previous connection is still open")
        connect_ms, requests = next(self.configurations)
        self.connection_count += 1
        self.elapsed_ms += connect_ms
        self.active = True
        requests = iter(requests)

        def open_session():
            duration_ms, session_id = next(requests)
            self.elapsed_ms += duration_ms
            return SessionOpened(2, session_id)

        try:
            yield SimpleNamespace(open_session=open_session)
        finally:
            self.elapsed_ms += 100
            self.active = False


class TestManagementMetrics(unittest.TestCase):
    def test_rtt_excludes_warmup_and_reconnect_includes_dial_and_first_reply(self):
        service = FakeManagementConnections([
            (5, [(50, "first-session"), (2, "first-session"), (4, "first-session")]),
            (8, [(3, "second-session")]),
            (12, [(5, "third-session")]),
        ])
        output = io.StringIO()

        rtt, reconnect = metrics.measure_management(
            service.connect, output, rtt_samples=2, reconnect_samples=2,
            clock_ns=service.clock_ns,
        )

        self.assertEqual((rtt, reconnect), ([2, 4], [11, 17]))
        self.assertEqual(service.connection_count, 3)
        rows = list(csv.DictReader(io.StringIO(output.getvalue())))
        self.assertEqual(
            [(r["measurement"], r["sample"], r["status"], r["duration_ms"]) for r in rows],
            [("management_rtt", "1", "ok", "2.000"),
             ("management_rtt", "2", "ok", "4.000"),
             ("reconnect", "1", "ok", "11.000"),
             ("reconnect", "2", "ok", "17.000")],
        )
        self.assertNotIn("session", output.getvalue())

    def test_rtt_rejects_a_changed_session_and_keeps_completed_sample(self):
        service = FakeManagementConnections([
            (5, [(1, "original"), (2, "original"), (3, "unexpected")]),
        ])
        output = io.StringIO()

        with self.assertRaises(ValueError):
            metrics.measure_management(
                service.connect, output, rtt_samples=2, reconnect_samples=1,
                clock_ns=service.clock_ns,
            )

        rows = list(csv.DictReader(io.StringIO(output.getvalue())))
        self.assertEqual([(r["sample"], r["status"]) for r in rows], [("1", "ok"), ("2", "failed")])
        self.assertNotIn("unexpected", output.getvalue())
        self.assertFalse(service.active)

    def test_reconnect_rejects_a_session_used_before(self):
        service = FakeManagementConnections([
            (5, [(1, "original"), (2, "original")]),
            (8, [(2, "new")]),
            (10, [(2, "original")]),
        ])
        output = io.StringIO()

        with self.assertRaises(ValueError):
            metrics.measure_management(
                service.connect, output, rtt_samples=1, reconnect_samples=2,
                clock_ns=service.clock_ns,
            )

        rows = list(csv.DictReader(io.StringIO(output.getvalue())))
        self.assertEqual(
            [(r["measurement"], r["sample"], r["status"]) for r in rows],
            [("management_rtt", "1", "ok"), ("reconnect", "1", "ok"), ("reconnect", "2", "failed")],
        )
        self.assertNotIn("original", output.getvalue())
        self.assertFalse(service.active)

    def test_summary_reports_median_and_nearest_rank_p95(self):
        output = io.StringIO()
        with redirect_stdout(output):
            metrics.print_summary("Management RTT", list(range(20, 0, -1)))
        self.assertIn("P50 10.500 ms", output.getvalue())
        self.assertIn("P95 19.000 ms", output.getvalue())

    def test_cli_uses_existing_verified_connection_and_writes_new_file(self):
        service = FakeManagementConnections([
            (5, [(1, "original"), (2, "original")]), (8, [(2, "new")]),
        ])
        output = io.StringIO()
        with TemporaryDirectory() as directory:
            path = Path(directory) / "a10" / "management.csv"
            with (
                patch.object(metrics, "getpass", return_value="test-secret") as password,
                patch.object(metrics, "connect_management", side_effect=lambda *a, **k: service.connect()) as connect,
                redirect_stdout(output),
            ):
                result = metrics.main([
                    "--host", "192.0.2.20", "--ca-file", "sample-ca.pem", "--client-id", "test-desktop",
                    "--output", str(path), "--rtt-samples", "1", "--reconnect-samples", "1",
                ])
            self.assertEqual(result, 0)
            self.assertEqual(connect.call_count, 2)
            self.assertEqual(connect.call_args.args, (("192.0.2.20", 19177),))
            self.assertEqual(connect.call_args.kwargs, {
                "ca_file": Path("sample-ca.pem"), "server_hostname": "192.0.2.20",
                "client_id": "test-desktop", "credential": "test-secret",
            })
            password.assert_called_once_with("Credential (hidden): ")
            self.assertNotIn("test-secret", path.read_text(encoding="utf-8"))
            self.assertNotIn("test-secret", output.getvalue())

    def test_cli_preserves_existing_file(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "management.csv"
            path.write_text("previous evidence", encoding="utf-8")
            with (
                patch.object(metrics, "getpass") as password,
                patch.object(metrics, "connect_management") as connect,
                redirect_stdout(io.StringIO()),
            ):
                result = metrics.main(["--output", str(path)])
            self.assertEqual(result, 1)
            self.assertEqual(path.read_text(encoding="utf-8"), "previous evidence")
            password.assert_not_called()
            connect.assert_not_called()

    def test_cli_rejects_out_of_range_port_before_creating_report(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "management.csv"
            with (
                patch.object(metrics, "getpass") as password,
                patch.object(metrics, "connect_management") as connect,
                patch("sys.stderr", new=io.StringIO()),
                self.assertRaises(SystemExit) as raised,
            ):
                metrics.main(["--port", "65536", "--output", str(path)])
            self.assertEqual(raised.exception.code, 2)
            self.assertFalse(path.exists())
            password.assert_not_called()
            connect.assert_not_called()


if __name__ == "__main__":
    unittest.main()
