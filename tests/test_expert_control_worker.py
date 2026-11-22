from contextlib import nullcontext
from threading import Event
from time import monotonic, sleep
import unittest
from unittest.mock import MagicMock, patch

from rover.core.control_target import ControlTarget
from rover.desktop import expert_control_worker as module
from rover.protocol.control_codec import AuthorityGeneration


class FakeExpertClient:
    def __init__(self):
        self.phase = "idle"
        self.holder = None
        self.number = 0
        self.remaining = 3.0
        self.result = None
        self.requests = []
        self.window_waiting = Event()
        self.window_release = Event()
        self.window_release.set()
        self.window_denial = None

    def generation(self):
        return {"epoch": "test-run", "number": self.number}

    def query_authority(self, session_id):
        owns = self.holder == "desktop-a"
        actions = ["acquire_authority"] if self.phase == "idle" else []
        if owns:
            actions = ["renew_authority", "release_authority", "transfer_authority",
                       "set_authority_enabled", "emergency_stop"]
        return {
            "phase": self.phase, "owns_authority": owns, "holder_client_id": self.holder,
            "authority_generation": self.generation() if self.holder is not None else None,
            "allowed_actions": actions, "last_authority_result": self.result,
            **({"generation": self.generation(), "lease_remaining_s": self.remaining,
                "target_session_ids": ["session-b"]} if owns else {}),
        }

    def open_session(self):
        return "session-a"

    def record(self, action, *args):
        self.requests.append((action, *args))
        self.result = {"action": action, "accepted": True, "reason": None}
        return {"phase": self.phase, "accepted": True, "reason": None}

    def acquire_authority(self, session_id):
        self.number += 1
        self.holder = "desktop-a"
        self.phase = "held"
        return {**self.record("acquire_authority", session_id), "generation": self.generation()}

    def renew_authority(self, session_id, generation):
        self.remaining = 3.0
        return {**self.record("renew_authority", session_id, generation), "lease_remaining_s": 3.0}

    def transfer_authority(self, session_id, generation, target_session_id):
        self.number += 1
        self.holder = "desktop-b"
        self.phase = "held"
        return self.record("transfer_authority", session_id, generation, target_session_id)

    def set_authority_enabled(self, session_id, generation, enabled):
        self.phase = "controlling"
        return {**self.record("set_authority_enabled", session_id, generation, enabled),
                "control_key": "01" * 32}

    def issue_control_window(self, session_id, generation):
        self.window_waiting.set()
        if not self.window_release.wait(timeout=1.0):
            raise TimeoutError("test window reply timed out")
        if self.window_denial is not None:
            return {"accepted": False, "reason": self.window_denial}
        return {"accepted": True, "window_id": "current-window", "lifetime_s": 0.25}


