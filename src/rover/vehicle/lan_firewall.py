"""Inspect and install narrowly scoped Windows firewall rules for Rover."""

import base64
import ctypes
import json
import subprocess
import sys
import time
from dataclasses import dataclass
from hashlib import sha256
from ipaddress import IPv4Address
from pathlib import Path


_INSPECT_TIMEOUT = 20
_CONSENT_TIMEOUT = 90
_PROFILES = frozenset(("Private", "Public"))
_DESCRIPTION = "Rover vehicle administration local network"


@dataclass(frozen=True)
class FirewallPlan:
    program: Path
    bind_host: str
    management_port: int
    profile: str

    @property
    def local_address(self) -> str:
        return self.bind_host if self.profile == "Public" else "Any"

    @property
    def rule_names(self) -> tuple[str, str]:
        scope = f"{str(self.program).casefold()}\0{self.profile}\0{self.local_address}"
        tcp = sha256(f"{scope}\0{self.management_port}".encode()).hexdigest()[:24]
        udp = sha256(scope.encode()).hexdigest()[:24]
        return f"Rover.LAN.Management.{tcp}", f"Rover.LAN.UDP.{udp}"


@dataclass(frozen=True)
class FirewallCheck:
    plan: FirewallPlan
    ready: bool


_PROFILE_SCRIPT = r"""
Import-Module (Join-Path $PSHOME 'Modules\NetTCPIP\NetTCPIP.psd1')
Import-Module (Join-Path $PSHOME 'Modules\NetConnection\NetConnection.psd1')
function Read-NetworkProfile {
    $indices = @(Get-NetIPAddress -AddressFamily IPv4 -IPAddress $settings.bind_host |
        Select-Object -ExpandProperty InterfaceIndex -Unique)
    if ($indices.Count -ne 1) { throw 'Rover requires a single interface for the selected IPv4 address.' }
    $profiles = @(Get-NetConnectionProfile -InterfaceIndex $indices[0])
    if ($profiles.Count -ne 1) { throw 'Rover could not determine the active Windows network profile.' }
    return [string]$profiles[0].NetworkCategory
}
"""

_RULE_SCRIPT = r"""
Import-Module (Join-Path $PSHOME 'Modules\NetSecurity\NetSecurity.psd1')
$rules = @(
    @{ Name = $settings.tcp_name; Protocol = 'TCP'; ProtocolNumber = '6'
       LocalPort = [string]$settings.management_port; DisplayName = 'Rover LAN management TCP' },
    @{ Name = $settings.udp_name; Protocol = 'UDP'; ProtocolNumber = '17'
       LocalPort = 'Any'; DisplayName = 'Rover LAN telemetry and control UDP' }
)

function Read-LanRule($name) {
    $queryErrors = @()
    $matches = @(Get-NetFirewallRule -PolicyStore ActiveStore -Name $name -ErrorAction SilentlyContinue -ErrorVariable queryErrors)
    foreach ($queryError in $queryErrors) {
        if ($queryError.CategoryInfo.Category -ne 'ObjectNotFound') { throw $queryError }
    }
    if ($matches.Count -gt 1) { throw 'Multiple matching Rover firewall rules were found.' }
    if ($matches.Count -eq 1) { return $matches[0] }
}

function Assert-LanRule($rule, $expected) {
    $app = $rule | Get-NetFirewallApplicationFilter
    $address = $rule | Get-NetFirewallAddressFilter
    $port = $rule | Get-NetFirewallPortFilter
    if ([string]$rule.Enabled -ne 'True' -or [string]$rule.Direction -ne 'Inbound' -or
        [string]$rule.Action -ne 'Allow' -or [string]$rule.Profile -ne $settings.profile -or
        [string]$rule.EdgeTraversalPolicy -ne 'Block' -or
        $rule.Description -ne $settings.description -or $app.Program -ne $settings.program -or
        @($address.LocalAddress).Count -ne 1 -or @($address.LocalAddress)[0] -ne $settings.local_address -or
        @($address.RemoteAddress).Count -ne 1 -or @($address.RemoteAddress)[0] -ne 'LocalSubnet' -or
        [string]$port.Protocol -notin @($expected.Protocol, $expected.ProtocolNumber) -or
        @($port.LocalPort).Count -ne 1 -or @($port.LocalPort)[0] -ne $expected.LocalPort) {
        throw 'A managed Rover firewall rule exists with different filters.'
    }
}

function Test-LanRules {
    $ready = $true
    foreach ($expected in $rules) {
        $rule = Read-LanRule $expected.Name
        if ($null -eq $rule) { $ready = $false }
        else { Assert-LanRule $rule $expected }
    }
    return $ready
}
"""

