from collections import deque

from vehicle_model import VehicleState


class TrajectoryHistory:
    def __init__(self, max_points: int = 1000):
        if type(max_points) is not int or max_points <= 0:
            raise ValueError("max_points must be a positive integer")

        # deque(maxlen=max_points)：有容量上限的双端队列。从右侧追加新点，满了就自动移除左侧最旧的点。
        self._points: deque[tuple[float, float]] = deque(maxlen=max_points)

    def record(self, state: VehicleState) -> None:
        point = (state.x_m, state.y_m)

        # self._points[-1]：只与最近记录的位置比较，因此 A→A 会跳过重复，A→B→A 会保留完整顺序
        if self._points and self._points[-1] == point:
            return

        self._points.append(point)

    # Python的列表是引用传递，防止 self._points 里 append 新点后，外部引用的 points() 也跟着改变。
    # Python的元组不可变,所以tuple(self._points)：返回独立的不可变快照。
    def points(self) -> tuple[tuple[float, float], ...]:
        return tuple(self._points)