class TestExpertControlWorker(unittest.TestCase):
    def setup_worker(self, client=None):
        self.client = client or FakeExpertClient()
        self.udp = MagicMock()
        self.udp.__enter__.return_value = self.udp
        self.send = self.enterContext(patch.object(module, "send_expert_control_command"))
        self.enterContext(patch.object(module.socket, "socket", return_value=self.udp))
        self.enterContext(patch.object(module, "AUTHORITY_POLL_S", 0.01))
        self.worker = module.ExpertControlWorker(lambda: nullcontext(self.client), ("127.0.0.1", 9999))
        self.addCleanup(self.close_worker)
        self.worker.start()

    def close_worker(self):
        self.client.window_release.set()
        self.worker.close()
        self.assertTrue(self.worker.wait_closed(timeout=1.0))

    def wait_until(self, condition):
        deadline = monotonic() + 1.0
        while not condition():
            if monotonic() >= deadline:
                self.fail(f"worker did not reach expected state: {self.worker.status()}")
            sleep(0.003)

    def acquire(self):
        self.wait_until(lambda: self.worker.status().authority is not None)
        self.assertTrue(self.worker.acquire_authority())
        self.wait_until(lambda: self.worker.status().authority.owns_authority
                        and not self.worker.status().pending)

    def test_authority_actions_use_published_generation_and_require_explicit_apply(self):
        self.setup_worker()
        self.assertFalse(self.worker.apply_target(ControlTarget(1.0, 0.0)))
        self.acquire()
        self.send.assert_not_called()
        self.assertTrue(self.worker.apply_target(ControlTarget(1.0, 0.0)))
        self.wait_until(lambda: self.send.call_count >= 2)
        commands = [call.args[1] for call in self.send.call_args_list]
        self.assertEqual([item.seq for item in commands[:2]], [0, 1])
        self.assertEqual(commands[0].generation, AuthorityGeneration("test-run", 1))
        self.assertTrue(self.worker.renew_authority())
        self.wait_until(lambda: self.worker.status().authority.last_result.action == "renew_authority"
                        and not self.worker.status().pending)
        self.assertIn(("renew_authority", "session-a", {"epoch": "test-run", "number": 1}),
                      self.client.requests)
        self.assertTrue(self.worker.transfer_authority())
        self.wait_until(lambda: self.worker.status().authority.holder_client_id == "desktop-b")
        sends = self.send.call_count
        self.assertFalse(self.worker.apply_target(ControlTarget(1.0, 0.0)))
        sleep(0.08)
        self.assertEqual(self.send.call_count, sends)
        self.assertIn(("transfer_authority", "session-a", {"epoch": "test-run", "number": 1}, "session-b"),
                      self.client.requests)

    def test_loss_and_new_generation_never_resume_previous_motion(self):
        self.setup_worker()
        self.acquire()
        self.assertTrue(self.worker.apply_target(ControlTarget(1.0, 0.0)))
        self.wait_until(lambda: self.send.called)
        self.client.holder = None
        self.client.phase = "idle"
        self.wait_until(lambda: not self.worker.status().authority.owns_authority)
        count = self.send.call_count
        self.acquire()
        sleep(0.08)
        self.assertEqual(self.send.call_count, count)
        self.assertTrue(self.worker.apply_target(ControlTarget(0.5, 0.0)))
        self.wait_until(lambda: self.send.call_count > count)
        packet = self.send.call_args.args[1]
        self.assertEqual(packet.generation.number, 2)
        self.assertEqual(packet.seq, 0)

    def test_stop_cancels_first_datagram_while_tls_window_is_pending(self):
        self.setup_worker()
        self.acquire()
        self.client.window_release.clear()
        self.assertTrue(self.worker.apply_target(ControlTarget(1.0, 0.0)))
        self.assertTrue(self.client.window_waiting.wait(timeout=1.0))
        self.worker.stop_sending()
        self.client.window_release.set()
        sleep(0.08)
        self.send.assert_not_called()

    def test_auto_renew_is_opt_in_and_only_runs_while_moving(self):
        self.setup_worker()
        self.acquire()
        self.client.remaining = 0.8
        self.wait_until(lambda: self.worker.status().authority.lease_remaining_s == 0.8)
        self.assertTrue(self.worker.apply_target(ControlTarget(1.0, 0.0)))
        self.wait_until(lambda: self.send.call_count >= 2)
        self.assertFalse(any(request[0] == "renew_authority" for request in self.client.requests))
        self.worker.stop_sending()
        self.worker.set_auto_renew(True)
        sleep(0.08)
        self.assertFalse(any(request[0] == "renew_authority" for request in self.client.requests))
        count = self.send.call_count
        self.assertTrue(self.worker.apply_target(ControlTarget(0.0, 0.0)))
        self.wait_until(lambda: self.send.call_count >= count + 2)
        self.assertFalse(any(request[0] == "renew_authority" for request in self.client.requests))
        self.assertTrue(self.worker.apply_target(ControlTarget(1.0, 0.0)))
        self.wait_until(lambda: any(request[0] == "renew_authority" for request in self.client.requests))

    def test_previous_status_contract_fails_closed(self):
        client = FakeExpertClient()
        client.query_authority = lambda _: {"phase": "idle", "owns_authority": False}
        self.setup_worker(client)
        self.wait_until(lambda: self.worker.status().state == "failed")
        self.assertFalse(self.worker.acquire_authority())
        self.assertFalse(self.worker.apply_target(ControlTarget(1.0, 0.0)))

    def test_window_denial_feedback_survives_status_poll_and_allows_retry(self):
        self.setup_worker()
        self.acquire()
        self.client.window_denial = "session_disabled"
        self.assertTrue(self.worker.apply_target(ControlTarget(1.0, 0.0)))
        self.wait_until(lambda: self.worker.status().state == "stopped")
        sleep(0.04)
        self.assertEqual(self.worker.status().detail, "session_disabled")
        self.send.assert_not_called()
        self.client.window_denial = None
        self.assertTrue(self.worker.apply_target(ControlTarget(1.0, 0.0)))
        self.wait_until(lambda: self.worker.status().state == "sending")
        self.assertEqual(self.worker.status().detail, "")

    def test_automatic_renewal_can_be_cancelled_before_dispatch(self):
        self.setup_worker()
        self.acquire()
        original_queue = self.worker._queue_action
        queued = Event()

        def disable_after_queue(name, **options):
            accepted = original_queue(name, **options)
            if accepted and name == "renew_authority":
                self.worker.set_auto_renew(False)
                queued.set()
            return accepted

        self.enterContext(patch.object(self.worker, "_queue_action", side_effect=disable_after_queue))
        self.client.remaining = 0.8
        self.worker.set_auto_renew(True)
        self.assertTrue(self.worker.apply_target(ControlTarget(1.0, 0.0)))
        self.assertTrue(queued.wait(timeout=1.0))
        self.wait_until(lambda: not self.worker.status().pending)
        self.assertNotIn("renew_authority", [request[0] for request in self.client.requests])
