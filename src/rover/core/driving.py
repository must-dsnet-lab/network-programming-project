"""Fixed-step planar simulation for normalized driver input."""

from dataclasses import dataclass, replace
from math import cos, isfinite, remainder, sin, tau

from rover.core.control_target import ControlTarget
from rover.core.vehicle_model import VehicleState


DRIVE_MAX_SPEED_MPS = 2.0
DRIVE_ACCEL_MPS2 = 1.2
COAST_DECEL_MPS2 = 0.30
BRAKE_DECEL_MPS2 = 2.4
TURN_CURVATURE_PER_M = 0.8


@dataclass(frozen=True)
class DriveInput:
    """Pedals range from 0 to 1. Steering ranges from -1 (right) to 1 (left)."""

    throttle: float
    brake: float
    steering: float

    def __post_init__(self) -> None:
        for name, lower_bound in (("throttle", 0.0), ("brake", 0.0), ("steering", -1.0)):
            value = getattr(self, name)
            if type(value) not in (int, float) or not lower_bound <= value <= 1.0:
                raise ValueError(f"{name} must be a finite number in [{lower_bound}, 1]")


type ControlIntent = ControlTarget | DriveInput


def advance_driving(state: VehicleState, controls: DriveInput, dt_s: float) -> VehicleState:
    """Apply one input over a simulated step and advance along a circular arc."""
    if not isfinite(dt_s) or dt_s < 0.0:
        raise ValueError("dt_s must be finite and non-negative")
    if dt_s == 0.0:
        return state

    # Entering driving mode from numerical control bounds speed to its forward range.
    speed = min(DRIVE_MAX_SPEED_MPS, max(0.0, state.speed_mps))
    if controls.brake > 0.0 or controls.throttle == 0.0:
        acceleration = -(COAST_DECEL_MPS2 + controls.brake * (BRAKE_DECEL_MPS2 - COAST_DECEL_MPS2))
        limit = 0.0
    else:
        acceleration = controls.throttle * DRIVE_ACCEL_MPS2
        limit = DRIVE_MAX_SPEED_MPS

    # Split the step at the stop or speed cap, where acceleration becomes zero.
    time_to_limit = (limit - speed) / acceleration
    accelerating_s = min(dt_s, time_to_limit)
    final_speed = limit if time_to_limit <= dt_s else speed + acceleration * accelerating_s
    distance_m = (speed + final_speed) * accelerating_s / 2 + final_speed * (dt_s - accelerating_s)

    half_turn = controls.steering * TURN_CURVATURE_PER_M * distance_m / 2
    middle_heading = state.heading_rad + half_turn
    chord_m = distance_m * sin(half_turn) / half_turn if half_turn else distance_m
    heading = remainder(state.heading_rad + 2 * half_turn, tau) if half_turn else state.heading_rad
    return replace(
        state,
        x_m=state.x_m + chord_m * cos(middle_heading),
        y_m=state.y_m + chord_m * sin(middle_heading),
        heading_rad=heading,
        speed_mps=final_speed,
    )