_DISCOVER = _PROFILE_SCRIPT + r"""
@{ profile = (Read-NetworkProfile) } | ConvertTo-Json -Compress
"""

_INSPECT = _RULE_SCRIPT + r"""
(Test-LanRules) | ConvertTo-Json -Compress
"""

_APPLY = _PROFILE_SCRIPT + _RULE_SCRIPT + r"""
$mutex = [Threading.Mutex]::new($false, ('Global\' + $settings.udp_name))
$locked = $false
try {
    try {
        $locked = $mutex.WaitOne(10000)
    } catch [Threading.AbandonedMutexException] {
        $locked = $true
    }
    if (-not $locked) { throw 'Another Rover firewall setup is in progress.' }
    if ([DateTimeOffset]::UtcNow.ToUnixTimeSeconds() -ge $settings.deadline) { exit 4 }
    if ((Read-NetworkProfile) -ne $settings.profile) { exit 2 }
    if (Test-LanRules) { exit 0 }
    $created = @()
    try {
        foreach ($expected in $rules) {
            $existing = Read-LanRule $expected.Name
            if ($null -ne $existing) {
                Assert-LanRule $existing $expected
                continue
            }
            New-NetFirewallRule -PolicyStore PersistentStore -Name $expected.Name -DisplayName $expected.DisplayName `
                -Description $settings.description -Enabled True -Direction Inbound -Action Allow `
                -Profile $settings.profile -Program $settings.program -Protocol $expected.Protocol `
                -LocalPort $expected.LocalPort -LocalAddress $settings.local_address -RemoteAddress LocalSubnet `
                -EdgeTraversalPolicy Block | Out-Null
            $created += $expected.Name
        }
        if (-not (Test-LanRules)) { throw 'Rover rules were not accepted by the active policy.' }
    } catch {
        foreach ($name in $created) {
            Remove-NetFirewallRule -PolicyStore PersistentStore -Name $name -ErrorAction SilentlyContinue
        }
        throw
    }
} finally {
    if ($locked) { $mutex.ReleaseMutex() }
    $mutex.Dispose()
}
"""


def _packaged_program() -> Path | None:
    program = Path(sys.executable)
    if sys.platform == "win32" and getattr(sys, "frozen", False):
        if program.is_absolute() and program.name.casefold() == "rovervehicleadmin.exe":
            return program
    return None


def _local_ipv4(bind_host: str) -> str:
    address = IPv4Address(bind_host)
    if not address.is_private or address.is_unspecified or address.is_loopback or address.is_reserved:
        raise ValueError("局域网防火墙配置需要当前车端的内网 IPv4。")
    return str(address)


def supports_lan_firewall(bind_host: str) -> bool:
    if _packaged_program() is None:
        return False
    try:
        _local_ipv4(bind_host)
    except ValueError:
        return False
    return True


def _validate_port(port: int) -> None:
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError("管理端口需要 1–65535 的整数。")


def _settings(plan: FirewallPlan) -> dict:
    tcp, udp = plan.rule_names
    return dict(
        bind_host=plan.bind_host, program=str(plan.program), profile=plan.profile,
        local_address=plan.local_address, management_port=plan.management_port,
        description=_DESCRIPTION, tcp_name=tcp, udp_name=udp,
    )


def _powershell_path() -> Path:
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    get_system_directory = kernel.GetSystemDirectoryW
    get_system_directory.argtypes = (ctypes.c_wchar_p, ctypes.c_uint)
    get_system_directory.restype = ctypes.c_uint
    directory = ctypes.create_unicode_buffer(32768)
    length = get_system_directory(directory, len(directory))
    if not 0 < length < len(directory):
        raise OSError("无法确定 Windows 系统目录。")
    return Path(directory.value) / "WindowsPowerShell" / "v1.0" / "powershell.exe"


def _encode_powershell(settings: dict, script: str) -> str:
    data = base64.b64encode(json.dumps(settings).encode()).decode("ascii")
    command = (
        "$ErrorActionPreference = 'Stop'\n"
        "[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)\n"
        "$env:PSModulePath = Join-Path $PSHOME 'Modules'\n"
        f"$settings = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{data}')) | ConvertFrom-Json\n"
        + script
    )
    return base64.b64encode(command.encode("utf-16-le")).decode("ascii")


