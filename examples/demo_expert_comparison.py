"""Run the same legitimate stop and recovery trial in both localhost modes."""

import argparse
from contextlib import contextmanager
from functools import partial
from getpass import GetPassWarning, getpass
import json
from pathlib import Path
import platform
import socket
import subprocess
import sys
from threading import Event
from time import monotonic, sleep
from warnings import catch_warnings, simplefilter

from rover.core.control_target import ControlTarget
from rover.desktop.control_worker import SecureControlWorker
from rover.desktop.expert_control_worker import ExpertControlWorker
from rover.desktop.secure_connection import connect_expert_management, connect_management
from rover.transport.udp_transport import try_receive_telemetry


HOST = "127.0.0.1"
OBSERVATION_TIMEOUT_S = 3.0
POLL_INTERVAL_S = 0.01


def _wait_for_worker(worker, condition, description):
    deadline = monotonic() + OBSERVATION_TIMEOUT_S
    while monotonic() < deadline:
        status = worker.status()
        if status.state in ("failed", "closed", "closing"):
            raise RuntimeError(f"{description}: worker {status.state} {status.detail}")
        if condition(status):
            return
        sleep(POLL_INTERVAL_S)
    raise TimeoutError(description)


def _wait_for_telemetry(worker, telemetry, speed, fault):
    last = None

    def observed(_status):
        nonlocal last
        for _ in range(16):
            received = try_receive_telemetry(telemetry)
            if received is None:
                break
            snapshot, peer = received
            if peer[0] == HOST:
                last = snapshot
        return last is not None and last.state.speed_mps == speed and last.fault_latched is fault

    _wait_for_worker(worker, observed, f"missing telemetry speed={speed}, fault_latched={fault}")


def _acquire(worker):
    _wait_for_worker(
        worker, lambda status: status.authority is not None
        and "acquire_authority" in status.authority.allowed_actions,
        "expert authority acquisition is unavailable",
    )
    if not worker.acquire_authority():
        raise RuntimeError("worker did not queue authority acquisition")
    _wait_for_worker(
        worker, lambda status: status.authority is not None
        and status.authority.owns_authority and not status.pending,
        "expert authority was not granted",
    )


def _exercise(worker, telemetry, expert, tls_open, checkpoints):
    def observe(label, speed, fault=False):
        _wait_for_telemetry(worker, telemetry, speed, fault)
        checkpoints.append(label)

    def apply(label, speed):
        if not worker.apply_target(ControlTarget(speed, 0.0)):
            raise RuntimeError(f"worker rejected target for {label}")
        observe(label, speed)

    _wait_for_worker(worker, lambda status: status.state == "ready", "worker did not become ready")
    observe("initial_zero", 0.0)
    if expert:
        _acquire(worker)
    apply("first_motion", 1.0)
    apply("normal_stop", 0.0)
    apply("reapply_before_watchdog", 1.0)

    worker.stop_sending()
    _wait_for_telemetry(worker, telemetry, 0.0, True)
    if not tls_open():
        raise RuntimeError("TLS connection closed during the watchdog measurement")
    checkpoints.append("zero_latched_with_tls")

    if expert:
        _wait_for_worker(
            worker, lambda status: status.authority is not None
            and "reset_control" in status.authority.allowed_actions,
            "expert reset is unavailable",
        )
    if not worker.reset_control():
        raise RuntimeError("worker did not queue explicit reset")
    _wait_for_worker(worker, lambda status: status.state == "ready", "control reset did not finish")
    observe("zero_after_reset", 0.0)
    if not tls_open():
        raise RuntimeError("TLS connection closed during explicit reset")
    if expert:
        _acquire(worker)
    apply("reset_and_reapply", 1.0)
    apply("final_normal_stop", 0.0)


def _vehicle_args(expert, tcp_port, control_port, telemetry_port, cert_dir, credentials_file, events):
    args = [sys.executable, "-X", "utf8", "-B", "-u", "-m", "rover.vehicle.app", "--secure"]
    if expert:
        args.append("--expert-management")
    args.extend([
        "--bind-host", HOST, "--management-port", str(tcp_port), "--control-port", str(control_port),
        "--telemetry-host", HOST, "--telemetry-port", str(telemetry_port),
        "--cert-dir", str(cert_dir), "--credentials-file", str(credentials_file),
        "--event-log", str(events),
    ])
    return args


