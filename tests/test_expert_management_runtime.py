"""Exercise the expert runtime over loopback TCP, with TLS wrapped by a fixture."""

from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
import socket
import ssl
from threading import Event, Thread
from time import monotonic
import unittest
from unittest.mock import Mock, patch

from rover.core.vehicle_model import VehicleState
from rover.desktop.expert_management_client import ExpertManagementClient
from rover.protocol.expert_management_codec import (
    decode_expert_management,
    encode_expert_management,
)
from rover.transport.tcp_transport import iter_records, send_record
from rover.vehicle.control_guard import ControlGuardState
from rover.vehicle.expert_management_runtime import ExpertManagementRuntime, expert_management_listener


class LoopbackTestSocket(socket.socket):
    def do_handshake(self):
        pass


class TestExpertManagementRuntime(unittest.TestCase):
    def test_queued_renewal_uses_processing_time_at_lease_deadline(self):
        runtime = ExpertManagementRuntime({}, operation_timeout_s=1.0)
        guard = ControlGuardState()
        guard, (_, opened) = runtime._dispatch(
            guard, "desktop-a", "connection-a", {"type": "open_session"}, 1.0, 0.0,
        )
        session_id = opened["session_id"]
        guard, (_, acquired) = runtime._dispatch(
            guard, "desktop-a", "connection-a",
            {"type": "acquire_authority", "session_id": session_id}, 1.0, 0.0,
        )
        guard, (_, status) = runtime._dispatch(
            guard, "desktop-a", "connection-a",
            {"type": "query_authority", "session_id": session_id}, 3.0, 0.0,
        )
        self.assertEqual(status["lease_remaining_s"], 1.0)

        def renew(guard, now_s, wait_ms):
            return runtime._dispatch(
                guard, "desktop-a", "connection-a",
                {"type": "renew_authority", "session_id": session_id,
                 "generation": acquired["generation"]}, now_s, wait_ms,
            )

        reply = Future()
        runtime._pending.put_nowait((renew, reply, 3.9))
        with patch("rover.vehicle.expert_management_runtime.monotonic", side_effect=[3.99, 4.0]):
            guard = runtime(guard)

        message_type, result = reply.result(timeout=0)
        self.assertEqual(message_type, "renew_authority_result")
        self.assertFalse(result["accepted"])
        self.assertEqual(result["reason"], "lease_expired")
        self.assertFalse(guard.fault_latched)
        self.assertIsNone(runtime.authority.grant)

    @contextmanager
    def listener(self, *, pump=True, operation_timeout_s=1.0, reported_vehicle=None):
        context = Mock()

        def wrap_socket(raw, **_):
            timeout_s = raw.gettimeout()
            wrapped = LoopbackTestSocket(fileno=raw.detach())
            wrapped.settimeout(timeout_s)
            return wrapped

        context.wrap_socket.side_effect = wrap_socket
        with patch.object(ssl, "SSLContext", return_value=context):
            with expert_management_listener(
                Path("unused-tls-fixture"),
                {"desktop-a": b"demo-credential"},
                ("127.0.0.1", 0),
                operation_timeout_s=operation_timeout_s,
            ) as process_one:
                stop = Event()

                def run_simulation():
                    guard = ControlGuardState()
                    while not stop.is_set():
                        guard = process_one(guard)
                        if reported_vehicle is not None:
                            guard = process_one.observe_vehicle(guard, reported_vehicle())
                        stop.wait(0.002)

                simulation = Thread(target=run_simulation)
                if pump:
                    simulation.start()
                try:
                    yield process_one
                finally:
                    stop.set()
                    if pump:
                        simulation.join()

    def connect(self, address):
        return socket.create_connection(address, timeout=2.0)

    def open(self, stream):
        client = ExpertManagementClient(stream)
        self.assertTrue(client.authenticate("desktop-a", "demo-credential"))
        return client, client.open_session()

    def test_two_connections_have_distinct_sessions_and_independent_cleanup(self):
        with self.listener() as listener:
            with self.connect(listener.address) as first:
                a, session_a = self.open(first)
                with self.connect(listener.address) as second:
                    b, session_b = self.open(second)
                    self.assertNotEqual(session_a, session_b)
                    self.assertEqual(a.open_session(), session_a)
                    for client, session in ((a, session_a), (b, session_b)):
                        status = client.query_authority(session)
                        self.assertEqual(status["online_sessions"], 2)
                        self.assertEqual(status["phase"], "idle")
                        self.assertFalse(status["owns_authority"])
                        self.assertGreaterEqual(status["queue_wait_ms"], 0)
                    with self.assertRaisesRegex(ValueError, "invalid_session"):
                        b.query_authority(session_a)
                    self.assertEqual(b.query_authority(session_b)["online_sessions"], 2)
                    first.shutdown(socket.SHUT_RDWR)
                    deadline = monotonic() + 1.0
                    while True:
                        status = b.query_authority(session_b)
                        if status["online_sessions"] == 1 or monotonic() >= deadline:
                            break
                    self.assertEqual(status["online_sessions"], 1)
                    self.assertEqual(b.open_session(), session_b)

    def test_silent_connection_does_not_block_the_other_connection(self):
        with self.listener() as listener:
            with self.connect(listener.address), self.connect(listener.address) as ready:
                client, session = self.open(ready)
                self.assertEqual(client.query_authority(session)["online_sessions"], 1)

    def wait_for_status(self, client, session, condition):
        deadline = monotonic() + 1.0
        while True:
            status = client.query_authority(session)
            if condition(status):
                return status
            if monotonic() >= deadline:
                self.fail(f"authority status did not reach the expected state: {status}")

    def test_simultaneous_acquisition_grants_only_one_connection(self):
        with (
            self.listener() as listener,
            self.connect(listener.address) as first,
            self.connect(listener.address) as second,
            ThreadPoolExecutor(max_workers=2) as executor,
        ):
            a, session_a = self.open(first)
            b, session_b = self.open(second)
            requested = (
                executor.submit(a.acquire_authority, session_a),
                executor.submit(b.acquire_authority, session_b),
            )
            replies = [request.result(timeout=2.0) for request in requested]
            self.assertEqual(sum(reply["accepted"] for reply in replies), 1)
            self.assertEqual(
                [reply["reason"] for reply in replies if not reply["accepted"]],
                ["authority_busy"],
            )

    def test_handoff_waits_for_vehicle_observation_and_rejects_old_holder(self):
        parked = Event()

        def report():
            return VehicleState(speed_mps=0.0 if parked.is_set() else 1.0)

        with (
            self.listener(reported_vehicle=report) as listener,
            self.connect(listener.address) as first,
            self.connect(listener.address) as second,
        ):
            a, session_a = self.open(first)
            b, session_b = self.open(second)
            old_generation = a.acquire_authority(session_a)["generation"]
            self.assertEqual(
                a.query_authority(session_a)["target_session_ids"], [session_b]
            )
            self.assertTrue(a.transfer_authority(session_a, old_generation, session_b)["accepted"])

            waiting = b.query_authority(session_b)
            self.assertEqual(waiting["phase"], "stopping")
            self.assertFalse(waiting["owns_authority"])
            self.assertNotIn("generation", waiting)
            parked.set()
            acquired = self.wait_for_status(b, session_b, lambda status: status["owns_authority"])
            self.assertEqual(acquired["phase"], "held")
            self.assertNotEqual(acquired["generation"], old_generation)
            denied = a.set_authority_enabled(session_a, old_generation, True)
            self.assertEqual(denied["reason"], "not_authority_holder")
            first.shutdown(socket.SHUT_RDWR)
            still_owned = self.wait_for_status(
                b, session_b, lambda status: status["online_sessions"] == 1
            )
            self.assertTrue(still_owned["owns_authority"])
            self.assertEqual(still_owned["generation"], acquired["generation"])

    def test_recipient_disconnect_before_vehicle_stops_cancels_handoff(self):
        parked = Event()

        def report():
            return VehicleState(speed_mps=0.0 if parked.is_set() else 1.0)

        with (
            self.listener(reported_vehicle=report) as listener,
            self.connect(listener.address) as first,
            self.connect(listener.address) as second,
        ):
            a, session_a = self.open(first)
            _, session_b = self.open(second)
            generation = a.acquire_authority(session_a)["generation"]
            self.assertTrue(a.transfer_authority(session_a, generation, session_b)["accepted"])
            second.shutdown(socket.SHUT_RDWR)
            still_stopping = self.wait_for_status(
                a, session_a, lambda status: status["online_sessions"] == 1
            )
            self.assertEqual(still_stopping["phase"], "stopping")
            parked.set()
            idle = self.wait_for_status(a, session_a, lambda status: status["phase"] == "idle")
            self.assertFalse(idle["owns_authority"])

    def test_duplicate_request_is_rejected_and_connection_stays_usable(self):
        with self.listener() as listener, self.connect(listener.address) as stream:
            replies = iter_records(stream)
            send_record(stream, encode_expert_management(
                "authenticate", 1, client_id="desktop-a", credential="demo-credential"
            ))
            self.assertTrue(decode_expert_management(next(replies))["accepted"])
            send_record(stream, encode_expert_management("open_session", 2))
            session_id = decode_expert_management(next(replies))["session_id"]

            send_record(stream, encode_expert_management("open_session", 2))
            reply = decode_expert_management(next(replies))
            self.assertEqual(reply["request_id"], 2)
            self.assertEqual(reply["reason"], "duplicate_request_id")

            send_record(stream, encode_expert_management("query_authority", 3, session_id=session_id))
            reply = decode_expert_management(next(replies))
            self.assertEqual(reply["request_id"], 3)
            self.assertEqual(reply["online_sessions"], 1)

    def test_timed_out_open_does_not_leave_a_session_after_the_loop_resumes(self):
        with self.listener(pump=False, operation_timeout_s=0.05) as listener:
            with self.connect(listener.address) as raw:
                client = ExpertManagementClient(raw)
                self.assertTrue(client.authenticate("desktop-a", "demo-credential"))
                for _ in range(2):
                    with self.assertRaisesRegex(ValueError, "simulation_timeout"):
                        client.open_session()
                with self.assertRaisesRegex(ValueError, "queue_full"):
                    client.open_session()
                guard = ControlGuardState()
                self.assertIs(listener(guard), guard)
                self.assertIs(listener(guard), guard)

                complete = Event()
                result = []

                def open_and_query():
                    try:
                        result.append(client.query_authority(client.open_session()))
                    except Exception as error:
                        result.append(error)
                    finally:
                        complete.set()

                worker = Thread(target=open_and_query)
                worker.start()
                try:
                    deadline = monotonic() + 2.0
                    while not complete.is_set() and monotonic() < deadline:
                        guard = listener(guard)
                        complete.wait(0.002)
                    self.assertTrue(complete.is_set())
                    self.assertIsInstance(result[0], dict)
                    self.assertEqual(result[0]["online_sessions"], 1)
                finally:
                    worker.join(timeout=2.0)
