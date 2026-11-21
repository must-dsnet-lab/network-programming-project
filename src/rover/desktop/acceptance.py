import csv
from collections import deque
from datetime import datetime, timedelta, timezone
from threading import Lock
from time import monotonic

from rover.transport.udp_transport import send_target, send_windowed_control_command


TELEMETRY_DELAY_S = 1.5


class TelemetryDelivery:
    """Inject local display faults without interrupting UDP reads or control."""

    def __init__(self):
        self.mode = "live"
        self.pending = deque()

    def set_mode(self, mode):
        if mode not in ("live", "delay", "pause"):
            raise ValueError("unknown telemetry fault mode")
        self.mode = mode
        self.pending.clear()

    def receive(self, now, snapshot, peer):
        if self.mode != "pause":
            self.pending.append((now, snapshot, peer))

    def deliver(self, now):
        cutoff = now - TELEMETRY_DELAY_S if self.mode == "delay" else now
        while self.pending and self.pending[0][0] <= cutoff:
            yield self.pending.popleft()


class AcceptanceRecording:
    FIELDS = ("time_utc", "trial", "elapsed_ms", "event", "detail", "peer", "x_m", "y_m",
              "heading_rad", "speed_mps", "fault_latched")

    def __init__(self):
        self.active = False
        self.trial = 0
        self.started = 0.0
        self.wall_started = None
        self.rows = []

    def begin(self, now):
        self.active = True
        self.trial += 1
        self.started = now
        self.wall_started = datetime.now(timezone.utc)

    def append(self, now, event, detail="", *, snapshot=None, peer=None):
        if not self.active:
            return
        elapsed = now - self.started
        row = {
            "time_utc": (self.wall_started + timedelta(seconds=elapsed)).isoformat(timespec="milliseconds"),
            "trial": self.trial, "elapsed_ms": round(elapsed * 1000),
            "event": event, "detail": detail,
        }
        if snapshot is not None:
            state = snapshot.state
            row.update(peer=f"{peer[0]}:{peer[1]}", x_m=state.x_m, y_m=state.y_m,
                       heading_rad=state.heading_rad, speed_mps=state.speed_mps,
                       fault_latched=int(snapshot.fault_latched))
        self.rows.append(row)

    def write(self, path):
        with open(path, "w", encoding="utf-8-sig", newline="") as file:
            writer = csv.DictWriter(file, self.FIELDS)
            writer.writeheader()
            writer.writerows(sorted(self.rows, key=lambda row: (row["trial"], row["elapsed_ms"])))


class ControlDatagrams:
    """Observe local sends and optionally suppress every third datagram."""

    def __init__(self):
        self._lock = Lock()
        self.enabled = False
        self._attempt = 0
        self._capturing = False
        self._events = []

    def set_loss(self, enabled):
        with self._lock:
            self.enabled = enabled
            self._attempt = 0

    def set_capture(self, active):
        with self._lock:
            self._capturing = active

    def drain(self):
        with self._lock:
            events, self._events = self._events, []
            return events

    def send_target(self, sock, target, address):
        return self._deliver(send_target, (sock, target, address), {}, target)

    def send_windowed(self, sock, command, address, *, key):
        return self._deliver(send_windowed_control_command, (sock, command, address),
                             {"key": key}, command.target, sequence=command.seq)

    def _deliver(self, send, args, kwargs, target, *, sequence=None):
        with self._lock:
            self._attempt += 1
            dropped = self.enabled and self._attempt % 3 == 0
            sent = 0 if dropped else send(*args, **kwargs)
            if self._capturing:
                detail = f"速度 {target.speed_mps:.2f} m/s，朝向 {target.heading_rad:.3f} rad"
                if sequence is not None:
                    detail = f"seq={sequence} · {detail}"
                self._events.append((monotonic(), "control_dropped" if dropped else "control_sent", detail))
            return sent
