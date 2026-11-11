from dataclasses import dataclass

from rover.core.control_target import ControlTarget
from rover.core.vehicle_model import VehicleState

CONTROL_TIMEOUT_S = 0.5


@dataclass(frozen=True)
class AcceptedTarget:
    target: ControlTarget
    accepted_at_s: float  # 接收端单调时钟读数，单位秒

# target_for_tick 是“司机”，每次调用都在看判决书，没超时就继续开，超时了立刻踩刹车
# 没有有效控制记录，或距离最后一次接受控制已达到 0.5 秒，就返回停车目标
# 防止出现断网死机等情况使小车失控，并保留车辆当前朝向
def target_for_tick(
    state: VehicleState,
    last_accepted: AcceptedTarget | None,
    now_s: float,
) -> ControlTarget:
    if (
        last_accepted is None
        or now_s - last_accepted.accepted_at_s >= CONTROL_TIMEOUT_S
    ):
        return ControlTarget(speed_mps=0.0, heading_rad=state.heading_rad)
    return last_accepted.target
