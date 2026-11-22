from dataclasses import replace
import hmac
import json
import socket
import unittest
from unittest.mock import Mock, patch

from rover.core.control_target import ControlTarget
from rover.protocol import control_codec
from rover.transport import udp_transport


class TestExpertControlCodec(unittest.TestCase):
    key = b"test-expert-control-key"

    def make_command(self):
        generation = control_codec.AuthorityGeneration("test-epoch", 3)
        return control_codec.ControlCommand(
            "session-a", 0, ControlTarget(1.0, 0.5), "window-a", generation
        )

    def test_version_three_round_trip_preserves_authenticated_generation(self):
        command = self.make_command()
        packet = control_codec.encode_expert_command(command, self.key)
        message = json.loads(packet)

        self.assertEqual(message["version"], 3)
        self.assertEqual(message["generation"], {"epoch": "test-epoch", "number": 3})
        mac = message.pop("mac")
        payload = self.encode_fields(message)
        self.assertEqual(mac, hmac.new(self.key, payload, "sha256").hexdigest())
        self.assertEqual(control_codec.decode_expert_command(packet, self.key), command)

    def test_generation_field_changes_fail_authentication(self):
        packet = control_codec.encode_expert_command(self.make_command(), self.key)

        for field, value in (("epoch", "other-epoch"), ("number", 4)):
            with self.subTest(field=field):
                message = json.loads(packet)
                message["generation"][field] = value
                with self.assertRaises(control_codec.ControlMacVerificationError):
                    control_codec.decode_expert_command(
                        self.encode_fields(message), self.key
                    )

    def test_encoder_requires_a_valid_generation(self):
        command = self.make_command()
        for generation in (
            None,
            control_codec.AuthorityGeneration(" ", 3),
            control_codec.AuthorityGeneration("test-epoch", 0),
            control_codec.AuthorityGeneration("test-epoch", True),
        ):
            with self.subTest(generation=generation):
                with self.assertRaisesRegex(ValueError, "generation"):
                    control_codec.encode_expert_command(
                        replace(command, generation=generation), self.key
                    )

    def test_decoder_requires_a_valid_generation_even_with_a_valid_mac(self):
        packet = control_codec.encode_expert_command(self.make_command(), self.key)
        fields = json.loads(packet)
        fields.pop("mac")
        for generation in (
            None,
            "wrong-type",
            {},
            {"epoch": "test-epoch"},
            {"number": 3},
            {"epoch": 1, "number": 3},
            {"epoch": " ", "number": 3},
            {"epoch": "test-epoch", "number": -1},
            {"epoch": "test-epoch", "number": 3.0},
            {"epoch": "test-epoch", "number": True},
        ):
            with self.subTest(generation=generation):
                message = {**fields, "generation": generation}
                if generation is None:
                    del message["generation"]
                payload = self.encode_fields(message)
                message["mac"] = hmac.new(self.key, payload, "sha256").hexdigest()
                with self.assertRaisesRegex(ValueError, "generation"):
                    control_codec.decode_expert_command(
                        self.encode_fields(message), self.key
                    )

    def test_decoders_do_not_fall_back_between_versions(self):
        command = self.make_command()
        version_two = control_codec.encode_windowed_command(
            replace(command, generation=None), self.key
        )
        version_three = control_codec.encode_expert_command(command, self.key)

        with self.assertRaises(control_codec.ControlProtocolVersionError):
            control_codec.decode_expert_command(version_two, self.key)
        with self.assertRaisesRegex(ValueError, "version"):
            control_codec.decode_windowed_command(version_three, self.key)

    def test_json_recursion_errors_are_reported_as_invalid_packets(self):
        command = self.make_command()
        versions = (
            (control_codec.encode_expert_command, control_codec.decode_expert_command),
            (control_codec.encode_windowed_command, control_codec.decode_windowed_command),
        )
        json_steps = (
            (control_codec.json, "loads"),
            (control_codec, "_encode_control_fields"),
        )
        for encode, decode in versions:
            packet = encode(command, self.key)
            for module, name in json_steps:
                with self.subTest(decode=decode.__name__, step=name):
                    with patch.object(module, name, side_effect=RecursionError):
                        with self.assertRaisesRegex(ValueError, "too deeply nested"):
                            decode(packet, self.key)

    def test_transport_preserves_generation_and_peer(self):
        command = self.make_command()
        sender = Mock(spec=socket.socket)
        receiver = Mock(spec=socket.socket)
        destination = ("127.0.0.1", 9999)
        peer = ("127.0.0.1", 12345)

        udp_transport.send_expert_control_command(
            sender, command, destination, key=self.key
        )
        packet, actual_destination = sender.sendto.call_args.args
        receiver.recvfrom.return_value = (packet, peer)

        self.assertEqual(actual_destination, destination)
        self.assertEqual(
            udp_transport.try_receive_expert_control_command(receiver, key=self.key),
            (command, peer),
        )

    def test_transport_checks_key_only_after_receiving_a_datagram(self):
        receiver = Mock(spec=socket.socket)
        receiver.recvfrom.side_effect = BlockingIOError
        self.assertIsNone(
            udp_transport.try_receive_expert_control_command(receiver, key=None)
        )

        receiver.recvfrom.side_effect = None
        receiver.recvfrom.return_value = (b"datagram", ("127.0.0.1", 12345))
        with self.assertRaises(udp_transport.NoActiveControlKeyError):
            udp_transport.try_receive_expert_control_command(receiver, key=None)

    @staticmethod
    def encode_fields(message):
        return json.dumps(
            message, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
