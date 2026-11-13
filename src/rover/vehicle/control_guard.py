from dataclasses import dataclass, replace

from rover.core.control_target import ControlTarget
from rover.vehicle.control_watchdog import CONTROL_TIMEOUT_S, AcceptedTarget
from rover.protocol.control_codec import ControlCommand

@dataclass(frozen=True)
# ControlGuardState控制的是一整个session的信息
class ControlGuardState:
    last_accepted: AcceptedTarget | None = None
    fault_latched: bool = False
    session_id: str | None = None  # 车端当前登记的控制会话
    enabled: bool = False  # 是否已明确允许控制
    last_seq: int | None = None  # 最后接受的序号；None表示尚未接受
    # enabled_at_s：本次使能的时间，用于限制等待首条有效指令的时长
    enabled_at_s: float | None = None  # 本次使能时间，作为首包等待起点

# update_control_guard 是“法官”，负责判定系统是否违规（超时），
# 一旦违规直接判死刑（锁死），并清空有效控制记录（last_accepted）
def update_control_guard(
    guard: ControlGuardState,
    now_s: float,
    incoming: ControlTarget | None = None,  # 速度、朝向
) -> ControlGuardState:
    if guard.fault_latched:
        return guard #封锁之后，返回原状态

    last_accepted = guard.last_accepted

    if last_accepted is not None:  # 已接受过有效指令：使用 accepted_at_s
        timeout_started_at_s = last_accepted.accepted_at_s
    elif guard.enabled:  # 尚未接受指令，但已使能：使用 enabled_at_s
        timeout_started_at_s = guard.enabled_at_s
    else:  # 未使能且没有接受记录：暂不计时
        timeout_started_at_s = None

    # 已经使能，却找不到计时起点
    missing_time_reference = guard.enabled and timeout_started_at_s is None

    if missing_time_reference or(
        timeout_started_at_s is not None
        and now_s - timeout_started_at_s >= CONTROL_TIMEOUT_S
    ):
        # # 之前的命令曾经有效，但它的有效期结束了，所以撤销其执行资格
        return replace(
            guard,
            last_accepted=None,  # 清空当前可执行目标
            fault_latched=True,  # 保持故障，阻止普通新命令恢复控制
            enabled=False,  # 撤销使能
        )

    if incoming is not None:
        return ControlGuardState(
            last_accepted=AcceptedTarget(
                target=incoming,
                accepted_at_s=now_s,
            )
        )

    return guard


# 保留: session_id + enabled + fault_latched
# 更新: last_accepted(targe+accepted_at_s) + last_seq
def update_command_guard(
    guard: ControlGuardState,
    now_s: float,
    incoming: ControlCommand | None = None,  # 会话、序号、目标
) -> ControlGuardState:
    # 使用update_control_guard()中的判断超时方法，同时对 会话、序号、目标 进行审查
    guard = update_control_guard(guard, now_s=now_s)  # 只复用保护检查，不会提前接受命令
    if guard.fault_latched:
        return guard

    if incoming is None or not guard.enabled:
        return guard

    if incoming.session_id != guard.session_id:
        return guard

    # 已经有接受历史，才比较序号；0 也属于有效历史
    # 拒绝重复或倒退的序号，保留原控制记录
    if guard.last_seq is not None and incoming.seq <= guard.last_seq:
        return guard

    return replace(
        guard,
        last_accepted=AcceptedTarget(
            target=incoming.target,
            accepted_at_s=now_s,
        ),
        last_seq=incoming.seq,
    )


def open_control_session(
    guard: ControlGuardState,
    session_id: str,
) -> ControlGuardState:
    # guard.session_id：车端已经登记的会话
    # 参数 session_id：本次想要登记的会话
    if guard.session_id is not None and guard.session_id != session_id:
        raise ValueError("another control session is already open")

    return replace(guard, session_id=session_id)


def enable_control_session(
    guard: ControlGuardState,
    now_s: float,
) -> ControlGuardState:
    if guard.session_id is None:
        raise ValueError("no control session is open")

    if guard.fault_latched:
        raise ValueError("control fault is latched")

    if guard.enabled:
        return guard

    return replace(
        guard,
        enabled=True,
        enabled_at_s=now_s,
    )


def close_control_session(
    guard: ControlGuardState,
    session_id: str,
) -> ControlGuardState:
    if guard.session_id is not None and guard.session_id != session_id:
        raise ValueError("control session does not match")

    return replace(
        guard,
        session_id=None,
        enabled=False,
        enabled_at_s=None,
        last_accepted=None,
        last_seq=None,
    )
