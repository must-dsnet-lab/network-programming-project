"""初始文件名：demo_udp_vehicle_loop.py"""

import socket
from time import monotonic, sleep

from rover.vehicle.control_guard import ControlGuardState, update_control_guard
from rover.protocol.telemetry_codec import TelemetrySnapshot
from rover.transport.udp_transport import send_telemetry, try_receive_target
from rover.core.vehicle_model import VehicleState
from rover.vehicle.vehicle_runtime import step_vehicle


def main() -> None:
    address = ("127.0.0.1", 9999)
    dt_s = 0.02
    telemetry_address = ("127.0.0.1", 10000)
    telemetry_interval_s = 0.05
    started_at_s = monotonic()
    next_telemetry_at_s = started_at_s
    next_telemetry_error_log_s = started_at_s

    state = VehicleState()
    guard = ControlGuardState()
    previous_speed = None
    previous_fault = None

    with (
    socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock,
    socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as telemetry_sock,
):
        sock.bind(address)
        sock.setblocking(False)

        telemetry_sock.bind(("127.0.0.1", 0))
        telemetry_sock.setblocking(False)

        print(f"waiting for UDP on {address}; Ctrl+C to exit", flush=True)

        try:
            while True:
                # 1. 记录本轮起点，先检查保护状态
                tick_started_s = monotonic()
                guard = update_control_guard(guard, tick_started_s)

                # 2. 每轮最多尝试接收一个控制报文
                received = None
                rejected_message = None
                try:
                    received = try_receive_target(sock)
                except ValueError as exc:
                    rejected_message = str(exc)

                # 3. 收包后重新取时，再判定保护状态
                now_s = monotonic()
                incoming = received[0] if received is not None else None
                guard = update_control_guard(guard, now_s, incoming)

                # 4. 每轮只推进车辆一次
                state = step_vehicle(state, guard.last_accepted, now_s, dt_s)

                # 5. 更新车辆后，按发送期限采样并回传
                telemetry_now_s = monotonic()

                if telemetry_now_s >= next_telemetry_at_s:
                    snapshot = TelemetrySnapshot(
                        state=state,
                        fault_latched=guard.fault_latched,
                    )

                    try:
                        send_telemetry(
                            telemetry_sock,
                            snapshot,
                            telemetry_address,
                        )
                    except (OSError, ValueError) as exc:
                        # 遥测发送失败只在此处处理，错误日志最多每秒一条
                        if telemetry_now_s >= next_telemetry_error_log_s:
                            print(f"telemetry unavailable: {exc}", flush=True)
                            next_telemetry_error_log_s = telemetry_now_s + 1.0

                    # 每轮最多发送一次；落后时跳过积压，不集中补发
                    next_telemetry_at_s += telemetry_interval_s
                    if next_telemetry_at_s <= telemetry_now_s:
                        next_telemetry_at_s = telemetry_now_s + telemetry_interval_s

                # 6. 输出本轮处理结果
                if rejected_message is not None:
                    print(f"rejected: {rejected_message}", flush=True)

                if received is not None:
                    target, peer = received
                    event = "ignored" if guard.fault_latched else "accepted"
                    print(f"{event} from {peer}: {target}", flush=True)

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


if __name__ == "__main__":
    main()