def _allocate_ports():
    with (
        socket.socket(socket.AF_INET, socket.SOCK_STREAM) as tcp,
        socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as control,
    ):
        tcp.bind((HOST, 0))
        control.bind((HOST, 0))
        return tcp.getsockname()[1], control.getsockname()[1]


@contextmanager
def _vehicle_process(command, output):
    log = output / "vehicle.log"
    creation_flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    with log.open("xb") as console, subprocess.Popen(
        command, stdout=console, stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL, creationflags=creation_flags,
    ) as process:
        try:
            deadline = monotonic() + OBSERVATION_TIMEOUT_S
            while monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError(f"vehicle startup failed, see {log}")
                if "waiting for UDP on" in log.read_text(encoding="utf-8", errors="replace"):
                    break
                sleep(POLL_INTERVAL_S)
            else:
                raise TimeoutError(f"vehicle startup timeout, see {log}")
            yield
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=2.0)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2.0)


def _run_trial(expert, output, options, credential):
    output.mkdir()
    checkpoints = []
    result = {"status": "failed", "checkpoints": checkpoints}
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as telemetry:
            telemetry.bind((HOST, 0))
            telemetry.setblocking(False)
            tcp_port, control_port = _allocate_ports()
            command = _vehicle_args(
                expert, tcp_port, control_port, telemetry.getsockname()[1], options.cert_dir,
                options.credentials_file, output / "vehicle-events.csv",
            )
            connect = partial(
                connect_expert_management if expert else connect_management,
                (HOST, tcp_port), ca_file=options.cert_dir / "ca.pem", server_hostname="localhost",
                client_id=options.client_id, credential=credential,
            )
            active = Event()
            connections = 0

            @contextmanager
            def connect_observed():
                nonlocal connections
                with connect() as client:
                    connections += 1
                    active.set()
                    try:
                        yield client
                    finally:
                        active.clear()

            with _vehicle_process(command, output):
                worker_type = ExpertControlWorker if expert else SecureControlWorker
                worker = worker_type(connect_observed, (HOST, control_port))
                worker.start()
                try:
                    _exercise(worker, telemetry, expert, active.is_set, checkpoints)
                finally:
                    worker.close()
                    if not worker.wait_closed(timeout=4.0):
                        raise TimeoutError("desktop worker did not close")
            if connections != 1:
                raise RuntimeError("trial did not keep a single TLS connection")
            result["status"] = "passed"
    except (OSError, RuntimeError, ValueError) as error:
        result["error"] = f"{type(error).__name__}: {error}"
    _write_json(output / "result.json", result)
    return result["status"] == "passed"


def _write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True, type=Path, help="new directory for trial evidence")
    parser.add_argument("--runs", type=int, default=3, help="number of trials per mode")
    parser.add_argument("--cert-dir", type=Path, default=Path("local/certs"))
    parser.add_argument("--credentials-file", type=Path, default=Path("local/trusted_clients.json"))
    parser.add_argument("--client-id", default="desktop-a")
    options = parser.parse_args(argv)
    if options.runs <= 0:
        parser.error("--runs must be positive")
    if options.output_dir.exists():
        parser.error("--output-dir must be new")
    required = [options.cert_dir / name for name in ("ca.pem", "server.pem", "server.key")]
    required.append(options.credentials_file)
    for path in required:
        if not path.is_file():
            parser.error(f"missing local trial input: {path}")

    try:
        with catch_warnings():
            simplefilter("error", GetPassWarning)
            credential = getpass("Credential (hidden): ")
        if not credential:
            raise ValueError("empty credential")
        git = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True, timeout=5.0,
        ).stdout.strip()
        options.output_dir.mkdir(parents=True)
        _write_json(options.output_dir / "metadata.json", {
            "git": git, "system": platform.platform(), "python": sys.version,
            "host": HOST, "runs_per_mode": options.runs,
        })
        failed = 0
        for trial in range(1, options.runs + 1):
            for mode in ("advanced", "expert"):
                name = f"{mode}-{trial:02d}"
                passed = _run_trial(mode == "expert", options.output_dir / name, options, credential)
                failed += not passed
                print(f"{name}: {'passed' if passed else 'failed'}", flush=True)
        return 1 if failed else 0
    except (EOFError, GetPassWarning, OSError, subprocess.SubprocessError, ValueError) as error:
        print(f"FAILED: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
