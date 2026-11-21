import socket
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from threading import Condition, Thread
from time import monotonic

from rover.core.control_target import ControlTarget
from rover.desktop.management_client import ManagementClient
from rover.desktop.secure_control import (
    ControlRequestRejected,
    ControlSendCancelled,
    SecureControlSession,
)

SEND_INTERVAL_S = 0.05
KEEPALIVE_S = 2.0


@dataclass(frozen=True)
class WorkerStatus:
    state: str
    detail: str = ""


class SecureControlWorker:
    """后台线程独占管理连接和 UDP socket；界面以后只提交目标、请求停发或关闭，再读取最新状态。
     线程大约每 50 毫秒发一次控制包，空闲时每 2 秒发一次管理请求来维持连接。"""

    def __init__(
        self,
        connect: Callable[[], AbstractContextManager[ManagementClient]],
        address: tuple[str, int],
    ):
        self._connect = connect
        self._address = address
        self._condition = Condition()  # Python 的条件变量, 让线程在没事做时睡眠
        self._target: ControlTarget | None = None
        self._generation = 0
        self._action: tuple[str, str] | None = None
        self._closing = False
        self._status = WorkerStatus("new")
        self._thread = Thread(target=self._run, name="desktop-secure-control")

    def start(self) -> None:
        with self._condition:
            if self._status.state != "new":
                raise RuntimeError("control worker has already been used")
            self._status = WorkerStatus("connecting")
            self._thread.start()

    def apply_target(self, target: ControlTarget) -> bool:
        with self._condition:
            if (
                self._closing
                or self._action is not None
                or self._status.state in ("new", "latched", "blocked")
            ):
                return False
            self._target = target
            self._generation += 1
            self._condition.notify()
            return True

    def stop_sending(self) -> None:
        with self._condition:
            if self._closing:
                return
            self._target = None
            self._generation += 1
            if self._status.state in ("ready", "sending"):
                self._status = WorkerStatus("stopped")
            self._condition.notify()

    def emergency_stop(self) -> bool:
        return self._queue_action("emergency_stop")

    def reset_control(self) -> bool:
        return self._queue_action("reset")

    def _queue_action(self, kind: str) -> bool:
        with self._condition:
            state = self._status.state
            if (
                self._closing
                or self._action is not None
                or state not in ("ready", "sending", "stopped", "latched", "blocked")
            ):
                return False
            if kind == "reset" and self._target is not None:
                return False

            self._target = None
            self._generation += 1
            self._action = (kind, state)
            pending = "stopping" if kind == "emergency_stop" else "resetting"
            self._status = WorkerStatus(pending)
            self._condition.notify()
            return True

    def close(self) -> None:
        with self._condition:
            if self._closing:
                return
            self._closing = True
            self._target = None
            self._generation += 1
            state = "closed" if self._status.state == "new" else "closing"
            self._status = WorkerStatus(state)
            self._condition.notify()

    def wait_closed(self, timeout: float | None = None) -> bool:
        if self._thread.ident is not None:
            self._thread.join(timeout)
        return not self._thread.is_alive()

    def status(self) -> WorkerStatus:
        with self._condition:
            return self._status

    @contextmanager
    def _send_gate(self, generation: int) -> Iterator[None]:
        with self._condition:
            if self._closing or generation != self._generation:
                raise ControlSendCancelled("control intent changed")
            yield

    def _run(self) -> None:
        failure = ""
        try:
            with (
                self._connect() as client,
                socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp,
            ):
                udp.setblocking(False)
                session = SecureControlSession(client, udp, self._address)
                with self._condition:
                    if self._closing:
                        return

                session_id = session.open()
                with self._condition:
                    if self._closing:
                        return
                    self._status = WorkerStatus("ready")

                self._run_loop(client, session, session_id)
        except Exception as error:
            failure = f"{type(error).__name__}: {error}"
        finally:
            with self._condition:
                state = "failed" if failure and not self._closing else "closed"
                self._closing = True
                self._target = None
                self._status = WorkerStatus(state, failure if state == "failed" else "")

    def _perform_action(
        self,
        client: ManagementClient,
        session: SecureControlSession,
        session_id: str,
        action: tuple[str, str],
    ) -> tuple[str, WorkerStatus]:
        kind, previous = action
        if kind == "emergency_stop":
            session.stop_sending()
            if client.emergency_stop(session_id).accepted:
                return session_id, WorkerStatus("latched")
            return session_id, WorkerStatus("blocked", "车端拒绝急停")

        if not session.reset_control():
            state = previous if previous in ("latched", "blocked") else "stopped"
            return session_id, WorkerStatus(state, "车端拒绝复位")

        with self._condition:
            if self._closing:
                return session_id, WorkerStatus("closing")

        fresh_id = session.open()
        if fresh_id == session_id:
            raise ConnectionError("reset returned the old control session")
        return fresh_id, WorkerStatus("ready", "复位成功，请重新应用")

    def _run_loop(
        self,
        client: ManagementClient,
        session: SecureControlSession,
        session_id: str,
    ) -> None:
        active = False
        last_generation = -1
        next_send_s = monotonic()
        next_keepalive_s = next_send_s + KEEPALIVE_S

        while True:
            with self._condition:
                if self._closing:
                    return

                action = self._action
                target, generation = self._target, self._generation
                now_s = monotonic()
                if action is not None:
                    deadline_s = now_s
                elif target is None:
                    if active:
                        session.stop_sending()
                        active = False
                    deadline_s = next_keepalive_s
                elif generation != last_generation:
                    deadline_s = now_s
                else:
                    deadline_s = next_send_s

                if deadline_s > now_s:
                    self._condition.wait(deadline_s - now_s)
                    continue
            if action is not None:
                session_id, status = self._perform_action(
                    client, session, session_id, action
                )
                active = False
                next_keepalive_s = monotonic() + KEEPALIVE_S
                with self._condition:
                    self._action = None
                    if not self._closing:
                        self._status = status
                continue

            if target is None:
                if client.open_session().session_id != session_id:
                    raise ConnectionError("control session changed while idle")
                next_keepalive_s = monotonic() + KEEPALIVE_S
                continue

            gate = self._send_gate(generation)
            try:
                if active:
                    session.send(target, send_gate=gate)
                else:
                    session.start(target, send_gate=gate)
            except ControlSendCancelled:
                active = False
                continue
            except ControlRequestRejected as error:
                active = False
                with self._condition:
                    if generation == self._generation and not self._closing:
                        self._target = None
                        self._status = WorkerStatus("blocked", str(error))
                next_keepalive_s = monotonic() + KEEPALIVE_S
                continue
            active = True
            last_generation = generation
            now_s = monotonic()
            next_send_s = now_s + SEND_INTERVAL_S
            next_keepalive_s = now_s + KEEPALIVE_S
            with self._condition:
                if generation == self._generation and not self._closing:
                    self._status = WorkerStatus("sending")
