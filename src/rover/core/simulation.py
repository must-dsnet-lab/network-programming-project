from rover.core.vehicle_model import VehicleState, advance

#总模拟时间 = 步长 × 步数
#沿当前朝向的位移 = 速度 × 总模拟时间

def run_steps(
    initial_state: VehicleState,
    dt_s: float,
    steps: int,
) -> list[VehicleState]:
    if steps < 0:
        raise ValueError("steps must be non-negative")
    if dt_s < 0:
        raise ValueError("dt_s must be non-negative")

    states = [initial_state] #创建一个列表，包含初始状态
    current = initial_state

    for _ in range(steps):
        current = advance(current, dt_s)
        states.append(current)

    return states