def _run_probe(settings: dict, script: str) -> subprocess.CompletedProcess:
    powershell = _powershell_path()
    return subprocess.run(
        [str(powershell), "-NoProfile", "-NonInteractive", "-EncodedCommand",
         _encode_powershell(settings, script)],
        stdin=subprocess.DEVNULL, capture_output=True, encoding="utf-8", errors="replace",
        timeout=_INSPECT_TIMEOUT, creationflags=subprocess.CREATE_NO_WINDOW,
    )


def _read_probe(settings: dict, script: str):
    try:
        result = _run_probe(settings, script)
        if result.returncode != 0:
            raise RuntimeError("无法确认车端的网络配置文件或现有防火墙规则。")
        return json.loads(result.stdout.strip())
    except (OSError, subprocess.TimeoutExpired, ValueError) as error:
        raise RuntimeError("无法读取 Windows 防火墙配置。") from error


def inspect_lan_firewall(bind_host: str, management_port: int) -> FirewallCheck:
    _validate_port(management_port)
    address = _local_ipv4(bind_host)
    program = _packaged_program()
    if program is None:
        raise RuntimeError("局域网防火墙配置需使用 Windows 车端管理 EXE。")
    discovery = _read_probe({"bind_host": address}, _DISCOVER)
    profile = discovery.get("profile") if isinstance(discovery, dict) else None
    if not isinstance(profile, str) or profile not in _PROFILES:
        raise RuntimeError("当前网络配置文件由 Windows 或组织管理员管理。")
    plan = FirewallPlan(program, address, management_port, profile)
    installed = _read_probe(_settings(plan), _INSPECT)
    if type(installed) is not bool:
        raise RuntimeError("无法识别 Windows 防火墙规则状态。")
    return FirewallCheck(plan, installed)


def _run_elevated(settings: dict, script: str) -> subprocess.CompletedProcess:
    powershell = _powershell_path()
    command = _encode_powershell(settings, script)
    # Keep the encoded child command on the argument list without encoding it a second time.
    wrapper = (
        "$ErrorActionPreference = 'Stop'; "
        "$env:PSModulePath = Join-Path $PSHOME 'Modules'; "
        "try { "
        "$p = Start-Process -FilePath (Join-Path $PSHOME 'powershell.exe') "
        f"-ArgumentList '-NoProfile','-NonInteractive','-EncodedCommand','{command}' "
        "-Verb RunAs -WindowStyle Hidden -Wait -PassThru; "
        "exit $p.ExitCode "
        "} catch { "
        "$cause = $_.Exception; while ($null -ne $cause) { "
        "if ($cause -is [ComponentModel.Win32Exception] -and $cause.NativeErrorCode -eq 1223) { exit 3 }; "
        "$cause = $cause.InnerException }; exit 1 }"
    )
    return subprocess.run(
        [str(powershell), "-NoProfile", "-NonInteractive", "-Command", wrapper],
        stdin=subprocess.DEVNULL, capture_output=True, encoding="utf-8", errors="replace",
        timeout=_CONSENT_TIMEOUT, creationflags=subprocess.CREATE_NO_WINDOW,
    )


def configure_lan_firewall(plan: FirewallPlan) -> bool:
    """Request administrator consent and add the program's two inbound rules."""
    _validate_port(plan.management_port)
    _local_ipv4(plan.bind_host)
    if plan.program != _packaged_program() or plan.profile not in _PROFILES:
        raise ValueError("当前车端程序或网络配置文件不支持自动放行。")
    settings = _settings(plan)
    settings["deadline"] = int(time.time()) + _CONSENT_TIMEOUT - 5
    try:
        result = _run_elevated(settings, _APPLY)
    except subprocess.TimeoutExpired as error:
        raise RuntimeError("等待管理员授权超时，请重新检查局域网放行状态。") from error
    if result.returncode == 0:
        return True
    if result.returncode == 3:
        return False
    if result.returncode == 2:
        raise RuntimeError("网络配置已变化，请重新启动车端并核对监听地址。")
    if result.returncode == 4:
        raise RuntimeError("管理员授权已超时，请重新启动车端。")
    raise RuntimeError("Windows 未能应用车端防火墙规则，请检查管理员权限或系统策略。")
