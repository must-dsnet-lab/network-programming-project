import io
import json
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch, sentinel

import rover.vehicle.app as app


class TestVehicleStartup(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.cert_dir = self.root / "certs"
        self.cert_dir.mkdir()
        for name in ("server.pem", "server.key"):
            (self.cert_dir / name).write_text("dummy certificate fixture", encoding="utf-8")
        self.credentials = self.root / "credentials.json"
        self.credentials.write_text('{"desktop": " token "}', encoding="utf-8")
        self.secure_args = [
            "--secure", "--cert-dir", str(self.cert_dir),
            "--credentials-file", str(self.credentials),
        ]

    def assert_rejected_without_startup(self, argv):
        with (
            patch.object(app, "main") as main,
            patch.object(app, "management_listener") as listener,
            redirect_stderr(io.StringIO()),
            self.assertRaises(SystemExit) as raised,
        ):
            app.cli(argv)
        self.assertEqual(raised.exception.code, 2)
        main.assert_not_called()
        listener.assert_not_called()

    def test_loader_accepts_utf8_and_bom_preserving_credentials(self):
        for encoding in ("utf-8", "utf-8-sig"):
            with self.subTest(encoding=encoding):
                self.credentials.write_text(
                    json.dumps({"desktop": " token ", "client-2": "密钥"}),
                    encoding=encoding,
                )
                self.assertEqual(
                    app.load_trusted_credentials(self.credentials),
                    {"desktop": b" token ", "client-2": b"\xe5\xaf\x86\xe9\x92\xa5"},
                )

    def test_loader_rejects_invalid_documents_and_missing_file(self):
        invalid_documents = [
            b"{", b"\xff", b"[]", b"{}", b"null", b"42",
            b'{"": "secret"}', b'{"  ": "secret"}',
            b'{"desktop": ""}', b'{"desktop": " \\t"}',
            b'{"desktop": null}', b'{"desktop": 7}',
            b'{"desktop": true}', b'{"desktop": []}',
        ]
        for document in invalid_documents:
            with self.subTest(document=document):
                self.credentials.write_bytes(document)
                with self.assertRaises(ValueError):
                    app.load_trusted_credentials(self.credentials)
        with self.assertRaises(FileNotFoundError):
            app.load_trusted_credentials(self.root / "missing.json")

    def test_demo_routes_default_and_explicit_addresses_without_listener(self):
        cases = [
            ([], ("127.0.0.1", 9999), ("127.0.0.1", 10000)),
            (["--bind-host", "0.0.0.0", "--control-port", "1",
              "--telemetry-host", "192.0.2.10", "--telemetry-port", "65535"],
             ("0.0.0.0", 1), ("192.0.2.10", 65535)),
        ]
        for argv, address, telemetry_address in cases:
            with (
                self.subTest(argv=argv),
                patch.object(app, "main") as main,
                patch.object(app, "management_listener") as listener,
            ):
                app.cli(argv)
                main.assert_called_once_with(
                    address=address,
                    telemetry_address=telemetry_address,
                    management=None,
                    observer_address=None,
                )
                listener.assert_not_called()

    def test_secure_routes_listener_context_and_selected_addresses(self):
        for custom in (False, True):
            argv = self.secure_args + ([
                "--bind-host", "0.0.0.0", "--control-port", "20001",
                "--management-port", "20002", "--telemetry-host", "192.0.2.20",
                "--telemetry-port", "20003",
            ] if custom else [])
            with (
                self.subTest(custom=custom),
                patch.object(app, "main") as main,
                patch.object(app, "management_listener", return_value=sentinel.context) as listener,
            ):
                app.cli(argv)
                listener.assert_called_once_with(
                    self.cert_dir, {"desktop": b" token "},
                    address=("0.0.0.0", 20002) if custom else ("127.0.0.1", 19177),
                )
                main.assert_called_once_with(
                    address=("0.0.0.0", 20001) if custom else ("127.0.0.1", 9999),
                    telemetry_address=(
                        ("192.0.2.20", 20003) if custom else ("127.0.0.1", 10000)
                    ),
                    management=sentinel.context,
                    observer_address=None,
                )

    def test_invalid_and_incomplete_arguments_do_not_start_runtime(self):
        cases = [
            ["--secure"], ["--secure", "--cert-dir", str(self.cert_dir)],
            ["--expert-management"],
            ["--secure", "--credentials-file", str(self.credentials)],
            ["--cert-dir", str(self.cert_dir)],
            ["--credentials-file", str(self.credentials)],
            ["--unknown-option"],
        ]
        for option in ("--bind-host", "--telemetry-host"):
            cases.extend([[option, ""], [option, "  "]])
        for option in ("--control-port", "--management-port", "--telemetry-port"):
            cases.extend([[option, value] for value in ("0", "65536", "-1", "abc")])
        for argv in cases:
            with self.subTest(argv=argv):
                self.assert_rejected_without_startup(argv)

    def test_expert_management_selects_opt_in_listener(self):
        with (
            patch.object(app, "main") as main,
            patch.object(app, "expert_management_listener", return_value=sentinel.context),
            patch.object(app, "management_listener") as advanced,
        ):
            app.cli([*self.secure_args, "--expert-management"])
        advanced.assert_not_called()
        self.assertIs(main.call_args.kwargs["management"], sentinel.context)

    def test_invalid_or_missing_credentials_prevent_secure_startup(self):
        for document in (b"{", b"\xff", b"[]", b"{}", b'{"client": " "}'):
            with self.subTest(document=document):
                self.credentials.write_bytes(document)
                self.assert_rejected_without_startup(self.secure_args)
        self.credentials.unlink()
        self.assert_rejected_without_startup(self.secure_args)

    def test_missing_certificate_files_prevent_secure_startup(self):
        for name in ("server.pem", "server.key"):
            with self.subTest(missing=name):
                missing = self.cert_dir / name
                missing.unlink()
                self.assert_rejected_without_startup(self.secure_args)
                missing.write_text("dummy certificate fixture", encoding="utf-8")

    def test_secure_runtime_oserror_exits_without_demo_retry(self):
        for failure_site in ("management_listener", "main"):
            with (
                self.subTest(failure_site=failure_site),
                patch.object(app, "main") as main,
                patch.object(app, "management_listener", return_value=sentinel.context) as listener,
                redirect_stderr(io.StringIO()),
            ):
                failing = listener if failure_site == "management_listener" else main
                failing.side_effect = OSError("TLS or bind failed")
                with self.assertRaises(SystemExit) as raised:
                    app.cli(self.secure_args)
                self.assertEqual(raised.exception.code, 2)
                listener.assert_called_once_with(
                    self.cert_dir, {"desktop": b" token "}, address=("127.0.0.1", 19177),
                )
                if failure_site == "management_listener":
                    main.assert_not_called()
                else:
                    main.assert_called_once_with(
                        address=("127.0.0.1", 9999),
                        telemetry_address=("127.0.0.1", 10000),
                        management=sentinel.context,
                        observer_address=None,
                    )

    def test_main_binds_telemetry_to_selected_local_interface(self):
        control = MagicMock()
        control.__enter__.return_value = control
        control.recvfrom.side_effect = BlockingIOError
        telemetry = MagicMock()
        telemetry.__enter__.return_value = telemetry

        with (
            patch.object(app.socket, "socket", side_effect=[control, telemetry]),
            patch.object(app, "sleep", side_effect=KeyboardInterrupt),
            patch("builtins.print"),
        ):
            app.main(
                address=("0.0.0.0", 20001),
                telemetry_address=("192.0.2.20", 20003),
            )

        control.bind.assert_called_once_with(("0.0.0.0", 20001))
        telemetry.bind.assert_called_once_with(("0.0.0.0", 0))

    def test_observer_options_route_an_extra_telemetry_address(self):
        for options, expected_port in (
            ([], 10001),
            (["--observer-port", "20004"], 20004),
        ):
            with self.subTest(options=options), patch.object(app, "main") as main:
                app.cli(["--observer-host", "192.0.2.30", *options])
                self.assertEqual(
                    main.call_args.kwargs["observer_address"],
                    ("192.0.2.30", expected_port),
                )

    def test_observer_port_requires_an_ipv4_address(self):
        for argv in (
            ["--observer-port", "20004"],
            ["--observer-host", "rover.example"],
            ["--observer-host", "192.0.2.30", "--observer-port", "0"],
        ):
            with self.subTest(argv=argv):
                self.assert_rejected_without_startup(argv)

    def test_event_log_argument_routes_output_path(self):
        event_log = self.root / "vehicle-events.csv"
        with patch.object(app, "main") as main:
            app.cli(["--event-log", str(event_log)])
        self.assertEqual(main.call_args.kwargs["event_log"], event_log)

    def test_one_telemetry_tick_attempts_both_destinations(self):
        primary = ("192.0.2.20", 20003)
        observer = ("192.0.2.30", 20004)

        for failing_address in (None, primary, observer):
            with self.subTest(failing_address=failing_address):
                control, telemetry = MagicMock(), MagicMock()
                control.__enter__.return_value = control
                telemetry.__enter__.return_value = telemetry
                control.recvfrom.side_effect = BlockingIOError

                def send_packet(packet, address):
                    if address == failing_address:
                        raise OSError("telemetry send failed")
                    return len(packet)

                telemetry.sendto.side_effect = send_packet
                with (
                    patch.object(
                        app.socket, "socket", side_effect=[control, telemetry]
                    ),
                    patch.object(app, "sleep", side_effect=KeyboardInterrupt),
                    patch("builtins.print"),
                ):
                    app.main(telemetry_address=primary, observer_address=observer)

                sent = [call.args for call in telemetry.sendto.call_args_list]
                self.assertEqual([address for _, address in sent], [primary, observer])
                self.assertEqual(sent[0][0], sent[1][0])


if __name__ == "__main__":
    unittest.main()
