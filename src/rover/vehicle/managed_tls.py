"""Keep deployment trust stable while issuing server certificates for new addresses."""

import os
import shutil
import ssl
import subprocess
import sys
from ipaddress import IPv4Address
from pathlib import Path
from secrets import token_hex
from tempfile import TemporaryDirectory


_CA_CONFIG = """[req]
prompt = no
distinguished_name = name
[name]
CN = Rover Local Deployment CA
[ca]
basicConstraints = critical,CA:TRUE,pathlen:0
keyUsage = critical,keyCertSign,cRLSign
subjectKeyIdentifier = hash
"""

_SERVER_CONFIG = """[req]
prompt = no
distinguished_name = name
[name]
CN = Rover Vehicle
[server]
basicConstraints = critical,CA:FALSE
keyUsage = critical,digitalSignature,keyEncipherment
extendedKeyUsage = serverAuth
authorityKeyIdentifier = keyid,issuer
subjectKeyIdentifier = hash
"""


def _openssl_path() -> Path:
    bundle = getattr(sys, "_MEIPASS", None)
    if bundle is not None:
        bundled = Path(bundle) / "rover-openssl" / "openssl.exe"
        if bundled.is_file():
            return bundled
    configured = os.environ.get("ROVER_OPENSSL")
    if configured:
        path = Path(configured)
        if path.is_absolute() and path.is_file():
            return path
        raise ValueError("ROVER_OPENSSL 请填写 OpenSSL 可执行文件的绝对路径。")
    executable = shutil.which("openssl")
    if executable:
        return Path(executable).resolve()
    if os.name == "nt":
        git = shutil.which("git")
        roots = list(Path(git).resolve().parents) if git else []
        program_files = os.environ.get("ProgramFiles")
        if program_files:
            roots.append(Path(program_files) / "Git")
        for root in roots:
            path = root / "mingw64" / "bin" / "openssl.exe"
            if path.is_file():
                return path
    raise ValueError("找不到 OpenSSL 3。请使用完整车端管理包，或配置 ROVER_OPENSSL。")


def _openssl(executable: Path, *args: str | Path, check: bool = True) -> subprocess.CompletedProcess:
    try:
        result = subprocess.run(
            [str(executable), *map(str, args)],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=30,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ValueError("无法完成 OpenSSL 证书操作。") from error
    if check and result.returncode:
        detail = result.stderr.decode(errors="replace").strip().splitlines()
        raise ValueError("OpenSSL 证书操作失败。" + (" " + detail[-1] if detail else ""))
    return result


def _load_or_create_ca(executable: Path, store: Path) -> tuple[Path, Path]:
    ca_dir = store / "ca"
    if not ca_dir.exists():
        with TemporaryDirectory(prefix=".ca-", dir=store) as scratch_name:
            scratch = Path(scratch_name)
            draft = scratch / "ca"
            draft.mkdir(mode=0o700)
            config = scratch / "openssl.cnf"
            config.write_text(_CA_CONFIG, encoding="ascii")
            _openssl(
                executable, "req", "-x509", "-newkey", "rsa:2048", "-noenc",
                "-sha256", "-days", "365", "-config", config, "-extensions", "ca",
                "-keyout", draft / "ca.key", "-out", draft / "ca.pem",
            )
            try:
                draft.rename(ca_dir)
            except OSError:
                if not ca_dir.is_dir():
                    raise
                # A concurrent first-time setup may already have published the CA.
    ca_file, ca_key = ca_dir / "ca.pem", ca_dir / "ca.key"
    if not ca_file.is_file() or not ca_key.is_file():
        raise ValueError("部署 CA 文件不完整。请恢复原 CA 证书及签发密钥。")
    _openssl(
        executable, "verify", "-check_ss_sig", "-no-CApath", "-no-CAstore",
        "-CAfile", ca_file, ca_file,
    )
    certificate_public_key = _openssl(executable, "x509", "-in", ca_file, "-noout", "-pubkey")
    signing_public_key = _openssl(executable, "pkey", "-in", ca_key, "-pubout")
    if certificate_public_key.stdout != signing_public_key.stdout:
        raise ValueError("部署 CA 证书与签发密钥不匹配。")
    return ca_file, ca_key


def _valid_server_pair(executable: Path, directory: Path, ca_file: Path, address: str) -> bool:
    server_file, server_key = directory / "server.pem", directory / "server.key"
    public_ca = directory / "ca.pem"
    if not all(path.is_file() for path in (server_file, server_key, public_ca)):
        return False
    if public_ca.read_bytes() != ca_file.read_bytes():
        return False
    try:
        ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER).load_cert_chain(server_file, server_key)
    except (OSError, ValueError):
        return False
    return _openssl(
        executable, "verify", "-no-CApath", "-no-CAstore", "-CAfile", ca_file, "-purpose", "sslserver",
        "-verify_ip", address, server_file, check=False,
    ).returncode == 0


def ensure_vehicle_certificate(store_dir: Path, vehicle_ipv4: str) -> Path:
    """Return a verified certificate directory without replacing a deployed CA."""
    address = IPv4Address(vehicle_ipv4)
    if address.is_unspecified or address.is_multicast or address.is_reserved or address.is_link_local:
        raise ValueError("请选择有效的本机 IPv4 地址。")
    executable = _openssl_path()
    store = store_dir.resolve()
    store.mkdir(mode=0o700, parents=True, exist_ok=True)
    ca_file, ca_key = _load_or_create_ca(executable, store)
    servers = store / "servers" / str(address)
    servers.mkdir(mode=0o700, parents=True, exist_ok=True)
    for directory in servers.iterdir():
        if directory.is_dir() and _valid_server_pair(executable, directory, ca_file, str(address)):
            return directory

    for days in (90, 30, 7, 1):
        if _openssl(
            executable, "x509", "-in", ca_file, "-noout", "-checkend",
            str(days * 86400), check=False,
        ).returncode == 0:
            break
    else:
        raise ValueError("部署 CA 即将过期。请安排 CA 更新后再签发服务器证书。")

    with TemporaryDirectory(prefix=".server-", dir=store) as scratch_name:
        scratch = Path(scratch_name)
        draft = scratch / "certificate"
        draft.mkdir(mode=0o700)
        config = scratch / "openssl.cnf"
        san = "DNS:localhost,IP:127.0.0.1"
        if str(address) != "127.0.0.1":
            san += f",IP:{address}"
        config.write_text(_SERVER_CONFIG + f"subjectAltName = {san}\n", encoding="ascii")
        request = scratch / "server.csr"
        _openssl(
            executable, "req", "-new", "-newkey", "rsa:2048", "-noenc", "-sha256",
            "-config", config, "-keyout", draft / "server.key", "-out", request,
        )
        _openssl(
            executable, "x509", "-req", "-in", request, "-CA", ca_file, "-CAkey", ca_key,
            "-set_serial", f"0x{token_hex(16)}", "-out", draft / "server.pem",
            "-days", str(days), "-sha256", "-extfile", config, "-extensions", "server",
        )
        shutil.copyfile(ca_file, draft / "ca.pem")
        if not _valid_server_pair(executable, draft, ca_file, str(address)):
            raise ValueError("新服务器证书未通过地址、签发链或私钥校验。")
        certificate_dir = servers / token_hex(8)
        draft.rename(certificate_dir)
        return certificate_dir
