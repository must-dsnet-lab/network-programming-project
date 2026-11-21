import socket
from collections.abc import Callable
from time import monotonic
from contextlib import AbstractContextManager, nullcontext
from rover.core.control_target import ControlTarget
from rover.desktop.management_client import ManagementClient
from rover.protocol.control_codec import ControlCommand
from rover.transport.udp_transport import send_windowed_control_command


class ControlSendCancelled(OSError):
    """调用者在控制报文发出前取消了本次发送。"""


class SecureControlSession:
    """在一条已认证的管理连接上维护桌面端的控制状态。"""

    def __init__(
        self,
        client: ManagementClient,
        sock: socket.socket,
        address: tuple[str, int],
        *,
        clock: Callable[[], float] = monotonic,
    ):
        self._client = client
        self._sock = sock
        self._address = address
        self._clock = clock
        self._session_id = ""
        self._key = b""
        self._next_seq = 0
        self._window_id: str | None = None
        self._window_expires_at_s = 0.0
        self._renew_at_s = 0.0
        self._sending = False

    def open(self) -> str:
        if self._session_id:
            return self._session_id

        opened = self._client.open_session()
        if opened.control_key is None:
            raise ValueError("control session has no key")
        key = bytes.fromhex(opened.control_key)
        if len(key) != 32:
            raise ValueError("control session key must be 32 bytes")

        self._session_id = opened.session_id
        self._key = key
        return self._session_id

    def start(
        self,
        target: ControlTarget,
        *,
        send_gate: AbstractContextManager[None] | None = None,
    ) -> int:
        if self._sending:
            return self.send(target, send_gate=send_gate)

        session_id = self.open()
        self._renew_window(session_id)
        if not self._client.enable_session(session_id).enabled:
            raise PermissionError("vehicle refused to enable control")
        if self._clock() >= self._window_expires_at_s:
            raise TimeoutError("control window expired while enabling")

        self._sending = True
        return self.send(target, send_gate=send_gate)

    def send(
        self,
        target: ControlTarget,
        *,
        send_gate: AbstractContextManager[None] | None = None,
    ) -> int:
        if not self._sending:
            raise RuntimeError("control sending is inactive")

        try:
            if self._clock() >= self._renew_at_s:
                self._renew_window(self._session_id)
            command = ControlCommand(
                session_id=self._session_id,
                seq=self._next_seq,
                target=target,
                window_id=self._window_id,
            )
            gate = send_gate if send_gate is not None else nullcontext()
            with gate:
                sent = send_windowed_control_command(
                    self._sock, command, self._address, key=self._key
                )
        except (OSError, ValueError):
            self.stop_sending()
            raise

        self._next_seq += 1
        return sent

    def stop_sending(self) -> None:
        self._sending = False

    def _renew_window(self, session_id: str) -> None:
        self._window_id = None
        requested_at_s = self._clock()
        window = self._client.issue_control_window(session_id)
        if not window.granted:
            raise PermissionError("vehicle refused a control window")
        if not window.window_id or window.lifetime_s is None:
            raise ValueError("control window reply is incomplete")

        expires_at_s = requested_at_s + window.lifetime_s
        received_at_s = self._clock()
        remaining_s = expires_at_s - received_at_s
        if remaining_s <= 0.0:
            raise TimeoutError("control window expired before its reply")

        self._window_id = window.window_id
        self._window_expires_at_s = expires_at_s
        self._renew_at_s = received_at_s + remaining_s / 2
