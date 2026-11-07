from math import pi

from control_target import ControlTarget, apply_target
from simulation import run_steps
from vehicle_model import VehicleState


def main() -> None:
    dt_s = 0.1
    steps_per_phase = 5
    sim_time_s = 0.0  # sim_t 是按步长推进的模拟时间
    state = VehicleState()

    phases = [
        ("right", ControlTarget(speed_mps=2.0, heading_rad=0.0)),
        ("up", ControlTarget(speed_mps=2.0, heading_rad=pi / 2)),
        ("stop", ControlTarget(speed_mps=0.0, heading_rad=pi / 2)),
    ]

    # 采用新目标 → 推进一段时间 → 取阶段终点 → 进入下一阶段
    for label, target in phases:
        state = apply_target(state, target)
        states = run_steps(state, dt_s, steps_per_phase)
        state = states[-1] #在 Python 里，[-1] 代表取列表的最后一个元素
        sim_time_s += dt_s * steps_per_phase

        print(
            f"{label}: t={sim_time_s:.1f}s "
            f"x={state.x_m:.3f}m y={state.y_m:.3f}m "
            f"speed={state.speed_mps:.1f}m/s"
        )


if __name__ == "__main__":
    main()
