import unittest

from rover.protocol.telemetry_registration import (
    REGISTRATION_DATAGRAM_SIZE,
    decode_telemetry_registration,
    encode_telemetry_registration,
    is_telemetry_registration,
)
from rover.vehicle.telemetry_registration import TelemetryRegistry


class TestTelemetryRegistrationCodec(unittest.TestCase):
    def test_codec_maps_tls_token_to_fixed_size_versioned_datagram(self):
        token = '01' * 32
        packet = encode_telemetry_registration(token)

        self.assertEqual(packet, b'RVTR\x01' + bytes.fromhex(token))
        self.assertEqual(len(packet), REGISTRATION_DATAGRAM_SIZE)
        self.assertTrue(is_telemetry_registration(packet))
        self.assertEqual(decode_telemetry_registration(packet), token)

    def test_codec_rejects_malformed_tokens_and_datagrams(self):
        for token in ('', 'aa' * 31, 'aa' * 33, 'gg' * 32, 'AA' * 32, 'aa ' * 32):
            with self.subTest(token=token), self.assertRaises(ValueError):
                encode_telemetry_registration(token)

        token = '12' * 32
        packet = encode_telemetry_registration(token)
        for malformed in (b'', packet[:-1], packet + b'\0', b'RVTR\x02' + packet[5:]):
            with self.subTest(malformed=malformed), self.assertRaises(ValueError):
                decode_telemetry_registration(malformed)
        self.assertTrue(is_telemetry_registration(b'RVTR'))
        self.assertFalse(is_telemetry_registration(b'{"type":"control_command"}'))


class TestTelemetryRegistry(unittest.TestCase):
    def setUp(self):
        self.registry = TelemetryRegistry(lifetime_s=5.0)
        self.desktop_a = ('127.0.0.1', 41001)
        self.desktop_b = ('127.0.0.1', 41002)

    def test_registration_records_observed_address_and_consumes_token(self):
        token = self.registry.issue('session-a', now_s=10.0, expected_ip='127.0.0.1')
        packet = encode_telemetry_registration(token)
        self.assertEqual(self.registry.destinations(), ())
        self.assertEqual(self.registry.accept(packet, self.desktop_a, now_s=11.0), 'session-a')
        self.assertEqual(self.registry.destination('session-a'), self.desktop_a)

        self.assertIsNone(self.registry.accept(packet, self.desktop_b, now_s=11.5))
        self.assertEqual(self.registry.destinations(), (self.desktop_a,))

    def test_other_source_or_invalid_datagram_preserves_the_pending_registration(self):
        token = self.registry.issue('session-a', now_s=10.0, expected_ip='127.0.0.1')
        packet = encode_telemetry_registration(token)
        for source in (('127.0.0.2', 41001), ('127.0.0.1', 0), ('127.0.0.1', True),
                       ('0.0.0.0', 41001), ('224.0.0.5', 41001), ('255.255.255.255', 41001)):
            with self.subTest(source=source):
                self.assertIsNone(self.registry.accept(packet, source, now_s=11.0))
        self.assertIsNone(self.registry.accept(packet[:-1], self.desktop_a, now_s=11.0))
        self.assertIsNone(self.registry.accept(encode_telemetry_registration('aa' * 32),
                                              self.desktop_a, now_s=11.0))
        self.assertEqual(self.registry.accept(packet, self.desktop_a, now_s=11.0), 'session-a')

    def test_registration_expiry_is_exclusive(self):
        packet = encode_telemetry_registration(self.registry.issue('session-a', now_s=10.0))

        self.assertIsNone(self.registry.accept(packet, self.desktop_a, now_s=15.0))
        self.assertIsNone(self.registry.destination('session-a'))

    def test_reissue_invalidates_previous_pending_token_without_dropping_active_destination(self):
        initial = encode_telemetry_registration(self.registry.issue('session-a', now_s=10.0))
        self.registry.accept(initial, self.desktop_a, now_s=10.5)
        superseded = encode_telemetry_registration(self.registry.issue('session-a', now_s=11.0))
        replacement = encode_telemetry_registration(self.registry.issue('session-a', now_s=12.0))

        self.assertIsNone(self.registry.accept(superseded, self.desktop_b, now_s=13.0))
        self.assertEqual(self.registry.destination('session-a'), self.desktop_a)
        self.assertEqual(self.registry.accept(replacement, self.desktop_b, now_s=13.0), 'session-a')
        self.assertEqual(self.registry.destinations(), (self.desktop_b,))

    def test_disconnect_clears_pending_and_active_state_for_only_that_session(self):
        first = encode_telemetry_registration(self.registry.issue('session-a', now_s=10.0))
        other = encode_telemetry_registration(self.registry.issue('session-b', now_s=10.0))
        self.registry.accept(first, self.desktop_a, now_s=11.0)
        self.registry.accept(other, self.desktop_b, now_s=11.0)
        pending = encode_telemetry_registration(self.registry.issue('session-a', now_s=11.0))
        self.assertEqual(self.registry.destinations(), (self.desktop_a, self.desktop_b))

        self.registry.disconnect('session-a')
        self.registry.disconnect('session-a')
        self.assertIsNone(self.registry.destination('session-a'))
        self.assertIsNone(self.registry.accept(pending, self.desktop_a, now_s=12.0))
        self.assertEqual(self.registry.destinations(), (self.desktop_b,))

    def test_shared_destination_is_sent_one_copy_until_last_session_disconnects(self):
        for session in ('session-a', 'session-b'):
            packet = encode_telemetry_registration(self.registry.issue(session, now_s=10.0))
            self.registry.accept(packet, self.desktop_a, now_s=11.0)

        self.assertEqual(self.registry.destinations(), (self.desktop_a,))
        self.registry.disconnect('session-a')
        self.assertEqual(self.registry.destinations(), (self.desktop_a,))
        self.registry.disconnect('session-b')
        self.assertEqual(self.registry.destinations(), ())
