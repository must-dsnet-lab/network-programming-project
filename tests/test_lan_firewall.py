import base64
import json
import sys
import time
import unittest
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch

from rover.vehicle import lan_firewall


class LanFirewallTests(unittest.TestCase):
    program = Path(r"C:\Program Files\Rover\RoverVehicleAdmin.exe")

    def setUp(self):
        program = patch.object(lan_firewall, "_packaged_program", return_value=self.program)
        program.start()
        self.addCleanup(program.stop)

    def test_support_requires_concrete_local_ipv4_and_packaged_exe(self):
        self.assertTrue(lan_firewall.supports_lan_firewall("192.168.1.8"))
        for host in ("127.0.0.1", "0.0.0.0", "::1", "224.0.0.1", "8.8.8.8", "garbage"):
            with self.subTest(host=host):
                self.assertFalse(lan_firewall.supports_lan_firewall(host))
        with patch.object(lan_firewall, "_packaged_program", return_value=None):
            self.assertFalse(lan_firewall.supports_lan_firewall("192.168.1.8"))

    @staticmethod
    def discovery(profile):
        return CompletedProcess([], 0, json.dumps({"profile": profile}), "")

    def test_private_plan_reuses_identity_after_local_address_changes(self):
        with patch.object(lan_firewall, "_run_probe", side_effect=[
            self.discovery("Private"), CompletedProcess([], 0, "true", ""),
            self.discovery("Private"), CompletedProcess([], 0, "true", ""),
        ]):
            old = lan_firewall.inspect_lan_firewall("192.168.1.8", 19177)
            new = lan_firewall.inspect_lan_firewall("192.168.5.9", 19177)
        self.assertTrue(old.ready)
        self.assertTrue(new.ready)
        self.assertEqual(old.plan.rule_names, new.plan.rule_names)
        self.assertEqual(new.plan.local_address, "Any")

    def test_public_plan_changes_scope_with_local_address(self):
        with patch.object(lan_firewall, "_run_probe", side_effect=[
            self.discovery("Public"), CompletedProcess([], 0, "false", ""),
            self.discovery("Public"), CompletedProcess([], 0, "false", ""),
        ]):
            old = lan_firewall.inspect_lan_firewall("10.0.0.9", 19177)
            new = lan_firewall.inspect_lan_firewall("10.0.0.10", 19177)
        self.assertFalse(old.ready)
        self.assertNotEqual(old.plan.rule_names, new.plan.rule_names)
        self.assertEqual(new.plan.local_address, "10.0.0.10")

    def test_domain_and_invalid_port_do_not_request_rule_changes(self):
        with patch.object(lan_firewall, "_run_probe", return_value=self.discovery("DomainAuthenticated")) as run:
            with self.assertRaises(RuntimeError):
                lan_firewall.inspect_lan_firewall("192.168.1.8", 19177)
            with self.assertRaises(ValueError):
                lan_firewall.inspect_lan_firewall("192.168.1.8", True)
        self.assertEqual(run.call_count, 1)

    def test_declined_elevation_returns_false(self):
        plan = lan_firewall.FirewallPlan(self.program, "192.168.1.8", 19177, "Private")
        with patch.object(lan_firewall, "_run_elevated", return_value=CompletedProcess([], 3, "", "")):
            self.assertFalse(lan_firewall.configure_lan_firewall(plan))

    def test_elevation_result_distinguishes_drift_and_success(self):
        plan = lan_firewall.FirewallPlan(self.program, "192.168.1.8", 19177, "Private")
        with patch.object(lan_firewall, "_run_elevated", return_value=CompletedProcess([], 0, "", "")):
            self.assertTrue(lan_firewall.configure_lan_firewall(plan))
        with patch.object(lan_firewall, "_run_elevated", return_value=CompletedProcess([], 2, "", "")):
            with self.assertRaisesRegex(RuntimeError, "网络"):
                lan_firewall.configure_lan_firewall(plan)

    def test_settings_are_data_at_native_boundary(self):
        exe = self.program.with_name("Rover's VehicleAdmin.exe")
        plan = lan_firewall.FirewallPlan(exe, "192.168.1.8", 19177, "Private")
        with (
            patch.object(lan_firewall, "_packaged_program", return_value=exe),
            patch.object(lan_firewall, "_run_elevated", return_value=CompletedProcess([], 0, "", "")) as run,
        ):
            self.assertTrue(lan_firewall.configure_lan_firewall(plan))
        settings, script = run.call_args.args
        self.assertEqual(settings["program"], str(exe))
        self.assertEqual(settings["management_port"], 19177)
        self.assertNotIn(str(exe), script)
        encoded_command = lan_firewall._encode_powershell(settings, script)
        expanded = base64.b64decode(encoded_command).decode("utf-16-le")
        self.assertNotIn(str(exe), expanded)
        self.assertIn("-RemoteAddress LocalSubnet", expanded)

    @unittest.skipUnless(sys.platform == "win32", "requires Windows PowerShell")
    def test_native_scripts_match_scalar_filters_and_roll_back_partial_rules(self):
        plan = lan_firewall.FirewallPlan(self.program, "10.0.0.9", 19177, "Public")
        settings = lan_firewall._settings(plan)
        settings["deadline"] = int(time.time()) + 60
        # Shadow every Windows firewall cmdlet used by the script before executing it.
        # Only the objects in this test process change.
        fake_cmdlets = r"""
$installed = @{}
function Import-Module { param($Name) }
function Get-NetIPAddress { param($AddressFamily,$IPAddress) @{ InterfaceIndex = 7 } | ForEach-Object { [pscustomobject]$_ } }
function Get-NetConnectionProfile { param($InterfaceIndex) [pscustomobject]@{ NetworkCategory = 'Public' } }
function Get-NetFirewallRule {
    [CmdletBinding()]
    param($PolicyStore,$Name)
    if ($installed.ContainsKey($Name)) { $installed[$Name] }
}
function Get-NetFirewallApplicationFilter {
    param([Parameter(ValueFromPipeline)]$Rule)
    process { [pscustomobject]@{ Program = $Rule.Program } }
}
function Get-NetFirewallAddressFilter {
    param([Parameter(ValueFromPipeline)]$Rule)
    process {
        [pscustomobject]@{ LocalAddress = $Rule.LocalAddress; RemoteAddress = $Rule.RemoteAddress }
    }
}
function Get-NetFirewallPortFilter {
    param([Parameter(ValueFromPipeline)]$Rule)
    process { [pscustomobject]@{ Protocol = $Rule.Protocol; LocalPort = $Rule.LocalPort } }
}
function New-NetFirewallRule {
    [CmdletBinding()]
    param($PolicyStore,$Name,$DisplayName,$Description,$Enabled,$Direction,$Action,$Profile,
          $Program,$Protocol,$LocalPort,$LocalAddress,$RemoteAddress,$EdgeTraversalPolicy)
    if ($settings.test_fail_udp -and $Protocol -eq 'UDP') { throw 'simulated UDP failure' }
    if ($RemoteAddress -ne 'LocalSubnet' -or $LocalAddress -ne '10.0.0.9' -or
        $Program -ne $settings.program -or $EdgeTraversalPolicy -ne 'Block' -or
        ($Protocol -eq 'TCP' -and $LocalPort -ne '19177') -or
        ($Protocol -eq 'UDP' -and $LocalPort -ne 'Any')) { throw 'unexpected rule filters' }
    $installed[$Name] = [pscustomobject]$PSBoundParameters
}
function Remove-NetFirewallRule {
    [CmdletBinding()]
    param($PolicyStore,$Name)
    $installed.Remove($Name)
    Write-Output "removed:$Name"
}
"""
        discovery = lan_firewall._run_probe(settings, fake_cmdlets + lan_firewall._DISCOVER)
        self.assertEqual(discovery.returncode, 0, discovery.stderr)
        self.assertEqual(json.loads(discovery.stdout), {"profile": "Public"})

        empty = lan_firewall._run_probe(settings, fake_cmdlets + lan_firewall._INSPECT)
        self.assertEqual(empty.returncode, 0, empty.stderr)
        self.assertFalse(json.loads(empty.stdout))

        complete = lan_firewall._run_probe(settings, fake_cmdlets + lan_firewall._APPLY)
        self.assertEqual(complete.returncode, 0, complete.stderr)

        settings["test_fail_udp"] = True
        partial = lan_firewall._run_probe(settings, fake_cmdlets + lan_firewall._APPLY)
        self.assertNotEqual(partial.returncode, 0)
        self.assertIn(f"removed:{plan.rule_names[0]}", partial.stdout)
        self.assertNotIn(f"removed:{plan.rule_names[1]}", partial.stdout)
