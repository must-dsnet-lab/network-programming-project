import json
from math import isfinite
from dataclasses import dataclass
from vehicle_model import VehicleState

MAX_TELEMETRY_MESSAGE_BYTES = 1200


@dataclass(frozen=True)
class TelemetrySnapshot:  # 车端当前实际是什么状态
    state: VehicleState
    fault_latched: bool


def encode_telemetry(snapshot: TelemetrySnapshot) -> bytes:
    state = snapshot.state

    message = {
        "version": 1,
        "type": "vehicle_state",
        "x_m": state.x_m,
        "y_m": state.y_m,
        "heading_rad": state.heading_rad,
        "speed_mps": state.speed_mps,
        "fault_latched": snapshot.fault_latched,
    }

    for field in ("x_m", "y_m", "heading_rad", "speed_mps"):
        _validate_motion_value(message[field], field)

    if type(snapshot.fault_latched) is not bool:
        raise ValueError("fault_latched must be a boolean")

    # allow_nan=False: 如果 message 里混入了 NaN 或 Infinity，json.dumps 会立刻抛出 ValueError
    packet = json.dumps(message, allow_nan=False).encode("utf-8")

    if len(packet) > MAX_TELEMETRY_MESSAGE_BYTES:
        raise ValueError("telemetry message is too large")

    return packet

# 接受整数和浮点数，拒绝布尔值、浮点溢出与非有限数值
def _validate_motion_value(value: object, field_name: str) -> None:
    if type(value) not in (int, float):
        raise ValueError(f"{field_name} must be a number")

    try:
        numeric_value = float(value)
    except OverflowError as exc:
        raise ValueError(f"{field_name} is outside float range") from exc

    if not isfinite(numeric_value):
        raise ValueError(f"{field_name} must be finite")
