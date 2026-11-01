from dataclasses import dataclass, replace
from math import cos,sin

#注：变量名的下划线后面是单位
#@dataclass 自动生成初始化等方法
#frozen=True 让状态对象的字段保持只读
@dataclass(frozen=True)
class VehicleState:
    x_m: float = 0.0
    y_m: float = 0.0
    heading_rad: float = 0.0 #朝向（单位：弧度）
    speed_mps: float = 0.0
    
#类型注解表达期望的类型，Python默认不强制进行运行时类型检查
#: VehicleState 是类型注解，说明参数必须是 VehicleState 类的实例
#-> VehicleState：是返回值注解，说明返回值也是 VehicleState 对象
#dt_s：本次推进的时间
def advance(state: VehicleState, dt_s: float) -> VehicleState:
    if dt_s < 0:
        raise ValueError("dt_s must be non-negative")

    distance_m = state.speed_mps * dt_s

    return replace(
        state,
        x_m=state.x_m + distance_m * cos(state.heading_rad),
        y_m=state.y_m + distance_m * sin(state.heading_rad),
    )
    # replace(obj, **changes) -> 创建新对象，更新指定字段，原对象保持不变
    ''' 等价：
    return VehicleState(
    x_m=new_x,                 # 换成新算的
    y_m=new_y,                 # 换成新算的
    heading_rad=state.heading_rad, # 没变的，硬抄过来
    speed_mps=state.speed_mps      # 没变的，硬抄过来
    )'''