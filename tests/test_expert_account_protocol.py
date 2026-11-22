import unittest
from unittest.mock import Mock, patch

from rover.desktop.expert_management_client import ExpertManagementClient
from rover.protocol.expert_management_codec import decode_expert_management, encode_expert_management
from rover.protocol.telemetry_registration import encode_telemetry_registration
from rover.vehicle.control_guard import ControlGuardState
from rover.vehicle.expert_management_runtime import ExpertManagementRuntime, PRODUCT_SESSION_LIMIT


class TestExpertAccountProtocol(unittest.TestCase):
    def serve(self, runtime, *messages):
        requests = iter(
            encode_expert_management(kind, request_id, **fields)
            for request_id, (kind, fields) in enumerate(messages, 1)
        )
        replies = []
        guard = ControlGuardState()

        def submit(operation):
            nonlocal guard
            guard, reply = operation(guard, 1.0, 0.0)
            return reply

        with (
            patch.object(runtime, "_submit", side_effect=submit),
            patch("rover.vehicle.expert_management_runtime.iter_records", return_value=requests),
            patch("rover.vehicle.expert_management_runtime.send_record",
                  side_effect=lambda _, payload: replies.append(decode_expert_management(payload))),
        ):
            runtime.serve(Mock(**{"getpeername.return_value": ("127.0.0.1", 45000)}))
        return replies

    def test_registration_through_expert_protocol_is_opt_in(self):
        identity = {"client_id": "observer", "credential": "sample-only-123"}
        legacy = ExpertManagementRuntime({}, 1.0)
        reply, = self.serve(legacy, ("register_account", identity))
        self.assertFalse(reply["accepted"])
        self.assertEqual(reply["reason"], "registration_unavailable")
        accounts = Mock()
        accounts.register.return_value = None
        runtime = ExpertManagementRuntime({}, 1.0, accounts=accounts)
        reply, = self.serve(runtime, ("register_account", {**identity, "role": "operator"}))
        self.assertEqual(reply["type"], "registration_result")
        self.assertFalse(reply["accepted"])
        accounts.register.assert_not_called()
        reply, = self.serve(runtime, ("register_account", identity))
        self.assertTrue(reply["accepted"])
        self.assertIsNone(reply["reason"])
        accounts.register.assert_called_once_with("observer", "sample-only-123")

    def test_observer_cannot_drive_or_receive_transfer(self):
        runtime = ExpertManagementRuntime({}, 1.0)
        guard = ControlGuardState()

        def dispatch(operator, action, role="operator", **fields):
            nonlocal guard
            guard, (_, reply) = runtime._dispatch(
                guard, operator, operator, {"type": action, **fields}, 1.0, 0.0, role=role,
            )
            return reply

        a = dispatch("a", "open_session")["session_id"]
        b = dispatch("b", "open_session", "observer")["session_id"]
        observer_status = dispatch("b", "query_authority", session_id=b)
        self.assertEqual(observer_status["allowed_actions"], [])
        self.assertNotIn("drive_input_supported", observer_status)
        for action in ("acquire_authority", "emergency_stop", "reset_control"):
            self.assertEqual(dispatch("b", action, session_id=b)["reason"], "permission_denied")
        generation = dispatch("a", "acquire_authority", session_id=a)["generation"]
        status = dispatch("a", "query_authority", session_id=a)
        self.assertEqual(status["target_session_ids"], [])
        reply = dispatch(
            "a", "transfer_authority", session_id=a, generation=generation, target_session_id=b,
        )
        self.assertEqual(reply["reason"], "invalid_transfer_target")
        self.assertEqual(runtime.authority.grant.session.session_id, a)

    def test_role_change_closes_previous_authenticated_session(self):
        accounts = Mock()
        accounts.authenticate.return_value = "operator"
        accounts.role.side_effect = ["operator", "observer"]
        runtime = ExpertManagementRuntime({}, 1.0, accounts=accounts)
        replies = self.serve(runtime, ("authenticate", {"client_id": "desk-a", "credential": "secret"}),
                             ("open_session", {}), ("open_session", {}))
        self.assertTrue(replies[0]["accepted"])
        self.assertEqual(replies[1]["type"], "session_opened")
        self.assertEqual(replies[2]["reason"], "account_role_changed")
        runtime._apply_closed_connections(ControlGuardState())
        self.assertEqual(runtime._sessions, {})

    def test_client_registers_and_reports_rejection_reason(self):
        client = ExpertManagementClient(Mock())
        with patch.object(client, "_exchange", return_value={"accepted": False, "reason": "account_exists"}) as call:
            self.assertEqual(client.register_account("desk-a", "sample-only-123"), "account_exists")
        call.assert_called_once_with(
            "register_account", "registration_result", client_id="desk-a", credential="sample-only-123",
        )

    def test_product_registration_fans_out_and_cleans_disconnect(self):
        runtime = ExpertManagementRuntime({}, 1.0, accounts=Mock())
        runtime.configure_telemetry_registration(46000)
        guard = ControlGuardState()

        def dispatch(name, action, *, role="operator", now_s=1.0, **fields):
            nonlocal guard
            guard, (_, reply) = runtime._dispatch(
                guard, name, name, {"type": action, **fields}, now_s, 0.0,
                peer_address=("127.0.0.1", 50001), role=role,
            )
            return reply

        names = ("a", "b", "c", "observer")
        sessions = {name: dispatch(name, "open_session", role=name if name == "observer" else "operator")
                    ["session_id"] for name in names}
        self.assertEqual(dispatch("a", "acquire_authority", session_id=sessions["a"])["reason"],
                         "telemetry_not_registered")
        for index, name in enumerate(names, 1):
            reply = dispatch(name, "prepare_telemetry", session_id=sessions[name])
            self.assertEqual(reply["port"], 46000)
            runtime.register_telemetry(encode_telemetry_registration(reply["token"]), ("127.0.0.1", 41000 + index), 1.1)
            if name == "observer":
                observer = dispatch(name, "query_authority", session_id=sessions[name])
                self.assertEqual(observer["account_role"], name)
                self.assertIs(observer["drive_input_supported"], True)
        waiting = dispatch("unregistered", "open_session")["session_id"]
        self.assertEqual(len(runtime.telemetry_destinations()), 4)
        generation = dispatch("a", "acquire_authority", session_id=sessions["a"])["generation"]
        self.assertEqual(
            dispatch("a", "query_authority", session_id=sessions["a"])["transfer_candidates"],
            [{"session_id": sessions[name], "client_id": name} for name in ("b", "c")],
        )
        self.assertEqual(dispatch("a", "transfer_authority", session_id=sessions["a"],
                                  generation=generation, target_session_id=waiting)["reason"],
                         "invalid_transfer_target")
        self.assertTrue(dispatch("a", "transfer_authority", session_id=sessions["a"],
                                 generation=generation, target_session_id=sessions["c"])["accepted"])
        runtime._closed_connections.append("b")
        guard = runtime._apply_closed_connections(guard)
        runtime.publish_admin_snapshot()
        rows = {row["client_id"]: row for row in runtime.admin_snapshot()}
        self.assertEqual(len(runtime.telemetry_destinations()), 3)
        self.assertFalse(rows["b"]["online"])
        self.assertEqual(rows["b"]["events"], ("登录", "遥测登记", "断开"))
        self.assertEqual(rows["observer"]["telemetry_address"], ("127.0.0.1", 41004))

    def test_product_capacity_returns_explicit_rejection(self):
        runtime = ExpertManagementRuntime({}, 1.0, accounts=Mock())
        guard = ControlGuardState()
        for index in range(PRODUCT_SESSION_LIMIT):
            guard, (kind, _) = runtime._dispatch(guard, "a", str(index), {"type": "open_session"}, 0.0, 0.0)
            self.assertEqual(kind, "session_opened")
        _, reply = runtime._dispatch(guard, "b", "overflow", {"type": "open_session"}, 0.0, 0.0)
        self.assertEqual(reply, ("management_error", {"reason": "capacity_reached"}))
