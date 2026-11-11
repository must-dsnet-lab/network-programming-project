from rover.core.control_target import apply_target
from rover.vehicle.control_watchdog import AcceptedTarget, target_for_tick
from rover.core.vehicle_model import VehicleState, advance

# 结合超时保护，计算并返回车辆在单个时间步长后的最新状态
def step_vehicle(
    state: VehicleState,
    last_accepted: AcceptedTarget | None,
    # now_s 是本轮开始时的单调时钟读数
    now_s: float,
    # dt_s 是本轮推进的模拟时长
    dt_s: float,
) -> VehicleState:
    target = target_for_tick(state, last_accepted, now_s)
    controlled_state = apply_target(state, target)
    return advance(controlled_state, dt_s)
