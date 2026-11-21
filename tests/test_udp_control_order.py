import unittest

from rover.transport.udp_control_order import Delivery, DeliveryRole, FaultMode, PacketOrderPolicy


class TestPacketOrderPolicy(unittest.TestCase):
    def test_duplicate_selection_is_repeatable_for_a_seed(self):
        def selected_indices(seed: int) -> list[int]:
            policy = PacketOrderPolicy[int](FaultMode.DUPLICATE, seed=seed)
            return [
                delivery.packet
                for index in range(1, 11)
                for delivery in policy.receive(index, index / 100)
                if delivery.role == DeliveryRole.DUPLICATE
            ]

        self.assertEqual(selected_indices(42), [2, 8, 10])
        self.assertEqual(selected_indices(42), selected_indices(42))
        self.assertNotEqual(selected_indices(43), selected_indices(42))

    def test_duplicate_immediately_emits_the_same_packet_twice(self):
        packet = (12, b"\x00rover-control\xff", ("127.0.0.1", 12001), 10.0)
        policy = PacketOrderPolicy(FaultMode.DUPLICATE, probability=1)

        original, duplicate = policy.receive(packet, 10.0)

        self.assertIs(original.packet, packet)
        self.assertIs(duplicate.packet, packet)
        self.assertEqual(original.role, DeliveryRole.ORIGINAL)
        self.assertEqual(duplicate.role, DeliveryRole.DUPLICATE)
        self.assertIsNone(policy.deadline_s)

    def test_reorder_emits_next_packet_first_without_drawing_for_it(self):
        policy = PacketOrderPolicy[int](FaultMode.REORDER)
        delivered = [
            (delivery.packet, delivery.role)
            for index in range(1, 11)
            for delivery in policy.receive(index, 10.0 + index / 20)
        ]

        self.assertEqual([packet for packet, _ in delivered], [1, 3, 2, 4, 5, 6, 7, 8, 10, 9])
        self.assertEqual([packet for packet, role in delivered if role == DeliveryRole.REORDERED], [2, 9])
        self.assertIsNone(policy.deadline_s)

    def test_held_packet_is_released_once_at_idle_deadline(self):
        now_s = 1_000_000.0
        policy = PacketOrderPolicy[bytes](FaultMode.REORDER, probability=1)

        self.assertEqual(policy.receive(b"held", now_s), ())
        deadline = now_s + 0.12
        self.assertEqual(policy.deadline_s, deadline)
        self.assertEqual(policy.flush_due(deadline - 0.001), ())
        self.assertEqual(policy.flush_due(deadline), (Delivery(b"held", DeliveryRole.HOLD_EXPIRED),))
        self.assertIsNone(policy.deadline_s)
        self.assertEqual(policy.flush_due(deadline + 0.01), ())

    def test_arrival_at_deadline_releases_old_packet_and_draws_for_new_packet(self):
        policy = PacketOrderPolicy[bytes](FaultMode.REORDER, probability=1, hold_s=0.1)

        self.assertEqual(policy.receive(b"first", 10.0), ())
        self.assertEqual(policy.receive(b"second", 10.1), (Delivery(b"first", DeliveryRole.HOLD_EXPIRED),))
        self.assertEqual(policy.deadline_s, 10.1 + 0.1)
        self.assertEqual(policy.receive(b"third", 10.15), (
            Delivery(b"third", DeliveryRole.ORIGINAL),
            Delivery(b"second", DeliveryRole.REORDERED),
        ))
        self.assertIsNone(policy.deadline_s)


if __name__ == "__main__":
    unittest.main()
