"""Publish expert authority snapshots and serialize desktop TLS and UDP operations."""

import socket
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass, replace
from threading import Condition, Thread
from time import monotonic

from rover.core.control_target import ControlTarget
from rover.desktop.control_worker import SEND_INTERVAL_S, WorkerStatus
from rover.desktop.expert_management_client import ExpertManagementClient
from rover.desktop.secure_control import ControlRequestRejected
from rover.protocol.control_codec import AuthorityGeneration, ControlCommand
from rover.transport.udp_transport import send_expert_control_command


AUTHORITY_POLL_S = 0.2
AUTO_RENEW_MARGIN_S = 1.25


@dataclass(frozen=True)
class ExpertAuthorityResult:
    action: str
    accepted: bool
    reason: str | None


@dataclass(frozen=True)
class ExpertAuthorityStatus:
    phase: str
    holder_client_id: str | None
    generation: AuthorityGeneration | None
    owns_authority: bool
    lease_remaining_s: float | None
    allowed_actions: frozenset[str]
    last_result: ExpertAuthorityResult | None


@dataclass(frozen=True)
class ExpertWorkerStatus(WorkerStatus):
    authority: ExpertAuthorityStatus | None = None
    pending: bool = False


@dataclass(frozen=True)
class _Action:
    name: str
    generation: AuthorityGeneration | None
    recipient: str | None = None
    automatic: bool = False


def _generation_fields(generation: AuthorityGeneration) -> dict[str, str | int]:
    return {"epoch": generation.epoch, "number": generation.number}


def _authority_status(reply: dict[str, object]) -> ExpertAuthorityStatus:
    required = ("holder_client_id", "authority_generation", "allowed_actions", "last_authority_result")
    if any(field not in reply for field in required):
        raise ValueError("车端缺少 P6 控制权状态字段")
    generation = reply["authority_generation"]
    owns = reply["owns_authority"]
    if owns and (generation is None or generation != reply.get("generation")
                 or reply.get("lease_remaining_s") is None):
        raise ValueError("车端控制权与租约状态不完整")
    result = reply["last_authority_result"]
    return ExpertAuthorityStatus(
        phase=reply["phase"],
        holder_client_id=reply["holder_client_id"],
        generation=AuthorityGeneration(**generation) if generation is not None else None,
        owns_authority=owns,
        lease_remaining_s=reply.get("lease_remaining_s"),
        allowed_actions=frozenset(reply["allowed_actions"]),
        last_result=(ExpertAuthorityResult(result["action"], result["accepted"], result["reason"])
                     if result is not None else None),
    )


class _ExpertMotion:
    """Sequence and window belong to a single grant and enable context."""

    def __init__(self) -> None:
        self.clear()

    def clear(self) -> None:
        self.generation: AuthorityGeneration | None = None
        self.key = b""
        self.seq = 0
        self.window_id: str | None = None
        self.window_renew_at_s = 0.0
        self.window_expires_at_s = 0.0

    def prepare(
        self, client: ExpertManagementClient, session_id: str,
        generation: AuthorityGeneration, target: ControlTarget,
    ) -> ControlCommand:
        fields = _generation_fields(generation)
        if self.generation != generation:
            self.clear()
            enabled = client.set_authority_enabled(session_id, fields, True)
            if not enabled["accepted"]:
                raise ControlRequestRejected(str(enabled["reason"]))
            self.key = bytes.fromhex(enabled["control_key"])
            self.generation = generation

        requested_at_s = monotonic()
        if requested_at_s >= self.window_renew_at_s or self.window_id is None:
            window = client.issue_control_window(session_id, fields)
            if not window["accepted"]:
                raise ControlRequestRejected(str(window["reason"]))
            received_at_s = monotonic()
            self.window_expires_at_s = requested_at_s + window["lifetime_s"]
            if received_at_s >= self.window_expires_at_s:
                raise ControlRequestRejected("控制窗口在返回前已到期")
            self.window_id = window["window_id"]
            self.window_renew_at_s = (received_at_s + self.window_expires_at_s) / 2

        return ControlCommand(session_id, self.seq, target, self.window_id, generation)


