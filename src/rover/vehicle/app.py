"""初始文件名：demo_udp_vehicle_loop.py"""
import argparse
import csv
import json
from pathlib import Path
from ipaddress import IPv4Address
import socket
from time import monotonic, sleep
from contextlib import nullcontext
from rover.vehicle.control_guard import (
    ControlGuardState,
    update_control_guard,
    close_control_session,
    update_windowed_command_guard,
)
from rover.vehicle.management_runtime import management_listener
from rover.protocol.telemetry_codec import TelemetrySnapshot
from rover.transport.udp_transport import (
    send_telemetry,
    try_receive_windowed_control_command,
    try_receive_target,
)
from rover.core.vehicle_model import VehicleState
from rover.vehicle.vehicle_runtime import step_vehicle


def main(
    *,
    management=None,
    address: tuple[str, int] = ("127.0.0.1", 9999),
    telemetry_address: tuple[str, int] = ("127.0.0.1", 10000),
    observer_address: tuple[str, int] | None = None,
    event_log: Path | None = None,
) -> None:
    telemetry_destinations = (telemetry_address,)
    if observer_address is not None and observer_address != telemetry_address:
        telemetry_destinations += (observer_address,)
    dt_s = 0.02
    telemetry_interval_s = 0.05
    started_at_s = monotonic()
    next_telemetry_at_s = started_at_s
    next_telemetry_error_log_s = started_at_s

    state = VehicleState()
    guard = ControlGuardState()
    previous_speed = None
    previous_fault = None

    log_context = (
        event_log.open("x", encoding="utf-8", newline="", buffering=1)
        if event_log is not None else nullcontext()
    )
    with (
        log_context as event_stream,
        socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock,
        socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as telemetry_sock,
        management if management is not None else nullcontext() as process_management,
    ):
        sock.bind(address)
        sock.setblocking(False)

        telemetry_sock.bind((address[0], 0))
        telemetry_sock.setblocking(False)

        writer = csv.writer(event_stream) if event_stream is not None else None

        def record(at_s: float, event: str, *, seq: int | None = None, detail: str = "") -> None:
            if writer is not None:
                writer.writerow((
                    f"{at_s - started_at_s:.6f}", event, seq, state.speed_mps,
                    str(guard.fault_latched).lower(), detail,
                ))

        last_recorded_state = (state.speed_mps, guard.fault_latched)
        if writer is not None:
            writer.writerow(("elapsed_s", "event", "seq", "speed_mps", "fault_latched", "detail"))
            record(started_at_s, "state", detail="initial")

        print(f"waiting for UDP on {address}; Ctrl+C to exit", flush=True)

        try:
            while True:
                # 1. 记录本轮起点，先检查保护状态
                tick_started_s = monotonic()
                session_change = ""
                if process_management is not None: # 如果没有后台管理，就继续沿用物理引擎
                    # process_management(guard)调用之前备份原来的guard.sesion_id
                    previous_session_id = guard.session_id
                    # process_management(guard) 每轮最多处理一个管理请求，队列空就直接返回。
                    # 仿真循环保存 guard，TLS 线程等待结果
                    guard = process_management(guard)
                    if guard.session_id != previous_session_id:

                        print(f"Management session: {guard.session_id}", flush=True)
                        if guard.session_id is None:
                            session_change = "closed"
                        elif previous_session_id is None:
                            session_change = "opened"
                        else:
                            session_change = "replaced"
                guard = update_control_guard(guard, tick_started_s)

                # 2. 每轮最多尝试接收一个控制报文
                received = None
                rejected_message = None
                try:
                    if process_management is None:
                        received = try_receive_target(sock)
                    else:
                        received = try_receive_windowed_control_command(
                            sock, key=guard.control_key
                        )
                except ValueError as exc:
                    rejected_message = str(exc)

                # 3. 收包后重新取时，再判定保护状态
                now_s = monotonic()
                incoming = received[0] if received is not None else None
                previous_seq = guard.last_seq
                if process_management is None:
                    guard = update_control_guard(guard, now_s, incoming)
                else:
                    guard = update_windowed_command_guard(guard, now_s, incoming)

                # 4. 每轮只推进车辆一次
                state = step_vehicle(state, guard.last_accepted, now_s, dt_s)

                # 5. 更新车辆后，按发送期限采样并回传
                telemetry_now_s = monotonic()

                if telemetry_now_s >= next_telemetry_at_s:
                    snapshot = TelemetrySnapshot(
                        state=state,
                        fault_latched=guard.fault_latched,
                    )

                    for destination in telemetry_destinations:
                        try:
                            send_telemetry(telemetry_sock, snapshot, destination)
                        except (OSError, ValueError) as exc:
                            if telemetry_now_s >= next_telemetry_error_log_s:
                                print(f"telemetry unavailable: {destination}: {exc}", flush=True)
                                next_telemetry_error_log_s = telemetry_now_s + 1.0

                    # 每轮最多发送一次；落后时跳过积压，不集中补发
                    next_telemetry_at_s += telemetry_interval_s
                    if next_telemetry_at_s <= telemetry_now_s:
                        next_telemetry_at_s = telemetry_now_s + telemetry_interval_s

                # 6. 输出本轮处理结果
                if rejected_message is not None:
                    print(f"rejected: {rejected_message}", flush=True)

                event = None
                if received is not None:
                    message, peer = received
                    if process_management is None:
                        event = "ignored" if guard.fault_latched else "accepted"
                    else:
                        event = "accepted" if guard.last_seq != previous_seq else "ignored"
                    print(f"{event} from {peer}: {message}", flush=True)

                if writer is not None:
                    if session_change:
                        record(now_s, "session", detail=session_change)
                    if event == "accepted":
                        record(guard.last_accepted.accepted_at_s, "control_accepted", seq=guard.last_seq)
                    observed_state = (state.speed_mps, guard.fault_latched)
                    if observed_state != last_recorded_state:
                        # Use the timestamp after stepping the simulation for actual motion.
                        record(telemetry_now_s, "state")
                        last_recorded_state = observed_state

                if (
                    received is not None
                    or state.speed_mps != previous_speed
                    or guard.fault_latched != previous_fault
                ):
                    print(
                        f"state: wall_t={now_s - started_at_s:.3f}s "
                        f"x={state.x_m:.4f}m y={state.y_m:.4f}m "
                        f"speed={state.speed_mps:.1f}m/s "
                        f"fault_latched={guard.fault_latched}",
                        flush=True,
                    )

                previous_speed = state.speed_mps
                previous_fault = guard.fault_latched

                # 7. 将收包、仿真、遥测及日志耗时都计入本轮
                work_s = monotonic() - tick_started_s
                sleep(max(0.0, dt_s - work_s))

        except KeyboardInterrupt:
            print("\nreceiver stopped")
        finally:
            if process_management is not None and guard.session_id is not None:
                guard = close_control_session(guard, guard.session_id)
                print(f"Management session: {guard.session_id}", flush=True)
                if writer is not None:
                    record(monotonic(), "session", detail="closed")


