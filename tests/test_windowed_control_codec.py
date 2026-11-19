import hmac
import json
import unittest

from rover.core.control_target import ControlTarget
from rover.protocol.control_codec import (
    MAX_CONTROL_MESSAGE_BYTES,
    ControlCommand,
    decode_authenticated_command,
    decode_windowed_command,
    encode_authenticated_command,
    encode_windowed_command,
)


class TestWindowedControlCodec(unittest.TestCase):
    key = b"test-session-key"
    command = ControlCommand(
        session_id="session-a",
        seq=7,
        target=ControlTarget(1.0, 0.5),
        window_id="window-a",
    )

    def _message(self):
        return {
            "version": 2,
            "type": "authenticated_control_command",
            "session_id": "session-a",
            "seq": 7,
            "speed_mps": 1.0,
            "heading_rad": 0.5,
            "window_id": "window-a",
        }

    def _signed_packet(self, message):
        payload = json.dumps(
            message, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
        signed = message.copy()
        signed["mac"] = hmac.new(self.key, payload, "sha256").hexdigest()
        return json.dumps(signed).encode("utf-8")

    def test_encoder_matches_version_two_wire_format(self):
        payload = (
            b'{"heading_rad":0.5,"seq":7,"session_id":"session-a",'
            b'"speed_mps":1.0,"type":"authenticated_control_command",'
            b'"version":2,"window_id":"window-a"}'
        )
        expected = self._message()
        expected["mac"] = hmac.new(self.key, payload, "sha256").hexdigest()

        self.assertEqual(
            json.loads(encode_windowed_command(self.command, self.key)),
            expected,
        )

    def test_round_trip_preserves_window_id(self):
        packet = encode_windowed_command(self.command, self.key)

        self.assertEqual(
            decode_windowed_command(packet, self.key),
            self.command,
        )

    def test_tampered_window_id_is_rejected(self):
        packet = encode_windowed_command(self.command, self.key)
        changed = json.loads(packet)
        changed["window_id"] = "window-other"

        with self.assertRaisesRegex(ValueError, "MAC"):
            decode_windowed_command(json.dumps(changed).encode("utf-8"), self.key)

    def test_wrong_key_is_rejected(self):
        packet = encode_windowed_command(self.command, self.key)

        with self.assertRaisesRegex(ValueError, "MAC"):
            decode_windowed_command(packet, b"wrong-key")

    def test_encoder_requires_valid_window_id(self):
        for window_id in (None, "", "   ", 7, True):
            with self.subTest(window_id=window_id):
                command = ControlCommand(
                    "session-a",
                    7,
                    ControlTarget(1.0, 0.5),
                    window_id=window_id,
                )
                with self.assertRaisesRegex(ValueError, "window_id"):
                    encode_windowed_command(command, self.key)

    def test_decoder_requires_valid_window_even_with_valid_mac(self):
        messages = []
        missing = self._message()
        del missing["window_id"]
        messages.append(missing)

        for window_id in (None, "", "   ", 7, True):
            message = self._message()
            message["window_id"] = window_id
            messages.append(message)

        for message in messages:
            with self.subTest(message=message):
                with self.assertRaisesRegex(ValueError, "window_id"):
                    decode_windowed_command(self._signed_packet(message), self.key)

    def test_valid_mac_does_not_allow_invalid_command_fields(self):
        cases = (
            ("version", True),
            ("version", 2.0),
            ("type", "control_target"),
            ("session_id", ""),
            ("seq", True),
            ("speed_mps", "1.0"),
        )

        for field, value in cases:
            with self.subTest(field=field, value=value):
                message = self._message()
                message[field] = value

                with self.assertRaises(ValueError):
                    decode_windowed_command(self._signed_packet(message), self.key)

    def test_versions_do_not_fall_back(self):
        legacy = ControlCommand("session-a", 7, ControlTarget(1.0, 0.5))

        with self.assertRaisesRegex(ValueError, "version"):
            decode_windowed_command(
                encode_authenticated_command(legacy, self.key), self.key
            )

        with self.assertRaisesRegex(ValueError, "version"):
            decode_authenticated_command(
                encode_windowed_command(self.command, self.key), self.key
            )

    def _command_for_packet_size(self, size):
        base = ControlCommand("s", 0, ControlTarget(1.0, 0.5), window_id="window-a")
        base_size = len(encode_windowed_command(base, self.key))

        return ControlCommand(
            "s" * (1 + size - base_size),
            0,
            base.target,
            window_id="window-a",
        )

    def test_accepts_packet_at_size_limit(self):
        command = self._command_for_packet_size(MAX_CONTROL_MESSAGE_BYTES)
        packet = encode_windowed_command(command, self.key)

        self.assertEqual(len(packet), MAX_CONTROL_MESSAGE_BYTES)
        self.assertEqual(decode_windowed_command(packet, self.key), command)

    def test_encoder_rejects_packet_over_size_limit(self):
        command = self._command_for_packet_size(MAX_CONTROL_MESSAGE_BYTES + 1)

        with self.assertRaisesRegex(ValueError, "too large"):
            encode_windowed_command(command, self.key)

    def test_decoder_checks_size_before_parsing(self):
        with self.assertRaisesRegex(ValueError, "too large"):
            decode_windowed_command(b"{" * (MAX_CONTROL_MESSAGE_BYTES + 1), self.key)
