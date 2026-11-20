"""Runtime handoff tests: real TCP and worker; SSL/auth/framing are replaced.

These tests do not exercise TLS encryption or certificate verification.
"""

from concurrent.futures import Future
from contextlib import ExitStack, nullcontext
from pathlib import Path
import socket
from threading import Event, get_ident
from time import monotonic
import unittest
from unittest.mock import Mock, patch

from rover.protocol.management_codec import (
    EmergencyStopRequest,
    EnableSessionRequest,
    IssueControlWindowRequest,
    OpenSessionRequest,
    ResetControlRequest,
)
from rover.vehicle import management_runtime as runtime
from rover.vehicle import management_session as session
from rover.vehicle.control_guard import ControlGuardState
from rover.vehicle.control_window import ControlWindow


class TestManagementRuntime(unittest.TestCase):
    def run_script(self, guard, script):
        """Pump on the test thread; callbacks run on the runtime's real worker."""
        outcome, done, sockets = Future(), Event(), []
        real_socket, main_thread = socket.socket, get_ident()

        def make_socket(*args, **kwargs):
            sock = real_socket(*args, **kwargs)
            sockets.append(sock)
            return sock

        def serve(_stream, trusted, open_session, close_connection, **callbacks):
            try:
                self.assertNotEqual(get_ident(), main_thread)
                self.assertEqual(trusted, {"client": b"test-secret"})
                script(open_session, close_connection, **callbacks)
            except BaseException as error:
                outcome.set_exception(error)
            else:
                outcome.set_result(None)
            finally:
                done.set()

        context = Mock()
        context.wrap_socket.side_effect = lambda raw, **_: nullcontext(raw)
        with ExitStack() as stack:
            stack.enter_context(patch.object(runtime.socket, "socket", make_socket))
            stack.enter_context(
                patch.object(runtime.ssl, "SSLContext", return_value=context)
            )
            stack.enter_context(
                patch.object(runtime, "serve_management_connection", serve)
            )
            stack.enter_context(
                patch.object(runtime, "monotonic", return_value=100.0, create=True)
            )
            for name in (
                "handle_open_session",
                "handle_enable_session",
                "handle_issue_control_window",
                "handle_emergency_stop",
                "handle_reset_control",
                "handle_connection_closed",
            ):
                handler = getattr(session, name)

                def checked(*args, _handler=handler, **kwargs):
                    self.assertEqual(get_ident(), main_thread)
                    return _handler(*args, **kwargs)

                stack.enter_context(patch.object(runtime, name, checked, create=True))

            with runtime.management_listener(
                Path("unused-test-certificates"),
                {"client": b"test-secret"},
                address=("127.0.0.1", 0),
            ) as process_one:
                self.assertTrue(sockets, "management_listener did not start a socket")
                self.assertIs(process_one(guard), guard)
                with socket.create_connection(sockets[0].getsockname(), timeout=2.0):
                    deadline = monotonic() + 3.0
                    while not done.is_set() and monotonic() < deadline:
                        guard = process_one(guard)
                        done.wait(0.001)
                    self.assertTrue(
                        done.is_set(), "queued callback did not complete in time"
                    )
                    outcome.result()
        return guard

    def test_lifecycle_hands_every_operation_and_reply_between_threads(self):
        def script(open_session, _close, **callbacks):
            connection, opened = open_session(
                session.AuthenticatedConnection("client"),
                OpenSessionRequest(1),
            )
            self.assertEqual(opened.request_id, 1)
            self.assertEqual(connection.session_id, opened.session_id)
            self.assertTrue(
                callbacks["enable_session"](
                    connection,
                    EnableSessionRequest(2, opened.session_id),
                )
            )
            window = callbacks["issue_control_window"](
                connection,
                IssueControlWindowRequest(3, opened.session_id),
            )
            self.assertTrue(window.granted)
            stopped = callbacks["emergency_stop"](
                connection,
                EmergencyStopRequest(4, opened.session_id),
            )
            self.assertTrue(stopped.accepted)
            connection, reset = callbacks["reset_control"](
                connection,
                ResetControlRequest(5, opened.session_id),
            )
            self.assertTrue(reset.accepted)
            self.assertIsNone(connection.session_id)

        self.assertEqual(
            self.run_script(ControlGuardState(), script), ControlGuardState()
        )

    def test_operation_exception_preserves_guard_and_worker_accepts_next_callback(self):
        guard = ControlGuardState(session_id="owner", control_key=b"k" * 32)

        def script(open_session, close_connection, **_callbacks):
            other = session.AuthenticatedConnection("other")
            with self.assertRaisesRegex(ValueError, "another control session"):
                open_session(other, OpenSessionRequest(1))
            close_connection(other)

        self.assertIs(self.run_script(guard, script), guard)

    def test_owner_disconnect_revokes_binding_and_control_material(self):
        guard = ControlGuardState(
            session_id="owner",
            enabled=True,
            enabled_at_s=100.0,
            last_seq=7,
            control_key=b"k" * 32,
            control_window=ControlWindow("window", 100.0, 100.25),
        )

        def script(_open, close_connection, **_callbacks):
            close_connection(session.AuthenticatedConnection("client", "owner"))

        self.assertEqual(self.run_script(guard, script), ControlGuardState())
