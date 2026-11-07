from time import monotonic, sleep

from control_target import ControlTarget
from control_watchdog import AcceptedTarget
from vehicle_model import VehicleState
from vehicle_runtime import step_vehicle


def main() -> None:
    dt_s = 0.02
    duration_s = 1.0
    started_at_s = monotonic()

    # 只接受一次目标，循环中不刷新接受时间
    accepted = AcceptedTarget(
        target=ControlTarget(speed_mps=2.0, heading_rad=0.0),
        accepted_at_s=started_at_s,
    )
    state = VehicleState()
    steps = 0
    previous_speed = None

    while True:
        now_s = monotonic()
        elapsed_s = now_s - started_at_s

        state = step_vehicle(state, accepted, now_s, dt_s)
        steps += 1

        # 仅在速度改变时输出，便于观察启动和停车
        if state.speed_mps != previous_speed:
            print(
                f"change: wall_t={elapsed_s:.3f}s "
                f"sim_t={steps * dt_s:.3f}s "
                f"x={state.x_m:.3f}m speed={state.speed_mps:.1f}m/s",
                flush=True,
            )
            previous_speed = state.speed_mps

        if elapsed_s >= duration_s:
            break

        # 扣除本轮处理耗时，再等待剩余时间
        work_s = monotonic() - now_s
        sleep(max(0.0, dt_s - work_s))

    print(
        f"done: wall_t={monotonic() - started_at_s:.3f}s "
        f"sim_t={steps * dt_s:.3f}s "
        f"x={state.x_m:.3f}m speed={state.speed_mps:.1f}m/s"
    )


if __name__ == "__main__":
    main()
