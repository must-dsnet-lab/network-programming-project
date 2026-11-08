from dataclasses import dataclass,replace

from vehicle_model import VehicleState

# 收到控制目标 → 更新运动参数
# 仿真时间推进 → 更新位置


@dataclass(frozen=True)
class ControlTarget:  # 控制端希望小车怎样运动
    speed_mps: float
    heading_rad: float


def apply_target(
    state: VehicleState,
    target: ControlTarget,
) -> VehicleState:
    return replace(
        state,
        speed_mps=target.speed_mps,
        heading_rad=target.heading_rad,
    )
