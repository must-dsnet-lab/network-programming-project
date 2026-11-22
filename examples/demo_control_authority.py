"""Interactive local demo of the P1 control authority and vehicle simulation."""

from rover.core.control_target import ControlTarget
from rover.core.vehicle_model import VehicleState
from rover.vehicle.control_authority import AuthorityPhase, ControlAuthority, ControlSession
from rover.vehicle.control_watchdog import AcceptedTarget
from rover.vehicle.vehicle_runtime import step_vehicle


def _show(label: str, authority: ControlAuthority, vehicle: VehicleState) -> None:
    grant = authority.grant
    holder = grant.session.client_id if grant is not None else "无"
    number = grant.generation.number if grant is not None else "无"
    print(
        f"{label}｜状态={authority.phase} 持有者={holder} "
        f"代次={number} 速度={vehicle.speed_mps:.1f} m/s"
    )


def _step(vehicle: VehicleState, authority: ControlAuthority, now_s: float) -> VehicleState:
    target = None
    if authority.phase is AuthorityPhase.CONTROLLING:
        target = AcceptedTarget(ControlTarget(1.0, 0.0), now_s)
    return step_vehicle(vehicle, target, now_s, dt_s=0.02)


def main() -> None:
    print("P1 本机仿真。当前终端模拟桌面 A、桌面 B 和车端。")
    alice = ControlSession("A", "connection-a", "session-a")
    bob = ControlSession("B", "connection-b", "session-b")
    authority, _ = ControlAuthority().acquire(alice)
    assert authority.grant is not None
    generation = authority.grant.generation
    authority, _ = authority.set_enabled(alice, generation, True)
    vehicle = _step(VehicleState(), authority, 0.0)
    _show("A 已应用目标", authority, vehicle)
    _, reason = authority.acquire(bob)
    print(f"B 在 A 控制期间申请：{reason or '已通过'}")

    input("按 Enter 让 A 发起交接给 B。\n")
    authority, _ = authority.transfer(alice, generation, bob)
    _show("等待归零", authority, vehicle)
    _, reason = authority.set_enabled(bob, generation, True)
    print(f"B 提前使能：{reason or '已通过'}")

    print("归零前选择：1 正常交接，2 候选 B 断开，3 车端故障锁存。")
    choice = input("输入 1、2 或 3，直接回车默认选 1。\n").strip() or "1"
    while choice not in ("1", "2", "3"):
        choice = input("请输入 1、2 或 3。\n").strip()

    if choice == "2":
        authority = authority.disconnect(bob)
    elif choice == "3":
        authority = authority.latch_fault()

    vehicle = _step(vehicle, authority, 0.02)
    if vehicle.speed_mps != 0.0:
        raise RuntimeError("车端仿真尚未归零。")
    authority = authority.motion_stopped(generation)

    if choice == "2":
        _show("B 断开后已归零", authority, vehicle)
    elif choice == "3":
        _show("故障锁存，已归零", authority, vehicle)
        _, reason = authority.acquire(bob)
        print(f"B 在锁存期间申请：{reason or '已通过'}")
        input("按 Enter 模拟车端确认显式复位。\n")
        authority = authority.fault_cleared()
        _show("显式复位完成", authority, vehicle)
    else:
        _show("已归零，B 待使能", authority, vehicle)
        _, reason = authority.set_enabled(alice, generation, True)
        print(f"A 用旧授权使能：{reason or '已通过'}")
        assert authority.grant is not None
        input("按 Enter 让 B 使能并应用目标。\n")
        authority, _ = authority.set_enabled(bob, authority.grant.generation, True)
        vehicle = _step(vehicle, authority, 0.04)
        _show("B 已使能并应用目标", authority, vehicle)


if __name__ == "__main__":
    try:
        main()
    except (EOFError, KeyboardInterrupt):
        print("\n本轮演示已结束。")
