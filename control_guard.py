from dataclasses import dataclass

from control_target import ControlTarget
from control_watchdog import CONTROL_TIMEOUT_S, AcceptedTarget

@dataclass(frozen=True)
class ControlGuardState:
    last_accepted: AcceptedTarget | None = None
    fault_latched: bool = False

# update_control_guard 是“法官”，负责判定系统是否违规（超时），
# 一旦违规直接判死刑（锁死），并清空有效控制记录（last_accepted）
def update_control_guard(
    guard: ControlGuardState,
    now_s: float,
    incoming: ControlTarget | None = None,
) -> ControlGuardState:
    if guard.fault_latched:
        return guard #封锁之后，返回原状态

    last_accepted = guard.last_accepted
    if (
        last_accepted is not None
        and now_s - last_accepted.accepted_at_s >= CONTROL_TIMEOUT_S
    ):
        return ControlGuardState(fault_latched=True)

    if incoming is not None:
        return ControlGuardState(
            last_accepted=AcceptedTarget(
                target=incoming,
                accepted_at_s=now_s,
            )
        )

    return guard
