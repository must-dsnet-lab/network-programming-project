# 接收 UDP 目标 → 更新车辆速度与朝向 → 推进模拟 → 输出位置

import socket

from control_target import apply_target
from simulation import run_steps
from udp_transport import receive_target
from vehicle_model import VehicleState


def main() -> None:
    address = ("127.0.0.1", 9999)
    dt_s = 0.02
    steps = 50

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind(address)
        print(f"waiting for target on {address}", flush=True)

        try:
            target, peer = receive_target(sock)
        except ValueError as exc:
            print(f"rejected: {exc}")
            return

    state = apply_target(VehicleState(), target)
    states = run_steps(state, dt_s, steps)
    first = states[0]
    last = states[-1]

    print(f"received from {peer}")
    print(f"target: {target}")
    print(f"states={len(states)}")
    print(f"start: x={first.x_m:.4f}m y={first.y_m:.4f}m")
    print(f"end: sim_t={dt_s * steps:.2f}s " f"x={last.x_m:.4f}m y={last.y_m:.4f}m")


if __name__ == "__main__":
    main()
