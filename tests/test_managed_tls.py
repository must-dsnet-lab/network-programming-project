import ssl
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from rover.vehicle.managed_tls import ensure_vehicle_certificate


def verify_tls(cert_dir: Path, ca_file: Path, host: str) -> None:
    server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_context.load_cert_chain(cert_dir / "server.pem", cert_dir / "server.key")
    client_context = ssl.create_default_context(cafile=ca_file)
    server_input, server_output = ssl.MemoryBIO(), ssl.MemoryBIO()
    client_input, client_output = ssl.MemoryBIO(), ssl.MemoryBIO()
    server = server_context.wrap_bio(server_input, server_output, server_side=True)
    client = client_context.wrap_bio(client_input, client_output, server_hostname=host)
    for _ in range(10):
        finished = []
        for endpoint, outgoing, peer_input in (
            (client, client_output, server_input),
            (server, server_output, client_input),
        ):
            try:
                endpoint.do_handshake()
            except ssl.SSLWantReadError:
                pass
            else:
                finished.append(endpoint)
            if outgoing.pending:
                peer_input.write(outgoing.read())
        if len(finished) == 2:
            return
    raise AssertionError("TLS handshake did not complete")


class TestManagedTLS(unittest.TestCase):
    def test_changed_address_reuses_ca_and_existing_address_reuses_leaf(self):
        with tempfile.TemporaryDirectory() as temp:
            store = Path(temp) / "tls"
            first = ensure_vehicle_certificate(store, "192.0.2.10")
            original_ca = first / "ca.pem"
            second = ensure_vehicle_certificate(store, "192.0.2.11")

            self.assertNotEqual(first, second)
            self.assertEqual(original_ca.read_bytes(), (second / "ca.pem").read_bytes())
            self.assertEqual(second, ensure_vehicle_certificate(store, "192.0.2.11"))
            verify_tls(first, original_ca, "192.0.2.10")
            verify_tls(second, original_ca, "192.0.2.11")
            with self.assertRaises(ssl.SSLCertVerificationError):
                verify_tls(second, original_ca, "192.0.2.10")

    def test_bad_address_does_not_create_store(self):
        with tempfile.TemporaryDirectory() as temp:
            store = Path(temp) / "tls"
            with self.assertRaises(ValueError):
                ensure_vehicle_certificate(store, "0.0.0.0")
            self.assertFalse(store.exists())

    def test_damaged_ca_is_reported_without_replacing_trust(self):
        with tempfile.TemporaryDirectory() as temp:
            store = Path(temp) / "tls"
            cert_dir = ensure_vehicle_certificate(store, "192.0.2.10")
            saved_ca = cert_dir.joinpath("ca.pem").read_bytes()
            store.joinpath("ca", "ca.key").unlink()

            with self.assertRaises(ValueError):
                ensure_vehicle_certificate(store, "192.0.2.11")
            self.assertEqual(saved_ca, store.joinpath("ca", "ca.pem").read_bytes())
            verify_tls(cert_dir, cert_dir / "ca.pem", "192.0.2.10")

    def test_concurrent_first_use_keeps_one_ca(self):
        with tempfile.TemporaryDirectory() as temp, ThreadPoolExecutor(2) as threads:
            store = Path(temp) / "tls"
            addresses = ["192.0.2.10", "192.0.2.11"]
            first, second = threads.map(
                lambda ip: ensure_vehicle_certificate(store, ip), addresses,
            )
            self.assertEqual((first / "ca.pem").read_bytes(), (second / "ca.pem").read_bytes())
            verify_tls(first, second / "ca.pem", addresses[0])
            verify_tls(second, first / "ca.pem", addresses[1])
