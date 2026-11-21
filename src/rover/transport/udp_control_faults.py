import argparse
import csv
import socket
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from random import Random
from time import perf_counter
from typing import TextIO

from rover.transport.udp_control_order import REORDER_HOLD_S, FaultMode, PacketOrderPolicy
from rover.transport.udp_control_outage import POLL_INTERVAL_S, _lab_address
from rover.transport.udp_transport import UDP_RECEIVE_BUFFER_BYTES


MAX_PENDING_PACKETS = 256


@dataclass(frozen=True)
class _PendingPacket:
    index: int
    payload: bytes
    peer: tuple[str, int]
    received_s: float


def relay_controls(
    sock: socket.socket,
    desktop_host: str,
    vehicle_address: tuple[str, int],
    event_log: TextIO,
    *,
    loss_percent: int,
    delay_ms: int,
    seed: int,
    reorder_percent: int = 0,
    duplicate_percent: int = 0,
) -> None:
    writer = csv.writer(event_log)
    writer.writerow((
        "elapsed_s", "event", "packet_index", "peer_host", "peer_port", "packet_bytes",
        "observed_delay_ms", "detail",
    ))
    started_s = perf_counter()
    delay_s = delay_ms / 1000
    loss_probability = loss_percent / 100
    randomizer = Random(seed)
    pending: deque[_PendingPacket] = deque()
    packet_index = 0
    order_policy: PacketOrderPolicy[_PendingPacket] | None = None
    order_settings = ""
    if reorder_percent or duplicate_percent:
        mode = FaultMode.REORDER if reorder_percent else FaultMode.DUPLICATE
        percent = reorder_percent or duplicate_percent
        order_policy = PacketOrderPolicy(mode, seed=seed, probability=percent / 100)
        order_settings = (
            f"; reorder_percent={reorder_percent}; duplicate_percent={duplicate_percent}; "
            f"reorder_hold_ms={REORDER_HOLD_S * 1000:g}"
        )

    def record(
        now_s: float,
        event: str,
        packet: _PendingPacket | None = None,
        observed_delay_ms: float | None = None,
        detail: str = "",
    ) -> None:
        host, port = packet.peer if packet is not None else ("", "")
        writer.writerow((
            f"{now_s - started_s:.6f}", event, packet.index if packet else None, host, port,
            len(packet.payload) if packet else None,
            f"{observed_delay_ms:.3f}" if observed_delay_ms is not None else "", detail,
        ))
        event_log.flush()

    def forward(packet: _PendingPacket, detail: str = "") -> None:
        try:
            sock.sendto(packet.payload, vehicle_address)
        except OSError as error:
            message = f"{detail}: {error}" if detail else str(error)
            record(perf_counter(), "send_failed", packet, detail=message)
        else:
            sent_s = perf_counter()
            record(sent_s, "packet_forwarded", packet, (sent_s - packet.received_s) * 1000, detail)

    record(
        started_s, "relay_started",
        detail=f"desktop={desktop_host}; vehicle={vehicle_address}; loss_percent={loss_percent}; "
        f"delay_ms={delay_ms}; seed={seed}{order_settings}",
    )
    try:
        while True:
            now_s = perf_counter()
            deadline_s = None
            if order_policy is not None:
                for delivery in order_policy.flush_due(now_s):
                    forward(delivery.packet, delivery.role.value)
                deadline_s = order_policy.deadline_s
            else:
                while pending and pending[0].received_s + delay_s <= now_s:
                    forward(pending.popleft())
                    now_s = perf_counter()
                if pending:
                    deadline_s = pending[0].received_s + delay_s

            timeout_s = POLL_INTERVAL_S
            if deadline_s is not None:
                timeout_s = min(timeout_s, deadline_s - now_s)
            sock.settimeout(timeout_s)
            try:
                payload, peer = sock.recvfrom(UDP_RECEIVE_BUFFER_BYTES)
            except TimeoutError:
                continue
            received_s = perf_counter()
            if peer[0] != desktop_host:
                record(received_s, "source_ignored", detail=f"peer={peer}; bytes={len(payload)}")
                continue

            packet_index += 1
            packet = _PendingPacket(packet_index, payload, peer, received_s)
            if order_policy is not None:
                for delivery in order_policy.receive(packet, received_s):
                    forward(delivery.packet, delivery.role.value)
            elif loss_probability and randomizer.random() < loss_probability:
                record(received_s, "packet_dropped", packet)
            elif len(pending) >= MAX_PENDING_PACKETS:
                record(received_s, "delay_queue_full", packet)
            else:
                pending.append(packet)
    except KeyboardInterrupt:
        pass
    finally:
        pending_count = len(pending)
        if order_policy is not None and order_policy.deadline_s is not None:
            pending_count += 1
        record(perf_counter(), "relay_stopped", detail=f"pending_discarded={pending_count}")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Run the rover control-only N0-N3 UDP lab.")
    parser.add_argument("--bind-host", type=_lab_address, required=True, help="local vehicle interface")
    parser.add_argument("--desktop-host", type=_lab_address, required=True, help="expected control sender")
    parser.add_argument("--listen-port", type=int, default=10002)
    parser.add_argument("--vehicle-port", type=int, default=9999)
    parser.add_argument("--event-log", type=Path, required=True, help="new CSV file for this run")
    parser.add_argument("--seed", type=int, default=42, help="seed for fault decisions (default: 42)")
    fault = parser.add_mutually_exclusive_group()
    fault.add_argument("--loss-percent", type=int, choices=(5, 15), default=0, help="N1 per-packet loss")
    fault.add_argument("--delay-ms", type=int, choices=(100, 300), default=0, help="N2 fixed added delay")
    fault.add_argument("--reorder-percent", type=int, choices=(5, 15), default=0, help="N3 select packets to hold")
    fault.add_argument("--duplicate-percent", type=int, choices=(5, 15), default=0, help="N3 select packets to copy")
    args = parser.parse_args(argv)
    if not all(1 <= port <= 65535 for port in (args.listen_port, args.vehicle_port)):
        parser.error("ports must be between 1 and 65535")
    if args.listen_port == args.vehicle_port:
        parser.error("listen and vehicle ports must differ")

    relay_address = (args.bind_host, args.listen_port)
    vehicle_address = (args.bind_host, args.vehicle_port)
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.bind(relay_address)
            args.event_log.parent.mkdir(parents=True, exist_ok=True)
            with args.event_log.open("x", encoding="utf-8", newline="") as event_log:
                print(f"Control UDP relay {relay_address} -> {vehicle_address}", flush=True)
                print(f"Loss {args.loss_percent}% | delay {args.delay_ms} ms | seed {args.seed}", flush=True)
                if args.reorder_percent or args.duplicate_percent:
                    print(f"Reorder {args.reorder_percent}% | duplicate {args.duplicate_percent}%", flush=True)
                relay_controls(
                    sock, args.desktop_host, vehicle_address, event_log,
                    loss_percent=args.loss_percent, delay_ms=args.delay_ms, seed=args.seed,
                    reorder_percent=args.reorder_percent, duplicate_percent=args.duplicate_percent,
                )
    except OSError as error:
        parser.exit(1, f"UDP control relay failed: {error}\n")


if __name__ == "__main__":
    main()
