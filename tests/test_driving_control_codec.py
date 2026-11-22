import json
import unittest

from rover.core.control_target import ControlTarget
from rover.protocol.control_auth import compute_control_mac
from rover.protocol.control_codec import (
    AuthorityGeneration,
    ControlCommand,
    ControlMacVerificationError,
    decode_authenticated_command,
    decode_command,
    decode_expert_command,
    decode_target,
    decode_windowed_command,
    encode_authenticated_command,
    encode_command,
    encode_expert_command,
    encode_target,
    encode_windowed_command,
)


KEY = b"driving-codec-test-key"
INPUT_FIELDS = {"throttle": 0.8, "brake": 0.25, "steering": -0.4}


def _target_packet(**fields):
    return json.dumps({"version": 1, "type": "control_target", **fields}).encode()


def _canonical(message):
    return json.dumps(message, sort_keys=True, separators=(",", ":")).encode()


class TestDrivingControlCodec(unittest.TestCase):
    def test_plain_driving_target_round_trips_with_an_explicit_field(self):
        decoded = decode_target(_target_packet(drive_input=INPUT_FIELDS))
        from rover.core.driving import DriveInput

        self.assertEqual(decoded, DriveInput(**INPUT_FIELDS))
        self.assertEqual(json.loads(encode_target(decoded))["drive_input"], INPUT_FIELDS)

    def test_driving_target_round_trips_through_all_command_envelopes(self):
        from rover.core.driving import DriveInput

        drive = DriveInput(**INPUT_FIELDS)
        for encode, decode, window, generation, key_args in (
            (encode_command, decode_command, None, None, ()),
            (encode_authenticated_command, decode_authenticated_command, None, None, (KEY,)),
            (encode_windowed_command, decode_windowed_command, "window", None, (KEY,)),
            (encode_expert_command, decode_expert_command, "window", AuthorityGeneration("epoch", 2), (KEY,)),
        ):
            with self.subTest(encoder=encode.__name__):
                command = ControlCommand("session", 15, drive, window, generation)
                packet = encode(command, *key_args)
                fields = json.loads(packet)
                self.assertEqual(fields["drive_input"], INPUT_FIELDS)
                self.assertNotIn("speed_mps", fields)
                self.assertNotIn("heading_rad", fields)
                self.assertEqual(decode(packet, *key_args), command)

    def test_invalid_or_ambiguous_driving_fields_are_rejected(self):
        for fields in (
            {"drive_input": None},
            {"drive_input": []},
            {"drive_input": {"throttle": 1.0, "brake": 0.0}},
            {"drive_input": {**INPUT_FIELDS, "speed_mps": 1}},
            {"drive_input": {**INPUT_FIELDS, "throttle": True}},
            {"drive_input": {**INPUT_FIELDS, "throttle": 1.1}},
            {"drive_input": {**INPUT_FIELDS, "brake": -0.1}},
            {"drive_input": {**INPUT_FIELDS, "steering": -1.1}},
            {"drive_input": {**INPUT_FIELDS, "brake": float("nan")}},
            {"drive_input": INPUT_FIELDS, "speed_mps": 0.0},
            {"drive_input": INPUT_FIELDS, "heading_rad": 0.0},
            {"drive_input": INPUT_FIELDS, "brake": 0.0},
            {"speed_mps": 0.0, "heading_rad": 0.0, "steering": 0.0},
        ):
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                decode_target(_target_packet(**fields))

    def test_duplicate_driving_fields_are_rejected(self):
        for body in (
            '"drive_input":{"throttle":0.5,"brake":0,"steering":0,"steering":1}',
            '"drive_input":null,"drive_input":{"throttle":0.5,"brake":0,"steering":0}',
        ):
            packet = ('{"version":1,"type":"control_target",' + body + '}').encode()
            with self.subTest(body=body), self.assertRaisesRegex(ValueError, "duplicate"):
                decode_target(packet)

    def test_expert_signature_covers_driving_fields_and_signed_mixed_shape_is_rejected(self):
        from rover.core.driving import DriveInput

        command = ControlCommand("session", 7, DriveInput(**INPUT_FIELDS), "window", AuthorityGeneration("epoch", 2))
        message = json.loads(encode_expert_command(command, KEY))
        message["drive_input"]["steering"] = 0.0
        with self.assertRaises(ControlMacVerificationError):
            decode_expert_command(_canonical(message), KEY)

        message.pop("mac")
        message["speed_mps"] = 0.0
        message["mac"] = compute_control_mac(_canonical(message), KEY)
        with self.assertRaisesRegex(ValueError, "mixed"):
            decode_expert_command(_canonical(message), KEY)

    def test_legacy_numeric_wire_bytes_keep_the_existing_field_order(self):
        target = ControlTarget(1.0, 0.5)
        self.assertEqual(
            encode_target(target),
            b'{"version": 1, "type": "control_target", "speed_mps": 1.0, "heading_rad": 0.5}',
        )
        self.assertEqual(
            encode_command(ControlCommand("a", 0, target)),
            b'{"version": 1, "type": "control_command", "session_id": "a", "seq": 0, '
            b'"speed_mps": 1.0, "heading_rad": 0.5}',
        )
