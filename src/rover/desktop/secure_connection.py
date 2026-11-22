import socket
import ssl
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from rover.desktop.expert_management_client import ExpertManagementClient
from rover.desktop.management_client import ManagementClient


@contextmanager
def _verified_tls_socket(
    address: tuple[str, int],
    ca_file: Path,
    server_hostname: str,
    timeout_s: float,
) -> Iterator[ssl.SSLSocket]:
    context = ssl.create_default_context(cafile=str(ca_file))
    with socket.create_connection(address, timeout=timeout_s) as raw:
        with context.wrap_socket(raw, server_hostname=server_hostname) as tls:
            yield tls


@contextmanager
def connect_management(
    address: tuple[str, int],
    *,
    ca_file: Path,
    server_hostname: str,
    client_id: str,
    credential: str,
    timeout_s: float = 3.0,
) -> Iterator[ManagementClient]:
    with _verified_tls_socket(address, ca_file, server_hostname, timeout_s) as tls:
        client = ManagementClient(tls)
        result = client.authenticate(client_id, credential)
        if not result.accepted:
            raise PermissionError("client authentication rejected")
        yield client


@contextmanager
def connect_expert_management(
    address: tuple[str, int],
    *,
    ca_file: Path,
    server_hostname: str,
    client_id: str,
    credential: str,
    timeout_s: float = 3.0,
) -> Iterator[ExpertManagementClient]:
    with _verified_tls_socket(address, ca_file, server_hostname, timeout_s) as tls:
        client = ExpertManagementClient(tls)
        if not client.authenticate(client_id, credential):
            raise PermissionError("client authentication rejected")
        yield client
