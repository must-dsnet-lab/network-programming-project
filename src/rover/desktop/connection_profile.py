"""Validate and persist the desktop's non-secret connection settings."""

import json
import os
import re
from dataclasses import dataclass
from ipaddress import IPv4Address
from pathlib import Path
from tempfile import NamedTemporaryFile


_PROFILE_VERSION = 1
_PROFILE_FIELDS = (
    "mode", "vehicle_host", "management_port", "control_port", "telemetry_bind_host",
    "telemetry_port", "server_name", "ca_file", "client_id",
)
_DNS_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")


def _network_name(value: object, label: str, *, allow_empty: bool = False) -> str:
    if isinstance(value, str):
        name = value.strip()
        if not name and allow_empty:
            return ""
        try:
            return str(IPv4Address(name))
        except ValueError:
            pass

        try:
            hostname = name.encode("idna").decode("ascii").lower().removesuffix(".")
        except UnicodeError:
            hostname = ""
        if (len(hostname) <= 253 and re.fullmatch(r"[0-9.]+", hostname) is None
                and all(_DNS_LABEL.fullmatch(part) for part in hostname.split("."))):
            return hostname
    raise ValueError(f"{label}需要 IPv4 地址或有效主机名")


@dataclass(frozen=True)
class DesktopConnectionSettings:
    mode: str = "advanced"
    vehicle_host: str = "127.0.0.1"
    management_port: int = 19177
    control_port: int = 9999
    telemetry_bind_host: str = "127.0.0.1"
    telemetry_port: int = 10000
    server_name: str = ""
    ca_file: Path = Path("local/certs/ca.pem")
    client_id: str = "desktop-a"

    def __post_init__(self) -> None:
        if self.mode not in ("advanced", "expert"):
            raise ValueError("请选择高级或专家模式")
        for port, label in (
            (self.management_port, "管理端口"),
            (self.control_port, "控制端口"),
            (self.telemetry_port, "遥测端口"),
        ):
            if type(port) is not int or not 1 <= port <= 65535:
                raise ValueError(f"{label}需要 1–65535 的整数")

        vehicle_host = _network_name(self.vehicle_host, "车端地址")
        if vehicle_host == "0.0.0.0":
            raise ValueError("车端地址需要具体的 IPv4 地址或主机名")
        telemetry_host = _network_name(self.telemetry_bind_host, "遥测监听地址")
        server_name = _network_name(self.server_name, "证书服务器名称", allow_empty=True)
        client_id = self.client_id.strip() if isinstance(self.client_id, str) else ""
        if not client_id or not client_id.isprintable():
            raise ValueError("桌面账号需要可见字符")
        if not isinstance(self.ca_file, Path) or "\x00" in str(self.ca_file):
            raise ValueError("CA 证书需要有效的文件路径")
        try:
            ca_file = self.ca_file.expanduser()
        except RuntimeError as error:
            raise ValueError("CA 证书路径中的用户目录无效") from error

        object.__setattr__(self, "vehicle_host", vehicle_host)
        object.__setattr__(self, "telemetry_bind_host", telemetry_host)
        object.__setattr__(self, "server_name", server_name)
        object.__setattr__(self, "ca_file", ca_file)
        object.__setattr__(self, "client_id", client_id)

    @property
    def management_address(self) -> tuple[str, int]:
        return self.vehicle_host, self.management_port

    @property
    def control_address(self) -> tuple[str, int]:
        return self.vehicle_host, self.control_port

    @property
    def telemetry_address(self) -> tuple[str, int]:
        return self.telemetry_bind_host, self.telemetry_port

    @property
    def tls_server_name(self) -> str:
        default_name = "localhost" if self.vehicle_host == "127.0.0.1" else self.vehicle_host
        return self.server_name or default_name


def _unique_fields(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("连接配置含有重复字段")
        result[key] = value
    return result


def load_desktop_profile(path: Path) -> DesktopConnectionSettings | None:
    """Load the saved version-1 settings, returning None for a missing file."""
    try:
        with path.open(encoding="utf-8-sig") as stream:
            document = json.load(stream, object_pairs_hook=_unique_fields)
    except FileNotFoundError:
        return None
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise ValueError("连接配置必须是 UTF-8 JSON") from error

    if not isinstance(document, dict) or document.keys() != {*_PROFILE_FIELDS, "version"}:
        raise ValueError("连接配置字段不匹配")
    version = document.pop("version")
    if type(version) is not int or version != _PROFILE_VERSION:
        raise ValueError("连接配置版本不受支持")

    ca_file = document["ca_file"]
    if not isinstance(ca_file, str) or not ca_file.strip():
        raise ValueError("连接配置缺少 CA 证书路径")
    document["ca_file"] = Path(ca_file)
    return DesktopConnectionSettings(**document)


def save_desktop_profile(path: Path, settings: DesktopConnectionSettings) -> None:
    """Atomically replace the profile with an explicit set of non-secret fields."""
    document = {"version": _PROFILE_VERSION}
    document.update((name, getattr(settings, name)) for name in _PROFILE_FIELDS)
    document["ca_file"] = str(settings.ca_file.resolve())

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False)
    pending_path = Path(temporary.name)
    try:
        with temporary:
            json.dump(document, temporary, ensure_ascii=False, indent=2)
            temporary.write("\n")
            temporary.flush()
            os.fsync(temporary.fileno())
        pending_path.replace(path)
    finally:
        pending_path.unlink(missing_ok=True)
