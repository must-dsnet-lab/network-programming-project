import json
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from rover.desktop.connection_profile import (
    DesktopConnectionSettings,
    load_desktop_profile,
    save_desktop_profile,
)


class TestDesktopConnectionSettings(TestCase):
    def test_default_addresses_match_the_local_desktop(self):
        settings = DesktopConnectionSettings()

        self.assertEqual(settings.mode, "advanced")
        self.assertEqual(settings.management_address, ("127.0.0.1", 19177))
        self.assertEqual(settings.control_address, ("127.0.0.1", 9999))
        self.assertEqual(settings.telemetry_address, ("127.0.0.1", 10000))
        self.assertEqual(settings.tls_server_name, "localhost")

    def test_remote_name_and_telemetry_bind_are_normalized(self):
        settings = DesktopConnectionSettings(
            mode="expert", vehicle_host=" Rover.Example. ", telemetry_bind_host=" 0.0.0.0 ",
            telemetry_port=10001, client_id=" desktop-b ",
        )

        self.assertEqual(settings.management_address, ("rover.example", 19177))
        self.assertEqual(settings.telemetry_address, ("0.0.0.0", 10001))
        self.assertEqual(settings.tls_server_name, "rover.example")
        self.assertEqual(settings.client_id, "desktop-b")

    def test_explicit_certificate_name_does_not_change_destination(self):
        settings = DesktopConnectionSettings(
            vehicle_host="192.168.5.20", server_name=" Lab-Rover.Example ",
        )

        self.assertEqual(settings.control_address, ("192.168.5.20", 9999))
        self.assertEqual(settings.tls_server_name, "lab-rover.example")

    def test_rejects_invalid_or_unsupported_network_names(self):
        for field, value in (
            ("vehicle_host", ""),
            ("vehicle_host", "127.0.0.999"),
            ("vehicle_host", "https://lab-rover.example"),
            ("server_name", "rover..example"),
            ("server_name", "host:19177"),
            ("telemetry_bind_host", "::1"),
        ):
            with self.subTest(field=field, value=value):
                with self.assertRaises(ValueError):
                    DesktopConnectionSettings(**{field: value})

    def test_ports_require_integers_in_the_udp_tcp_port_range(self):
        settings = DesktopConnectionSettings(management_port=1, control_port=65535)
        self.assertEqual(settings.management_address[1], 1)
        self.assertEqual(settings.control_address[1], 65535)

        for field in ("management_port", "control_port", "telemetry_port"):
            for value in (0, 65536, True, 1.0, "19177"):
                with self.subTest(field=field, value=value):
                    with self.assertRaises(ValueError):
                        DesktopConnectionSettings(**{field: value})

    def test_rejects_invalid_mode_account_and_ca_type(self):
        for changes in ({"mode": "demo"}, {"client_id": "  "}, {"client_id": "a\nb"},
                        {"ca_file": "ca.pem"}):
            with self.subTest(changes=changes):
                with self.assertRaises(ValueError):
                    DesktopConnectionSettings(**changes)


class TestDesktopConnectionProfileIO(TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.profile = Path(temporary.name) / "rover" / "desktop-connection.json"

    def test_missing_profile_does_not_create_files(self):
        self.assertIsNone(load_desktop_profile(self.profile))
        self.assertFalse(self.profile.parent.exists())

    def test_round_trip_saves_only_connection_fields_with_absolute_ca(self):
        settings = DesktopConnectionSettings(
            mode="expert", vehicle_host="192.168.5.20", management_port=9101, control_port=9102,
            telemetry_bind_host="0.0.0.0", telemetry_port=9103, server_name="lab-rover.example",
            ca_file=Path("local") / "certs" / "public-ca.pem", client_id="实验员",
        )

        save_desktop_profile(self.profile, settings)

        persisted = json.loads(self.profile.read_text(encoding="utf-8"))
        self.assertEqual(persisted, {
            "version": 1, "mode": "expert", "vehicle_host": "192.168.5.20",
            "management_port": 9101, "control_port": 9102, "telemetry_bind_host": "0.0.0.0",
            "telemetry_port": 9103, "server_name": "lab-rover.example",
            "ca_file": str(settings.ca_file.resolve()), "client_id": "实验员",
        })
        self.assertEqual(load_desktop_profile(self.profile), replace(settings, ca_file=settings.ca_file.resolve()))

    def test_malformed_profiles_raise_value_error(self):
        save_desktop_profile(self.profile, DesktopConnectionSettings())
        raw = self.profile.read_text(encoding="utf-8")
        document = json.loads(raw)
        variants = (
            b"invalid {", b"\xff", b"[]",
            json.dumps({**document, "version": True}).encode(),
            json.dumps({**document, "version": 2}).encode(),
            json.dumps({**document, "credential": "sample-ignored"}).encode(),
            json.dumps({**document, "ca_file": ""}).encode(),
            json.dumps({**document, "ca_file": 22}).encode(),
            json.dumps({**document, "management_port": True}).encode(),
            json.dumps({key: value for key, value in document.items() if key != "vehicle_host"}).encode(),
            raw.replace('"version": 1', '"version": 1, "version": 1').encode(),
        )
        for index, body in enumerate(variants):
            with self.subTest(index=index):
                self.profile.write_bytes(body)
                with self.assertRaises(ValueError):
                    load_desktop_profile(self.profile)

    def test_read_errors_other_than_a_missing_file_propagate(self):
        self.profile.mkdir(parents=True)
        with self.assertRaises(OSError):
            load_desktop_profile(self.profile)

    def test_failed_replace_preserves_previous_profile_and_cleans_temporary_file(self):
        save_desktop_profile(self.profile, DesktopConnectionSettings())
        previous = self.profile.read_bytes()

        with patch.object(Path, "replace", side_effect=OSError("replace denied")):
            with self.assertRaises(OSError):
                save_desktop_profile(self.profile, DesktopConnectionSettings(mode="expert"))

        self.assertEqual(self.profile.read_bytes(), previous)
        self.assertEqual(list(self.profile.parent.iterdir()), [self.profile])
