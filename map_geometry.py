def world_to_screen(
    # 车辆的世界坐标(单位：m):
    x_m: float,
    y_m: float,  # 世界坐标系（数学/物理）—— Y轴朝上
    # 在 * 之后定义的所有参数，调用时绝对不能按位置（顺序）传入，
    # 必须严格写出“参数名=值”，避免把原点和比例的位置写混
    *,
    # 世界原点在画布上的位置
    origin_x_px: float,
    origin_y_px: float,  # 画布/屏幕坐标系（计算机图形）—— Y轴朝下
    # 每米对应多少画布像素（必须大于零）
    pixels_per_meter: float,
) -> tuple[float, float]:
    if pixels_per_meter <= 0:
        raise ValueError("pixels_per_meter must be positive")

    screen_x_px = origin_x_px + x_m * pixels_per_meter
    screen_y_px = origin_y_px - y_m * pixels_per_meter

    return screen_x_px, screen_y_px
