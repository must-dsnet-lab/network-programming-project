import socket
import ssl
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

from rover.desktop.expert_management_client import ExpertManagementClient
from rover.desktop.management_client import ManagementClient

if TYPE_CHECKING:
    from rover.desktop.connection_profile import DesktopConnectionSettings


class AuthenticationRejected(PermissionError):
    """The vehicle sent an explicit rejection of the user's credentials."""


class _CertificateLoadError(ssl.SSLError):
    """OpenSSL failed to load the configured local CA."""


class ConnectionSetupError(ConnectionError):
    """A connection error that the desktop setup screen can show to the user."""

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind


@contextmanager
def _verified_tls_socket(
    address: tuple[str, int],
    ca_file: Path,
    server_hostname: str,
    timeout_s: float,
) -> Iterator[ssl.SSLSocket]:
    try:
        context = ssl.create_default_context(cafile=str(ca_file))
    except ssl.SSLError as error:
        raise _CertificateLoadError(*error.args) from error
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
            raise AuthenticationRejected("client authentication rejected")
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
            raise AuthenticationRejected("client authentication rejected")
        yield client


@contextmanager
def _connection_setup_errors() -> Iterator[None]:
    try:
        yield
    except AuthenticationRejected:
        raise ConnectionSetupError("authentication", "认证被拒绝。请核对账号和凭据。") from None
    except (ssl.SSLCertVerificationError, _CertificateLoadError, FileNotFoundError):
        raise ConnectionSetupError("certificate", "证书校验失败。请核对 CA 文件和车端证书名称。") from None
    except ssl.SSLError:
        raise ConnectionSetupError("protocol", "TLS 握手失败。请核对车端是否在该管理端口运行安全模式。") from None
    except OSError:
        raise ConnectionSetupError("network", "无法连接车端。请检查网络、车端地址和管理端口。") from None
    except ValueError:
        raise ConnectionSetupError("protocol", "管理协议响应无效。请核对车端连接模式。") from None


@contextmanager
def connect_configured_management(
    settings: "DesktopConnectionSettings", credential: str,
) -> Iterator[ManagementClient | ExpertManagementClient]:
    """Label setup failures without changing exceptions raised during a session."""
    connect = connect_management if settings.mode == "advanced" else connect_expert_management
    with ExitStack() as stack:
        with _connection_setup_errors():
            client = stack.enter_context(connect(
                settings.management_address,
                ca_file=settings.ca_file,
                server_hostname=settings.tls_server_name,
                client_id=settings.client_id,
                credential=credential,
            ))
        yield client


def register_configured_account(settings: "DesktopConnectionSettings", credential: str) -> str | None:
    if settings.mode != "product":
        return "registration_unavailable"
    with _connection_setup_errors():
        with _verified_tls_socket(settings.management_address, settings.ca_file, settings.tls_server_name, 3.0) as tls:
            return ExpertManagementClient(tls).register_account(settings.client_id, credential)
