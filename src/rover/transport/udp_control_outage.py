import argparse
import csv
import os
import socket
import sys
from ipaddress import IPv4Address, IPv4Network
from pathlib import Path
from queue import Empty, Full, Queue
from threading import Thread
from time import monotonic
from typing import TextIO

from rover.transport.udp_transport import UDP_RECEIVE_BUFFER_BYTES


OUTAGE_DURATION_S = 2.0
POLL_INTERVAL_S = 0.02
LAB_NETWORKS = tuple(IPv4Network(network) for network in (
    "127.0.0.0/8", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16",
))


def relay_controls(
    sock: socket.socket,
    desktop_host: str,
    vehicle_address: tuple[str, int],
    requests: Queue[None],
    event_log: TextIO,
) -> None:
    writer = csv.writer(event_log)
    writer.writerow((
        "elapsed_s", "event", "trial", "peer_host", "peer_port", "packet_bytes",
        "dropped_packets", "detail",
    ))
    started_s = monotonic()
    outage_until_s = None
    trial = 0
    dropped_packets = 0

    def record(now_s, event, peer=None, packet_bytes=None, detail=""):
        host, port = peer if peer is not None else ("", "")
        writer.writerow((
            f"{now_s - started_s:.6f}", event, trial, host, port, packet_bytes,
            dropped_packets, detail,
        ))
        event_log.flush()

    record(
        started_s,
        "relay_started",
        detail=f"desktop={desktop_host}; vehicle={vehicle_address}; outage={OUTAGE_DURATION_S:g}s",
    )
    try:
        while True:
            received = None
            try:
                received = sock.recvfrom(UDP_RECEIVE_BUFFER_BYTES)
            except TimeoutError:
                pass

            now_s = monotonic()
            if outage_until_s is not None and now_s >= outage_until_s:
                outage_until_s = None
                record(now_s, "outage_ended")
                print(f"Trial {trial}: forwarding resumed; dropped {dropped_packets} packets", flush=True)

            try:
                requests.get_nowait()
            except Empty:
                pass
            else:
                if outage_until_s is None:
                    trial += 1
                    dropped_packets = 0
                    outage_until_s = now_s + OUTAGE_DURATION_S
                    record(now_s, "outage_started")
                    print(f"Trial {trial}: dropping control packets for {OUTAGE_DURATION_S:g} s", flush=True)
                else:
                    record(now_s, "trigger_ignored")

            if received is None:
                continue
            packet, peer = received
            if peer[0] != desktop_host:
                record(now_s, "source_ignored", peer, len(packet))
                continue
            if outage_until_s is not None:
                dropped_packets += 1
                record(now_s, "packet_dropped", peer, len(packet))
                continue
            try:
                sock.sendto(packet, vehicle_address)
            except OSError as error:
                record(now_s, "send_failed", peer, len(packet), str(error))
            else:
                record(now_s, "packet_forwarded", peer, len(packet))
    except KeyboardInterrupt:
        pass
    finally:
        now_s = monotonic()
        if outage_until_s is not None:
            record(now_s, "outage_aborted")
        record(now_s, "relay_stopped")


def _lab_address(value: str) -> str:
    try:
        address = IPv4Address(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("use an IPv4 address") from error
    if not any(address in network for network in LAB_NETWORKS):
        raise argparse.ArgumentTypeError("use a private or loopback IPv4 address for the lab")
    return str(address)


def _read_requests(fd: int, requests: Queue[None]) -> None:
    # Read from the descriptor so an idle console reader holds no Python buffer lock at exit.
    while chunk := os.read(fd, 4096):
        if b"\n" not in chunk and b"\r" not in chunk:
            continue
        try:
            requests.put_nowait(None)
        except Full:
            pass


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Run the rover control-only two-second outage lab.")
    parser.add_argument("--bind-host", type=_lab_address, required=True, help="local vehicle interface")
    parser.add_argument("--desktop-host", type=_lab_address, required=True, help="expected control sender")
    parser.add_argument("--listen-port", type=int, default=10002)
    parser.add_argument("--vehicle-port", type=int, default=9999)
    parser.add_argument("--event-log", type=Path, required=True, help="new CSV file for this run")
    args = parser.parse_args(argv)
    if not all(1 <= port <= 65535 for port in (args.listen_port, args.vehicle_port)):
        parser.error("ports must be between 1 and 65535")
    if args.listen_port == args.vehicle_port:
        parser.error("listen and vehicle ports must differ")

    relay_address = (args.bind_host, args.listen_port)
    vehicle_address = (args.bind_host, args.vehicle_port)
    requests: Queue[None] = Queue(maxsize=1)
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.bind(relay_address)
            sock.settimeout(POLL_INTERVAL_S)
            args.event_log.parent.mkdir(parents=True, exist_ok=True)
            with args.event_log.open("x", encoding="utf-8", newline="") as event_log:
                Thread(target=_read_requests, args=(sys.stdin.fileno(), requests), daemon=True).start()
                print(f"Control UDP relay {relay_address} -> {vehicle_address}", flush=True)
                print("Press Enter while moving to start a two-second outage; Ctrl+C to exit.", flush=True)
                relay_controls(sock, args.desktop_host, vehicle_address, requests, event_log)
    except OSError as error:
        parser.exit(1, f"UDP control relay failed: {error}\n")


if __name__ == "__main__":
    main()
