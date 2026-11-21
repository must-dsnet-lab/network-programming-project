from dataclasses import dataclass
from math import isfinite

CONTROL_WINDOW_LIFETIME_S = 0.25


@dataclass(frozen=True)
class ControlWindow:
    window_id: str  # 带编号的指令有效时间段
    issued_at_s: float
    expires_at_s: float


def _finite_seconds(value: object, field_name: str) -> float:
    if type(value) not in (int, float):
        raise ValueError(f"{field_name} must be a finite number")

    try:
        number = float(value)
    except OverflowError as exc:
        raise ValueError(f"{field_name} is outside float range") from exc

    if not isfinite(number):
        raise ValueError(f"{field_name} must be finite")

    return number

# 创建窗口记录，计算固定截止时刻。当前 ID 由调用者传入。
def issue_control_window(
    window_id: str,
    now_s: float,
    *,
    lifetime_s: float = CONTROL_WINDOW_LIFETIME_S,
) -> ControlWindow:
    if not isinstance(window_id, str) or not window_id.strip():
        raise ValueError("window_id must be a non-blank string")

    now_s = _finite_seconds(now_s, "now_s")
    lifetime_s = _finite_seconds(lifetime_s, "lifetime_s")
    expires_at_s = now_s + lifetime_s

    if lifetime_s <= 0 or not isfinite(expires_at_s) or expires_at_s <= now_s:
        raise ValueError("lifetime_s must produce a finite future deadline")

    return ControlWindow(window_id, now_s, expires_at_s)

def control_window_rejection_reason(
    window: ControlWindow | None,
    window_id: str,
    now_s: float,
) -> str | None:
    if window is None:
        return "window_missing"
    if window.window_id != window_id:
        return "window_mismatch"

    try:
        now_s = _finite_seconds(now_s, "now_s")
    except ValueError:
        return "window_time_invalid"

    # 签发时刻 <= 检查时刻 < 截止时刻
    if window.issued_at_s <= now_s:
        if now_s < window.expires_at_s:
            return None
        if now_s >= window.expires_at_s:
            return "window_expired"
    elif now_s < window.issued_at_s:
        return "window_not_started"
    return "window_time_invalid"


# 只检查 ID 和时间，返回 True/False，不会维护或延长期限。
def is_control_window_valid(
    window: ControlWindow | None,
    window_id: str,
    now_s: float,
) -> bool:
    return control_window_rejection_reason(window, window_id, now_s) is None