class ExpertControlWorker:
    """The worker owns both sockets; the UI reads snapshots and submits intent."""

    def __init__(
        self,
        connect: Callable[[], AbstractContextManager[ExpertManagementClient]],
        address: tuple[str, int],
    ):
        self._connect = connect
        self._address = address
        self._condition = Condition()
        self._thread = Thread(target=self._run, name="desktop-expert-control")
        self._status = ExpertWorkerStatus("new")
        self._closing = False
        self._auto_renew = False
        self._target: ControlTarget | None = None
        self._intent = 0
        self._action: _Action | None = None
        self._recipients: tuple[str, ...] = ()

    def start(self) -> None:
        with self._condition:
            if self._status.state != "new":
                raise RuntimeError("control worker has already been used")
            self._status = ExpertWorkerStatus("connecting")
            self._thread.start()

    def status(self) -> ExpertWorkerStatus:
        with self._condition:
            return self._status

    def apply_target(self, target: ControlTarget) -> bool:
        with self._condition:
            authority = self._status.authority
            if (self._closing or self._action is not None or authority is None
                    or not authority.owns_authority or authority.generation is None
                    or not authority.lease_remaining_s
                    or "set_authority_enabled" not in authority.allowed_actions):
                return False
            self._target = target
            self._intent += 1
            self._status = replace(self._status, detail="")
            self._condition.notify()
            return True

    def _clear_target(self) -> None:
        self._target = None
        self._intent += 1

    def stop_sending(self) -> None:
        with self._condition:
            self._clear_target()
            if self._status.state in ("ready", "sending"):
                self._status = replace(self._status, state="stopped", detail="")
            self._condition.notify()

    def set_auto_renew(self, enabled: bool) -> None:
        with self._condition:
            self._auto_renew = enabled
            self._condition.notify()

    def acquire_authority(self) -> bool:
        return self._queue_action("acquire_authority")

    def renew_authority(self) -> bool:
        return self._queue_action("renew_authority")

    def release_authority(self) -> bool:
        return self._queue_action("release_authority")

    def transfer_authority(self) -> bool:
        return self._queue_action("transfer_authority")

    def emergency_stop(self) -> bool:
        return self._queue_action("emergency_stop")

    def reset_control(self) -> bool:
        return self._queue_action("reset_control")

    def _queue_action(self, name: str, *, automatic: bool = False) -> bool:
        with self._condition:
            authority = self._status.authority
            if (self._closing or self._action is not None or authority is None
                    or name not in authority.allowed_actions):
                return False
            recipient = None
            if name == "transfer_authority":
                if len(self._recipients) != 1:
                    return False
                recipient = self._recipients[0]
            if name != "renew_authority":
                self._clear_target()
            state = {"emergency_stop": "stopping", "reset_control": "resetting"}.get(
                name, self._status.state,
            )
            self._action = _Action(
                name, authority.generation if authority.owns_authority else None,
                recipient, automatic,
            )
            self._status = replace(self._status, state=state, detail="", pending=True)
            self._condition.notify()
            return True

    def close(self) -> None:
        with self._condition:
            if self._closing:
                return
            self._closing = True
            self._clear_target()
            state = "closed" if self._status.state == "new" else "closing"
            self._status = ExpertWorkerStatus(state)
            self._condition.notify()

    def wait_closed(self, timeout: float | None = None) -> bool:
        if self._thread.ident is not None:
            self._thread.join(timeout)
        return not self._thread.is_alive()

    def _run(self) -> None:
        failure = ""
        try:
            with self._connect() as client, socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp:
                udp.setblocking(False)
                self._run_loop(client, client.open_session(), udp)
        except Exception as error:
            failure = f"{type(error).__name__}: {error}"
        finally:
            with self._condition:
                state = "failed" if failure and not self._closing else "closed"
                self._closing = True
                self._clear_target()
                self._status = ExpertWorkerStatus(state, failure if state == "failed" else "")

    def _dispatch(self, client: ExpertManagementClient, session_id: str, action: _Action) -> None:
        call = getattr(client, action.name)
        if action.name in ("acquire_authority", "reset_control"):
            call(session_id)
        else:
            assert action.generation is not None
            generation = _generation_fields(action.generation)
            if action.recipient is None:
                call(session_id, generation)
            else:
                call(session_id, generation, action.recipient)

    def _auto_renew_active(self) -> bool:
        return (self._auto_renew and self._status.state == "sending"
                and self._target is not None and self._target.speed_mps != 0)

    def _refresh(
        self, client: ExpertManagementClient, session_id: str, motion: _ExpertMotion,
        *, action_done: bool,
    ) -> None:
        reply = client.query_authority(session_id)
        authority = _authority_status(reply)
        with self._condition:
            if self._closing:
                return
            previous = self._status.authority
            self._recipients = tuple(reply.get("target_session_ids", ()))
            still_owns = (authority.owns_authority and previous is not None
                          and previous.generation == authority.generation
                          and bool(authority.lease_remaining_s)
                          and "set_authority_enabled" in authority.allowed_actions)
            if not still_owns:
                self._clear_target()
            if not still_owns or authority.phase != "controlling":
                motion.clear()
            if action_done:
                self._action = None
            if authority.phase == "fault_latched":
                state = "latched"
            elif self._action is not None and self._status.state in ("stopping", "resetting"):
                state = self._status.state
            elif self._target is not None and self._status.state == "sending":
                state = "sending"
            elif still_owns and self._status.state in ("stopped", "blocked"):
                state = "stopped"
            else:
                state = "ready"
            self._status = ExpertWorkerStatus(
                state, detail=self._status.detail, authority=authority, pending=self._action is not None,
            )
            remaining = authority.lease_remaining_s
            if (not action_done and self._auto_renew_active()
                    and remaining is not None and 0 < remaining <= AUTO_RENEW_MARGIN_S
                    and self._action is None):
                self._queue_action("renew_authority", automatic=True)

    def _run_loop(
        self, client: ExpertManagementClient, session_id: str, udp: socket.socket,
    ) -> None:
        motion = _ExpertMotion()
        next_poll_s = next_send_s = monotonic()
        sent_intent = -1
        while True:
            with self._condition:
                if self._closing:
                    return
                now_s = monotonic()
                if (self._action is not None and self._action.automatic
                        and not self._auto_renew_active()):
                    self._action = None
                    self._status = replace(self._status, pending=False)
                action, intent, target = self._action, self._intent, self._target
                authority = self._status.authority
                poll_due = now_s >= next_poll_s
                if action is None and not poll_due:
                    send_at_s = next_send_s if intent == sent_intent else now_s
                    wake_at_s = next_poll_s if target is None else min(next_poll_s, send_at_s)
                    if wake_at_s > now_s:
                        self._condition.wait(wake_at_s - now_s)
                        continue

            if action is not None or poll_due:
                if action is not None:
                    self._dispatch(client, session_id, action)
                self._refresh(client, session_id, motion, action_done=action is not None)
                next_poll_s = monotonic() + AUTHORITY_POLL_S
                continue

            assert target is not None and authority is not None and authority.generation is not None
            try:
                command = motion.prepare(client, session_id, authority.generation, target)
            except ControlRequestRejected as error:
                with self._condition:
                    if intent == self._intent and not self._closing:
                        self._clear_target()
                        self._status = replace(self._status, state="blocked", detail=str(error))
                next_poll_s = monotonic()
                continue
            with self._condition:
                if self._closing or intent != self._intent:
                    continue
                if monotonic() >= motion.window_expires_at_s:
                    continue
                send_expert_control_command(udp, command, self._address, key=motion.key)
                motion.seq += 1
                sent_intent = intent
                self._status = replace(self._status, state="sending", detail="")
            next_send_s = monotonic() + SEND_INTERVAL_S
