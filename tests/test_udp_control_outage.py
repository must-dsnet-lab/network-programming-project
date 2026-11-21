import csv
import io
import unittest
from contextlib import redirect_stderr, redirect_stdout
from queue import Queue
from unittest.mock import Mock, call, patch

from rover.transport import udp_control_outage


class TestUdpControlOutage(unittest.TestCase):
    desktop = ("192.168.174.1", 55186)
    vehicle = ("192.168.174.128", 9999)

    def run_relay(self, receives, times, *, trigger_at):
        incoming = iter(receives)
        requests = Queue(maxsize=1)
        sock = Mock()
        log = io.StringIO()

        def receive(_size):
            index, packet = next(incoming)
            if index in trigger_at:
                requests.put_nowait(None)
            if isinstance(packet, BaseException):
                raise packet
            return packet

        sock.recvfrom.side_effect = receive
        with (
            patch.object(udp_control_outage, "monotonic", side_effect=times),
            redirect_stdout(io.StringIO()),
        ):
            udp_control_outage.relay_controls(
                sock, self.desktop[0], self.vehicle, requests, log
            )
        log.seek(0)
        return sock, list(csv.DictReader(log))

    def test_outage_drops_without_buffering_and_forwards_at_exact_deadline(self):
        sock, rows = self.run_relay(
            list(enumerate([
                (b"before", self.desktop),
                (b"first lost", self.desktop),
                (b"last lost", self.desktop),
                (b"after", self.desktop),
                KeyboardInterrupt(),
            ])),
            [100.0, 100.1, 101.0, 102.999, 103.0, 103.1],
            trigger_at={1},
        )

        self.assertEqual(sock.sendto.call_args_list, [
            call(b"before", self.vehicle), call(b"after", self.vehicle),
        ])
        boundaries = [row for row in rows if row["event"].startswith("outage_")]
        self.assertEqual([row["event"] for row in boundaries], ["outage_started", "outage_ended"])
        self.assertEqual([row["elapsed_s"] for row in boundaries], ["1.000000", "3.000000"])
        self.assertEqual(boundaries[-1]["dropped_packets"], "2")

    def test_outage_ends_on_receive_timeout_and_ignores_extra_trigger(self):
        sock, rows = self.run_relay(
            list(enumerate([
                (b"lost", self.desktop), TimeoutError(), TimeoutError(),
                KeyboardInterrupt(),
            ])),
            [50.0, 50.1, 50.2, 52.12, 52.2],
            trigger_at={0, 1},
        )

        sock.sendto.assert_not_called()
        self.assertEqual([row["event"] for row in rows], [
            "relay_started", "outage_started", "packet_dropped", "trigger_ignored",
            "outage_ended", "relay_stopped",
        ])
        self.assertEqual(rows[-2]["dropped_packets"], "1")

    def test_only_the_configured_desktop_can_send_and_early_stop_is_marked(self):
        sock, rows = self.run_relay(
            list(enumerate([
                (b"other", ("192.168.174.50", 55186)),
                (b"lost", self.desktop),
                KeyboardInterrupt(),
            ])),
            [60.0, 60.2, 60.5, 60.6],
            trigger_at={1},
        )
        sock.sendto.assert_not_called()
        self.assertEqual([row["event"] for row in rows], [
            "relay_started", "source_ignored", "outage_started", "packet_dropped",
            "outage_aborted", "relay_stopped",
        ])

    def test_windows_udp_reset_keeps_relay_running_for_the_next_datagram(self):
        reset = ConnectionResetError("destination port unreachable")
        reset.winerror = 10054
        sock, rows = self.run_relay(
            list(enumerate([
                (b"first", self.desktop), reset, (b"next", self.desktop), KeyboardInterrupt(),
            ])),
            [70.0, 70.1, 70.2, 70.3, 70.4],
            trigger_at=set(),
        )

        self.assertEqual(sock.sendto.call_args_list, [
            call(b"first", self.vehicle), call(b"next", self.vehicle),
        ])
        self.assertEqual([row["event"] for row in rows], [
            "relay_started", "packet_forwarded", "destination_unreachable",
            "packet_forwarded", "relay_stopped",
        ])

    def test_cli_rejects_public_host_or_a_relay_loop_before_opening_socket(self):
        for options in (
            ["--desktop-host", "8.8.8.8"],
            ["--desktop-host", "127.0.0.1", "--listen-port", "9999"],
        ):
            with (
                self.subTest(options=options),
                patch.object(udp_control_outage.socket, "socket") as create_socket,
                redirect_stderr(io.StringIO()),
                self.assertRaises(SystemExit) as raised,
            ):
                udp_control_outage.main(["--bind-host", "127.0.0.1", "--event-log", "outage.csv", *options])
            self.assertEqual(raised.exception.code, 2)
            create_socket.assert_not_called()


if __name__ == "__main__":
    unittest.main()
