import argparse
import csv
from collections.abc import Callable
from contextlib import AbstractContextManager
from functools import partial
from getpass import getpass
from math import ceil
from pathlib import Path
from statistics import median
from time import perf_counter_ns
from typing import TextIO

from rover.desktop.management_client import ManagementClient
from rover.desktop.secure_connection import connect_management


def measure_management(
    connect: Callable[[], AbstractContextManager[ManagementClient]],
    output: TextIO,
    *,
    rtt_samples: int,
    reconnect_samples: int,
    clock_ns: Callable[[], int] = perf_counter_ns,
) -> tuple[list[float], list[float]]:
    writer = csv.writer(output)
    writer.writerow(("measurement", "sample", "status", "duration_ms", "error"))

    def record(
        measurement: str, sample: int, duration_ms: float | None = None, error: str = ""
    ) -> None:
        if duration_ms is None:
            writer.writerow((measurement, sample, "failed", "", error))
        else:
            writer.writerow((measurement, sample, "ok", f"{duration_ms:.3f}", ""))
        output.flush()

    rtt_ms: list[float] = []
    reconnect_ms: list[float] = []
    measurement = "setup"
    sample = 1
    try:
        with connect() as client:
            initial_id = client.open_session().session_id
            measurement = "management_rtt"
            for sample in range(1, rtt_samples + 1):
                started_ns = clock_ns()
                reply = client.open_session()
                duration_ms = (clock_ns() - started_ns) / 1_000_000
                if reply.session_id != initial_id:
                    raise ValueError("active management session changed during RTT measurement")
                record(measurement, sample, duration_ms)
                rtt_ms.append(duration_ms)
            measurement = "disconnect"
            sample = 1

        session_ids = {initial_id}
        measurement = "reconnect"
        for sample in range(1, reconnect_samples + 1):
            started_ns = clock_ns()
            with connect() as client:
                reply = client.open_session()
                duration_ms = (clock_ns() - started_ns) / 1_000_000
                if reply.session_id in session_ids:
                    raise ValueError("reconnect returned a previously used session ID")
                session_ids.add(reply.session_id)
            record(measurement, sample, duration_ms)
            reconnect_ms.append(duration_ms)
    except (OSError, ValueError) as error:
        record(measurement, sample, error=type(error).__name__)
        raise

    return rtt_ms, reconnect_ms


def print_summary(label: str, samples_ms: list[float]) -> None:
    ordered = sorted(samples_ms)
    p95 = ordered[ceil(0.95 * len(ordered)) - 1]
    print(f"{label} ({len(ordered)} samples)  P50 {median(ordered):.3f} ms  P95 {p95:.3f} ms")


def _positive_count(value: str) -> int:
    count = int(value)
    if count < 1:
        raise argparse.ArgumentTypeError("sample count must be positive")
    return count


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Measure management RTT and deliberate reconnect over verified TLS."
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=19177)
    parser.add_argument("--server-name", help="TLS certificate name or IP address")
    parser.add_argument("--ca-file", type=Path, default=Path("local/certs/ca.pem"))
    parser.add_argument("--client-id", default="desktop-a")
    parser.add_argument("--output", type=Path, required=True, help="new CSV path for raw samples")
    parser.add_argument("--rtt-samples", type=_positive_count, default=20)
    parser.add_argument("--reconnect-samples", type=_positive_count, default=5)
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")

    server_name = args.server_name or ("localhost" if args.host == "127.0.0.1" else args.host)
    try:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x", encoding="utf-8", newline="") as output:
            credential = getpass("Credential (hidden): ")
            connect = partial(
                connect_management,
                (args.host, args.port),
                ca_file=args.ca_file,
                server_hostname=server_name,
                client_id=args.client_id,
                credential=credential,
            )
            rtt_ms, reconnect_ms = measure_management(
                connect, output,
                rtt_samples=args.rtt_samples, reconnect_samples=args.reconnect_samples,
            )
    except (OSError, ValueError) as error:
        print(f"FAILED {type(error).__name__}")
        return 1

    print_summary("Management RTT on an established connection", rtt_ms)
    print_summary("Reconnect from new connection attempt to first SessionOpened", reconnect_ms)
    print("Reconnect timing excludes failure detection.")
    print(f"Raw CSV {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
