import socket
import ssl
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from rover.desktop.management_client import ManagementClient


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
    context = ssl.create_default_context(cafile=str(ca_file))
    with socket.create_connection(address, timeout=timeout_s) as raw:
        with context.wrap_socket(
            raw, server_hostname=server_hostname
        ) as tls:
            client = ManagementClient(tls)
            result = client.authenticate(client_id, credential)
            if not result.accepted:
                raise PermissionError("client authentication rejected")
            yield client
