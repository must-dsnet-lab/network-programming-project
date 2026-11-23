"""Read and write a portable user-edition endpoint and one public CA."""

import json
import re
import ssl
from dataclasses import dataclass
from hashlib import sha256
from ipaddress import IPv4Address
from pathlib import Path
from tempfile import NamedTemporaryFile


_MAX_BYTES = 32768
_FIELDS = {"version", "vehicle_host", "management_port", "control_port", "server_name", "ca_pem"}
_PEM_CERTIFICATE = re.compile(r"-----BEGIN CERTIFICATE-----[\r\n]+[A-Za-z0-9+/=\r\n]+-----END CERTIFICATE-----")
_DNS_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")


@dataclass(frozen=True)
class ConnectionBundle:
    vehicle_host: str
    management_port: int
    control_port: int
    server_name: str
    ca_pem: str
    ca_sha256: str


def _read_limited(path: Path) -> bytes:
    with path.open("rb") as stream:
        data = stream.read(_MAX_BYTES + 1)
    if len(data) > _MAX_BYTES:
        raise ValueError("连接包或公开 CA 文件过大。")
    return data


def _public_ca(pem: object) -> tuple[str, str]:
    if not isinstance(pem, str) or not _PEM_CERTIFICATE.fullmatch(pem.strip()):
        raise ValueError("连接包必须包含一张公开 PEM CA 证书。")
    pem = pem.strip()
    try:
        der = ssl.PEM_cert_to_DER_cert(pem)
        trust = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        trust.load_verify_locations(cadata=pem)
    except (ValueError, ssl.SSLError) as error:
        raise ValueError("连接包中的公开 CA 无法读取。") from error
    if trust.get_ca_certs(binary_form=True) != [der]:
        raise ValueError("连接包需要有效的公开 CA 证书。")
    return ssl.DER_cert_to_PEM_cert(der), sha256(der).hexdigest()


def ca_certificate_fingerprint(path: Path) -> str:
    """Hash the public certificate in DER form, independent of PEM formatting."""
    return _public_ca(_read_limited(path).decode("ascii"))[1]


def _bundle_from_fields(document: dict) -> ConnectionBundle:
    if type(document["version"]) is not int or document["version"] != 1:
        raise ValueError("连接包版本不受支持。")
    try:
        if not isinstance(document["vehicle_host"], str):
            raise ValueError
        host = IPv4Address(document["vehicle_host"])
    except ValueError as error:
        raise ValueError("连接包需要明确的车端 IPv4 地址。") from error
    if host.is_loopback or host.is_unspecified or host.is_multicast or host.is_reserved:
        raise ValueError("连接包需要供其他电脑访问的车端 IPv4 地址。")
    management_port = document["management_port"]
    control_port = document["control_port"]
    if any(type(port) is not int or not 1 <= port <= 65535 for port in (management_port, control_port)):
        raise ValueError("连接包中的端口需要 1–65535 的整数。")
    name = document["server_name"]
    if not isinstance(name, str):
        raise ValueError("连接包中的证书名称无效。")
    if name:
        try:
            name = str(IPv4Address(name))
        except ValueError:
            name = name.encode("idna").decode("ascii").lower().removesuffix(".")
            if (
                len(name) > 253 or re.fullmatch(r"[0-9.]+", name)
                or not all(_DNS_LABEL.fullmatch(part) for part in name.split("."))
            ):
                raise ValueError("连接包中的证书名称无效。") from None
    pem, digest = _public_ca(document["ca_pem"])
    return ConnectionBundle(str(host), management_port, control_port, name, pem, digest)


def _unique_fields(pairs):
    document = {}
    for key, value in pairs:
        if key in document:
            raise ValueError("连接包含有重复字段。")
        document[key] = value
    return document


def read_connection_bundle(path: Path) -> ConnectionBundle:
    try:
        document = json.loads(_read_limited(path).decode("utf-8"), object_pairs_hook=_unique_fields)
    except (json.JSONDecodeError, UnicodeDecodeError, RecursionError) as error:
        raise ValueError("连接包必须是 UTF-8 JSON。") from error
    if not isinstance(document, dict) or document.keys() != _FIELDS:
        raise ValueError("连接包字段不匹配。")
    return _bundle_from_fields(document)


def write_connection_bundle(
    path: Path, *, vehicle_host: str, management_port: int, control_port: int,
    ca_file: Path, server_name: str = "",
) -> None:
    document = dict(
        version=1, vehicle_host=vehicle_host, management_port=management_port,
        control_port=control_port, server_name=server_name, ca_pem=_read_limited(ca_file).decode("ascii"),
    )
    bundle = _bundle_from_fields(document)
    document.update(vehicle_host=bundle.vehicle_host, server_name=bundle.server_name, ca_pem=bundle.ca_pem)
    data = (json.dumps(document, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    if len(data) > _MAX_BYTES:
        raise ValueError("连接包文件过大。")
    temporary = NamedTemporaryFile(dir=path.parent, delete=False)
    pending_path = Path(temporary.name)
    try:
        with temporary:
            temporary.write(data)
        pending_path.replace(path)
    finally:
        pending_path.unlink(missing_ok=True)
