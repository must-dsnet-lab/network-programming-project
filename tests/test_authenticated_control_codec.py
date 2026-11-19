import json
import unittest

from rover.core.control_target import ControlTarget
from rover.protocol.control_codec import (
    MAX_CONTROL_MESSAGE_BYTES,
    ControlCommand,
    decode_authenticated_command,
    encode_authenticated_command,
    encode_command,
)


class TestAuthenticatedControlCodec(unittest.TestCase):
    key = b"test-session-key"
    command = ControlCommand(
        session_id="session-a",
        seq=7,
        target=ControlTarget(speed_mps=1.0, heading_rad=0.5),
    )

    def test_wire_format_and_round_trip(self):
        packet = encode_authenticated_command(self.command, self.key)
        message = json.loads(packet)

        self.assertEqual(message["type"], "authenticated_control_command")
        self.assertEqual(message["version"], 1)
        self.assertEqual(message["session_id"], "session-a")
        self.assertEqual(message["seq"], 7)
        self.assertEqual(message["speed_mps"], 1.0)
        self.assertEqual(message["heading_rad"], 0.5)
        self.assertIsInstance(message["mac"], str)
        self.assertEqual(len(message["mac"]), 64)
        self.assertEqual(
            decode_authenticated_command(packet, self.key),
            self.command,
        )

    def test_accepts_different_json_layout(self):
        packet = encode_authenticated_command(self.command, self.key)
        message = json.loads(packet)
        reordered = {name: message[name] for name in reversed(message)}
        reformatted = json.dumps(reordered, indent=2).encode("utf-8")

        self.assertEqual(
            decode_authenticated_command(reformatted, self.key),
            self.command,
        )

    def test_rejects_tampering_with_any_control_field(self):
        packet = encode_authenticated_command(self.command, self.key)
        original = json.loads(packet)
        cases = (
            ("version", 2),
            ("type", "control_target"),
            ("session_id", "session-other"),
            ("seq", 99),
            ("speed_mps", 9.0),
            ("heading_rad", -1.0),
        )

        for field, value in cases:
            with self.subTest(field=field):
                changed = original.copy()
                changed[field] = value
                tampered = json.dumps(changed).encode("utf-8")

                with self.assertRaisesRegex(ValueError, "MAC"):
                    decode_authenticated_command(tampered, self.key)

    def test_rejects_wrong_key(self):
        packet = encode_authenticated_command(self.command, self.key)

        with self.assertRaisesRegex(ValueError, "MAC"):
            decode_authenticated_command(packet, b"wrong-key")

    def test_rejects_missing_or_invalid_mac(self):
        packet = encode_authenticated_command(self.command, self.key)
        original = json.loads(packet)

        for mac in (None, 7, "", "0" * 64, "中" * 64):
            with self.subTest(mac=mac):
                changed = original.copy()
                if mac is None:
                    changed.pop("mac", None)
                else:
                    changed["mac"] = mac
                invalid = json.dumps(changed).encode("utf-8")

                with self.assertRaisesRegex(ValueError, "MAC"):
                    decode_authenticated_command(invalid, self.key)

    def test_rejects_unsigned_command(self):
        packet = encode_command(self.command)

        with self.assertRaisesRegex(ValueError, "MAC"):
            decode_authenticated_command(packet, self.key)

    def _command_for_packet_size(self, size):
        base = ControlCommand("s", 0, ControlTarget(1.0, 0.5))
        base_size = len(encode_authenticated_command(base, self.key))

        return ControlCommand(
            "s" * (1 + size - base_size),
            0,
            base.target,
        )

    def test_accepts_packet_at_size_limit(self):
        command = self._command_for_packet_size(MAX_CONTROL_MESSAGE_BYTES)
        packet = encode_authenticated_command(command, self.key)

        self.assertEqual(len(packet), MAX_CONTROL_MESSAGE_BYTES)
        self.assertEqual(
            decode_authenticated_command(packet, self.key),
            command,
        )

    def test_encoder_rejects_packet_over_size_limit(self):
        command = self._command_for_packet_size(MAX_CONTROL_MESSAGE_BYTES + 1)

        with self.assertRaisesRegex(ValueError, "too large"):
            encode_authenticated_command(command, self.key)

    def test_decoder_checks_size_before_parsing(self):
        packet = b"{" * (MAX_CONTROL_MESSAGE_BYTES + 1)

        with self.assertRaisesRegex(ValueError, "too large"):
            decode_authenticated_command(packet, self.key)
