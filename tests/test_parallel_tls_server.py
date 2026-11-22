import socket
import unittest
from pathlib import Path
from queue import Empty, Queue
from threading import Event
from unittest.mock import patch

from rover.transport.parallel_tls_server import parallel_tls_server


class MemorySocket:
    def __init__(self, incoming, outgoing):
        self.incoming = incoming
        self.outgoing = outgoing
        self.closed = Event()

    def settimeout(self, _timeout):
        pass

    def recv(self, _size):
        try:
            return self.incoming.get(timeout=2.0)
        except Empty:
            raise TimeoutError("memory socket receive timed out") from None

    def sendall(self, payload):
        for byte in payload:
            self.outgoing.put(bytes((byte,)))

    def shutdown(self, _how):
        self.incoming.put(b"")
        self.outgoing.put(b"")

    def close(self):
        if not self.closed.is_set():
            self.shutdown(socket.SHUT_RDWR)
            self.closed.set()


class MemoryListener:
    address = ("127.0.0.1", 8001)

    def __init__(self):
        self.incoming = Queue()

    def bind(self, _address):
        pass

    def listen(self, _backlog):
        pass

    def settimeout(self, timeout):
        self.timeout = timeout

    def getsockname(self):
        return self.address

    def accept(self):
        try:
            client = self.incoming.get(timeout=self.timeout)
        except Empty:
            raise TimeoutError from None
        if client is None:
            raise OSError("memory listener closed")
        return client, self.address

    def close(self):
        self.incoming.put(None)

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()


class TestParallelTlsServer(unittest.TestCase):
    def setUp(self):
        self.handshake_started = Event()
        self.first_handshake_finished = Event()
        self.first_handshake_saw_eof = Event()
        self.cert_dir = Path("certs")
        self.listener = MemoryListener()
        socket_factory = self.enterContext(patch("rover.transport.parallel_tls_server.socket.socket"))
        socket_factory.return_value = self.listener
        socket_factory.shutdown.side_effect = lambda client, how: client.shutdown(how)
        ssl_context = self.enterContext(patch("rover.transport.parallel_tls_server.ssl.SSLContext"))
        ssl_context.return_value.wrap_socket.side_effect = self.wrap_socket

    def wrap_socket(self, raw, *, server_side, do_handshake_on_connect):
        # Control the handshake on otherwise independent, full-duplex test channels.
        self.assertTrue(server_side)
        self.assertFalse(do_handshake_on_connect)
        test = self

        class TestTlsSocket:
            def do_handshake(self):
                first = not test.handshake_started.is_set()
                test.handshake_started.set()
                try:
                    marker = raw.recv(1)
                    if first and not marker:
                        test.first_handshake_saw_eof.set()
                    if marker != b"H":
                        raise OSError("test connection ended during handshake")
                finally:
                    if first:
                        test.first_handshake_finished.set()

            def __getattr__(self, name):
                return getattr(raw, name)

        return TestTlsSocket()

    def connect(self, address):
        self.assertEqual(address, self.listener.address)
        to_server, to_client = Queue(), Queue()
        client = MemorySocket(to_client, to_server)
        server = MemorySocket(to_server, to_client)
        client.server = server
        self.listener.incoming.put(server)
        self.addCleanup(client.close)
        return client

    def test_slow_handshake_leaves_second_lane_available(self):
        def handle_connection(tls):
            tls.sendall(tls.recv(1))

        with parallel_tls_server(self.cert_dir, ("127.0.0.1", 0), handle_connection) as address:
            self.assertGreater(address[1], 0)
            first = self.connect(address)
            self.assertTrue(self.handshake_started.wait(2.0))
            second = self.connect(address)
            second.sendall(b"HB")
            self.assertEqual(second.recv(1), b"B")
            self.assertFalse(self.first_handshake_finished.is_set())
            first.sendall(b"HA")
            self.assertEqual(first.recv(1), b"A")

    def test_when_full_third_client_does_not_take_an_active_lane(self):
        def handle_connection(tls):
            while payload := tls.recv(1):
                tls.sendall(payload)

        with parallel_tls_server(self.cert_dir, ("127.0.0.1", 0), handle_connection) as address:
            first = self.connect(address)
            second = self.connect(address)
            for client in (first, second):
                client.sendall(b"HX")
                self.assertEqual(client.recv(1), b"X")
            third = self.connect(address)
            self.assert_peer_closed(third)
            for client in (first, second):
                client.sendall(b"X")
                self.assertEqual(client.recv(1), b"X")

            first.close()
            self.assertTrue(first.server.closed.wait(2.0))
            replacement = self.connect(address)
            replacement.sendall(b"HN")
            self.assertEqual(replacement.recv(1), b"N")

    def test_context_exit_interrupts_handshake_and_active_read(self):
        handler_stopped = Event()

        def handle_connection(tls):
            tls.sendall(b"R")
            if tls.recv(1) == b"":
                handler_stopped.set()

        with (
            self.assertNoLogs("rover.transport.parallel_tls_server", level="WARNING"),
            parallel_tls_server(self.cert_dir, ("127.0.0.1", 0), handle_connection) as address,
        ):
            first = self.connect(address)
            self.assertTrue(self.handshake_started.wait(2.0))
            second = self.connect(address)
            second.sendall(b"H")
            self.assertEqual(second.recv(1), b"R")

        self.assertTrue(self.first_handshake_saw_eof.is_set())
        self.assertTrue(handler_stopped.is_set())
        self.assert_peer_closed(first)
        self.assert_peer_closed(second)

    def test_connection_errors_log_fixed_text_and_leave_other_lane_available(self):
        def handle_connection(tls):
            match tls.recv(1):
                case b"T":
                    raise TimeoutError("example-sensitive-input")
                case b"V":
                    raise ValueError("example-sensitive-input")
                case payload:
                    tls.sendall(payload)

        logger = "rover.transport.parallel_tls_server"
        with self.assertLogs(logger, level="WARNING") as captured:
            with parallel_tls_server(self.cert_dir, ("127.0.0.1", 0), handle_connection) as address:
                for command in (b"T", b"V"):
                    client = self.connect(address)
                    client.sendall(b"H" + command)
                    self.assert_peer_closed(client)
                    self.assertTrue(client.server.closed.wait(2.0))
                other = self.connect(address)
                other.sendall(b"HE")
                self.assertEqual(other.recv(1), b"E")

        self.assertEqual([record.getMessage() for record in captured.records], [
            "TLS management connection timed out",
            "TLS management connection ended after an invalid message",
        ])

    def assert_peer_closed(self, client):
        self.assertEqual(client.recv(1), b"")
