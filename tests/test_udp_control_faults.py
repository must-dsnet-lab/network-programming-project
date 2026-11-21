import csv
import io
import unittest
from contextlib import redirect_stderr
from unittest.mock import Mock, call, patch

from rover.transport import udp_control_faults


class TestUdpControlFaults(unittest.TestCase):
    desktop = ("192.168.174.1", 55186)
    vehicle = ("192.168.174.128", 9999)

    def run_relay(self, receives, *, loss_percent=0, delay_ms=0, seed=42):
        now = 10.0
        incoming = iter(receives)
        sock = Mock()
        log = io.StringIO()

        def receive(_size):
            nonlocal now
            now, packet = next(incoming)
            if isinstance(packet, BaseException):
                raise packet
            return packet

        sock.recvfrom.side_effect = receive
        with patch.object(udp_control_faults, "perf_counter", side_effect=lambda: now):
            udp_control_faults.relay_controls(
                sock, self.desktop[0], self.vehicle, log,
                loss_percent=loss_percent, delay_ms=delay_ms, seed=seed,
            )
        log.seek(0)
        return sock, list(csv.DictReader(log))

    def test_baseline_forwards_only_the_configured_desktop(self):
        sock, rows = self.run_relay([
            (10.01, (b"other", ("192.168.174.50", 55186))),
            (10.02, (b"control", self.desktop)),
            (10.03, KeyboardInterrupt()),
        ])
        sock.sendto.assert_called_once_with(b"control", self.vehicle)
        self.assertEqual([row["event"] for row in rows], [
            "relay_started", "source_ignored", "packet_forwarded", "relay_stopped",
        ])
        self.assertEqual(rows[2]["packet_index"], "1")

    def test_seeded_loss_records_the_exact_dropped_indices(self):
        packets = [(10.0 + index / 100, (bytes([index]), self.desktop)) for index in range(1, 11)]
        packets.append((11.0, KeyboardInterrupt()))
        for percent, dropped in ((5, [2, 10]), (15, [2, 8, 10])):
            with self.subTest(percent=percent):
                sock, rows = self.run_relay(packets, loss_percent=percent)
                repeated_sock, repeated_rows = self.run_relay(packets, loss_percent=percent)
                self.assertEqual(rows, repeated_rows)
                self.assertEqual(sock.sendto.call_args_list, repeated_sock.sendto.call_args_list)
                self.assertIn(f"loss_percent={percent}", rows[0]["detail"])
                self.assertIn("seed=42", rows[0]["detail"])
                self.assertEqual([
                    int(row["packet_index"]) for row in rows if row["event"] == "packet_dropped"
                ], dropped)
                self.assertEqual(sock.sendto.call_args_list, [
                    call(bytes([index]), self.vehicle) for index in range(1, 11) if index not in dropped
                ])

    def test_fixed_delay_preserves_order_and_flushes_during_idle(self):
        for delay_ms in (100, 300):
            with self.subTest(delay_ms=delay_ms):
                sock, rows = self.run_relay([
                    (10.0, (b"first", self.desktop)),
                    (10.05, (b"second", self.desktop)),
                    (10.0 + delay_ms / 1000 - 0.01, TimeoutError()),
                    (10.0 + delay_ms / 1000, TimeoutError()),
                    (10.05 + delay_ms / 1000, TimeoutError()),
                    (11.0, KeyboardInterrupt()),
                ], delay_ms=delay_ms)

                self.assertEqual(sock.sendto.call_args_list, [
                    call(b"first", self.vehicle), call(b"second", self.vehicle),
                ])
                forwards = [row for row in rows if row["event"] == "packet_forwarded"]
                self.assertEqual([row["packet_index"] for row in forwards], ["1", "2"])
                self.assertEqual([row["observed_delay_ms"] for row in forwards], [f"{delay_ms}.000"] * 2)

    def test_shutdown_records_pending_without_forwarding_it_early(self):
        sock, rows = self.run_relay([
            (10.0, (b"pending", self.desktop)), (10.05, KeyboardInterrupt()),
        ], delay_ms=100)
        sock.sendto.assert_not_called()
        self.assertEqual(rows[-1]["detail"], "pending_discarded=1")

    def test_cli_rejects_invalid_configuration_before_allocating_socket(self):
        for options in (
            ["--loss-percent", "10"],
            ["--delay-ms", "99"],
            ["--loss-percent", "5", "--delay-ms", "100"],
            ["--listen-port", "9999"],
            ["--desktop-host", "8.8.8.8"],
        ):
            with (
                self.subTest(options=options),
                patch.object(udp_control_faults.socket, "socket") as create_socket,
                redirect_stderr(io.StringIO()),
                self.assertRaises(SystemExit) as raised,
            ):
                udp_control_faults.main([
                    "--bind-host", "127.0.0.1", "--desktop-host", "127.0.0.1",
                    "--event-log", "faults.csv", *options,
                ])
            self.assertEqual(raised.exception.code, 2)
            create_socket.assert_not_called()


if __name__ == "__main__":
    unittest.main()
