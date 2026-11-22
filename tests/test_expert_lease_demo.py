from contextlib import redirect_stdout
from io import StringIO
import unittest
from unittest.mock import Mock, patch

from examples import demo_expert_lease as demo
from rover.core.vehicle_model import VehicleState
from rover.protocol.telemetry_codec import TelemetrySnapshot


class SimulatedVehicle:
    session = "session-a"
    key = b"a" * 32

    def __init__(self):
        self.time = 10.0
        self.generation = 0
        self.phase = "idle"
        self.speed = 0.0
        self.events = []
        self.commands = []
        self.expired_generation = None

    def advance(self, duration):
        self.time += duration
        if self.phase in {"held", "controlling"} and self.time >= self.expires_at:
            self.expired_generation = self.generation
            self.events.append(("lease_expired", self.time))
            self.phase = "idle"
            self.speed = 0.0
        if self.phase == "controlling" and self.time - self.last_command_at >= 0.5:
            self.events.append(("watchdog", self.time))
            self.phase = "fault_latched"
            self.speed = 0.0

    def acquire_authority(self, session_id):
        assert session_id == self.session and self.phase == "idle"
        self.generation += 1
        self.expires_at = self.time + 3.0
        self.events.append(("acquired", self.time))
        self.phase = "held"
        return self.reply(generation=self.generation_fields, lease_remaining_s=3.0)

    @property
    def generation_fields(self):
        return {"epoch": "simulated-vehicle", "number": self.generation}

    def reply(self, reason=None, **fields):
        return dict(accepted=reason is None, reason=reason, phase=self.phase, **fields)

    def set_authority_enabled(self, session_id, generation, enabled):
        assert (session_id, generation, enabled) == (self.session, self.generation_fields, True)
        self.phase = "controlling"
        self.last_command_at = self.time
        return self.reply(control_key=self.key.hex())

    def issue_control_window(self, session_id, generation):
        assert session_id == self.session and generation == self.generation_fields
        if self.phase != "controlling":
            return self.reply("not_authority_holder")
        self.window_id = f"window-{self.time}"
        self.window_until = self.time + 0.25
        return self.reply(window_id=self.window_id, lifetime_s=0.25)

    def renew_authority(self, session_id, generation):
        assert session_id == self.session and generation == self.generation_fields
        if self.phase == "idle" and self.expired_generation == generation["number"]:
            self.events.append(("late_renewal", self.time))
            return self.reply("lease_expired")
        assert self.phase == "controlling"
        self.expires_at = self.time + 3.0
        self.events.append(("renewed", self.time))
        return self.reply(lease_remaining_s=3.0)

    def release_authority(self, session_id, generation):
        assert session_id == self.session and generation == self.generation_fields
        self.phase = "idle"
        return self.reply()

    def query_authority(self, session_id):
        assert session_id == self.session
        return self.reply(owns_authority=self.phase in {"held", "controlling"})

    def send(self, sock, command, address, *, key):
        assert key == self.key and command.session_id == self.session
        assert command.target.speed_mps == 1.0
        # The publisher may still use a locally valid window briefly after server expiry.
        if self.phase == "controlling":
            assert command.generation.number == self.generation
            assert command.window_id == self.window_id and self.time < self.window_until
            self.speed = command.target.speed_mps
            self.last_command_at = self.time
        self.commands.append((command.generation.number, command.seq, self.time))

    def snapshot(self, *_):
        return TelemetrySnapshot(VehicleState(speed_mps=self.speed), self.phase == "fault_latched")


class TestExpertLeaseDemo(unittest.TestCase):
    def setUp(self):
        self.vehicle = SimulatedVehicle()
        self.output = StringIO()

    def run_case(self, case):
        vehicle = self.vehicle
        with (
            patch.object(demo, "monotonic", side_effect=lambda: vehicle.time),
            patch.object(demo, "sleep", side_effect=vehicle.advance),
            patch.object(demo, "send_expert_control_command", side_effect=vehicle.send),
            patch.object(demo, "_latest_telemetry", side_effect=vehicle.snapshot),
            redirect_stdout(self.output),
        ):
            case(vehicle, vehicle.session, Mock(), Mock(), ("127.0.0.1", 9999))

    def test_renews_then_keeps_publishing_until_expiry_and_reacquires(self):
        self.run_case(demo._run_lease_case)

        events = dict(self.vehicle.events)
        initial_deadline = 13.0
        self.assertLess(events["renewed"], initial_deadline)
        self.assertGreater(events["lease_expired"], initial_deadline)
        self.assertGreaterEqual(events["late_renewal"], events["lease_expired"])
        self.assertNotIn("watchdog", events)
        send_times = [time for _, _, time in self.vehicle.commands]
        last_live_send = max(time for time in send_times if time < events["lease_expired"])
        self.assertLess(events["lease_expired"] - last_live_send, 0.15)
        self.assertTrue(any(initial_deadline < time < events["lease_expired"] for time in send_times))
        self.assertEqual([seq for _, seq, _ in self.vehicle.commands], list(range(len(send_times))))
        self.assertEqual(self.vehicle.generation, 2)
        self.assertIn("fault_latched=False", self.output.getvalue())
        self.assertIn("lease_expired", self.output.getvalue())

    def test_watchdog_latches_after_motion_stops_but_lease_is_still_valid(self):
        self.run_case(demo._run_watchdog_case)

        events = dict(self.vehicle.events)
        self.assertIn("watchdog", events)
        self.assertNotIn("lease_expired", events)
        self.assertLess(events["watchdog"], self.vehicle.expires_at)
        self.assertAlmostEqual(events["watchdog"] - self.vehicle.commands[-1][2], 0.5, delta=0.06)
        self.assertIn("fault_latched=True", self.output.getvalue())


if __name__ == "__main__":
    unittest.main()