def load_trusted_credentials(path: Path) -> dict[str, bytes]:
    document = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(document, dict) or not document:
        raise ValueError("credentials must be a non-empty JSON object")

    trusted = {}
    for client_id, credential in document.items():
        if not isinstance(client_id, str) or not client_id.strip():
            raise ValueError("client ID must be a non-blank string")
        if not isinstance(credential, str) or not credential.strip():
            raise ValueError("credential must be a non-blank string")
        trusted[client_id] = credential.encode("utf-8")
    return trusted


def _port_argument(value: str) -> int:
    try:
        port = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("port must be an integer") from error
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError("port must be between 1 and 65535")
    return port


def _host_argument(value: str) -> str:
    host = value.strip()
    if not host:
        raise argparse.ArgumentTypeError("host must be non-blank")
    return host


def cli(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Run the vehicle demo or TLS-managed vehicle."
    )
    parser.add_argument("--secure", action="store_true")
    parser.add_argument("--cert-dir", type=Path)
    parser.add_argument("--credentials-file", type=Path)
    parser.add_argument("--bind-host", type=_host_argument, default="127.0.0.1")
    parser.add_argument("--control-port", type=_port_argument, default=9999)
    parser.add_argument("--management-port", type=_port_argument, default=19177)
    parser.add_argument("--telemetry-host", type=_host_argument, default="127.0.0.1")
    parser.add_argument("--telemetry-port", type=_port_argument, default=10000)
    parser.add_argument("--observer-host", type=IPv4Address, help="optional observer IPv4")
    parser.add_argument("--observer-port", type=_port_argument, help="observer UDP port (default: 10001)")
    parser.add_argument("--event-log", type=Path, help="write vehicle events to a new CSV file")
    args = parser.parse_args(argv)

    if args.secure: # 命令中带 --secure
        if args.cert_dir is None or args.credentials_file is None:
            parser.error("--secure requires --cert-dir and --credentials-file")
    elif args.cert_dir is not None or args.credentials_file is not None:
        parser.error("--cert-dir and --credentials-file require --secure")
    observer_address = None
    if args.observer_host is not None:
        observer_address = (str(args.observer_host), args.observer_port or 10001)
    elif args.observer_port is not None:
        parser.error("--observer-port requires --observer-host")
    management = None
    try:
        if args.secure:
            for name in ("server.pem", "server.key"):
                if not (args.cert_dir / name).is_file():
                    parser.error(f"missing certificate file: {args.cert_dir / name}")
            trusted = load_trusted_credentials(args.credentials_file)
            management = management_listener(
                args.cert_dir,
                trusted,
                address=(args.bind_host, args.management_port),
            )

        log_options = {"event_log": args.event_log} if args.event_log is not None else {}
        main(
            address=(args.bind_host, args.control_port),
            telemetry_address=(args.telemetry_host, args.telemetry_port),
            management=management,
            observer_address=observer_address,
            **log_options,
        )
    except (OSError, ValueError) as error:
        parser.error(f"vehicle startup failed: {error}")


if __name__ == "__main__":
    cli()
