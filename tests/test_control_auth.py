import unittest

from rover.protocol.control_auth import (
    compute_control_mac,
    verify_control_mac,
)

class TestControlAuth(unittest.TestCase):
    def test_matches_standard_hmac_sha256_vector(self):
        expected = "b0344c61d8db38535ca8afceaf0bf12b" "881dc200c9833da726e9376c2e32cff7"

        self.assertEqual(
            compute_control_mac(b"Hi There", b"\x0b" * 20),
            expected,
        )

    def test_rejects_empty_key(self):
        with self.assertRaisesRegex(ValueError, "key"):
            compute_control_mac(b"hello", b"")


class TestControlMacVerification(unittest.TestCase):
    payload = b"Hi There"
    key = b"\x0b" * 20
    mac = "b0344c61d8db38535ca8afceaf0bf12b" "881dc200c9833da726e9376c2e32cff7"

    def test_accepts_matching_mac(self):
        self.assertTrue(verify_control_mac(self.payload, self.key, self.mac))

    def test_rejects_changed_content_key_or_mac(self):
        cases = (
            ("payload", b"Hi There!", self.key, self.mac),
            ("key", self.payload, b"different-key", self.mac),
            ("mac", self.payload, self.key, "0" * 64),
        )

        for label, payload, key, mac in cases:
            with self.subTest(case=label):
                self.assertFalse(verify_control_mac(payload, key, mac))

    def test_rejects_malformed_macs(self):
        cases = (
            "",
            None,
            7,
            b"abc",
            "0" * 63,
            "0" * 65,
            "g" * 64,
            "中" * 64,
        )

        for mac in cases:
            with self.subTest(mac=mac):
                self.assertFalse(verify_control_mac(self.payload, self.key, mac))

    def test_rejects_empty_key(self):
        with self.assertRaisesRegex(ValueError, "key"):
            verify_control_mac(self.payload, b"", self.mac)
