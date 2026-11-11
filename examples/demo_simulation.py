from rover.core.simulation import run_steps
from rover.core.vehicle_model import VehicleState


def main() -> None:
    dt_s = 0.1
    initial = VehicleState(speed_mps=2.0)
    states = run_steps(initial, dt_s, 5)

    for step, state in enumerate(states):
        sim_time_s = step * dt_s
        print(
            f"t={sim_time_s:.1f}s "
            f"x={state.x_m:.3f}m "
            f"y={state.y_m:.3f}m"
        )


if __name__ == "__main__":
    main()