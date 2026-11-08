import socket
from time import monotonic, sleep

from control_guard import ControlGuardState, update_control_guard
from udp_transport import try_receive_target
from vehicle_model import VehicleState
from vehicle_runtime import step_vehicle


def main() -> None:
    address = ("127.0.0.1", 9999)
    dt_s = 0.02
    started_at_s = monotonic()

    state = VehicleState()
    guard = ControlGuardState()
    previous_speed = None
    previous_fault = None

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind(address)
        sock.setblocking(False)
        print(f"waiting for UDP on {address}; Ctrl+C to exit", flush=True)

        try:
            while True:
                tick_started_s = monotonic()
                guard = update_control_guard(guard, tick_started_s)

                # 每轮最多尝试接收一个报文
                received = None
                rejected_message = None
                try:
                    received = try_receive_target(sock)
                except ValueError as exc:
                    rejected_message = str(exc)

                # 收包、解析后重新取时；没有数据也检查保护状态
                now_s = monotonic()
                incoming = received[0] if received is not None else None
                guard = update_control_guard(guard, now_s, incoming)
                state = step_vehicle(state, guard.last_accepted, now_s, dt_s)

                # 先完成状态更新，再输出日志
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

                work_s = monotonic() - tick_started_s
                sleep(max(0.0, dt_s - work_s))

        except KeyboardInterrupt:
            print("\nreceiver stopped")


if __name__ == "__main__":
    main()
