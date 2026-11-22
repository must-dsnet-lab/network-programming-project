"""Record a repeatable flat-ground driving run using the vehicle's actual step function."""

import argparse
import csv
import os
from dataclasses import dataclass
from math import hypot
from pathlib import Path

from rover.core.driving import BRAKE_DECEL_MPS2, DriveInput
from rover.core.vehicle_model import VehicleState
from rover.vehicle.control_watchdog import AcceptedTarget
from rover.vehicle.vehicle_runtime import step_vehicle


STEP_S = 0.02
PHASES = (
    ("throttle", 90, DriveInput(1.0, 0.0, 0.0)),
    ("coast", 50, DriveInput(0.0, 0.0, 0.0)),
    ("brake", 40, DriveInput(0.0, 1.0, 0.0)),
    ("turn_left", 70, DriveInput(1.0, 0.0, 0.65)),
    ("center", 30, DriveInput(0.0, 0.0, 0.0)),
)
PHASE_COLORS = {
    "throttle": "#19647e", "coast": "#786022", "brake": "#ac4037",
    "turn_left": "#1a7159", "center": "#6f588e",
}


@dataclass(frozen=True)
class Sample:
    time_s: float
    phase: str
    inputs: DriveInput
    vehicle: VehicleState
    acceleration_mps2: float


def drive_samples() -> list[Sample]:
    previous = VehicleState()
    samples = [Sample(0.0, "initial", DriveInput(0.0, 0.0, 0.0), previous, 0.0)]
    for phase, steps, inputs in PHASES:
        for _ in range(steps):
            tick_s = (len(samples) - 1) * STEP_S
            state = step_vehicle(previous, AcceptedTarget(inputs, tick_s), tick_s, STEP_S)
            acceleration = (state.speed_mps - previous.speed_mps) / STEP_S
            samples.append(Sample(len(samples) * STEP_S, phase, inputs, state, acceleration))
            previous = state
    return samples


def write_csv(path: Path, samples: list[Sample]) -> None:
    with path.open("x", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow((
            "time_s", "phase", "throttle", "brake", "steering", "x_m", "y_m",
            "heading_rad", "speed_mps", "acceleration_mps2",
        ))
        for sample in samples:
            inputs, state = sample.inputs, sample.vehicle
            writer.writerow((
                f"{sample.time_s:.2f}", sample.phase, inputs.throttle, inputs.brake, inputs.steering,
                f"{state.x_m:.6f}", f"{state.y_m:.6f}", f"{state.heading_rad:.6f}",
                f"{state.speed_mps:.6f}", f"{sample.acceleration_mps2:.6f}",
            ))


def write_chart(path: Path, samples: list[Sample]) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCharts import QChart, QChartView, QLineSeries, QValueAxis
    from PySide6.QtCore import QRect, QSize, Qt
    from PySide6.QtGui import QColor, QFont, QFontDatabase, QPainter, QPen
    from PySide6.QtSvg import QSvgGenerator
    from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget

    app = QApplication.instance() or QApplication([])
    if not QFontDatabase.families() and (windows := os.environ.get("WINDIR")):
        for filename in ("arial.ttf", "arialbd.ttf"):
            QFontDatabase.addApplicationFont(str(Path(windows) / "Fonts" / filename))
    font_family = "Arial" if QFontDatabase.hasFamily("Arial") else "Sans Serif"
    figure = QWidget()
    figure.setStyleSheet("background: white")
    layout = QVBoxLayout(figure)
    layout.setSpacing(4)
    for title, accessor, lower, upper, show_legend in (
        ("Speed (m/s)", lambda sample: sample.vehicle.speed_mps, 0.0, 2.2, True),
        ("Acceleration (m/s²)", lambda sample: sample.acceleration_mps2, -2.7, 1.5, False),
    ):
        chart = QChart()
        chart.setTitle(title)
        chart.setTitleFont(QFont(font_family, 13, QFont.Weight.DemiBold))
        chart.legend().setFont(QFont(font_family, 10))
        x_axis, y_axis = QValueAxis(), QValueAxis()
        x_axis.setTitleText("Simulation time (s)")
        x_axis.setRange(0.0, samples[-1].time_s)
        x_axis.setLabelFormat("%.1f")
        x_axis.setTickCount(8)
        y_axis.setRange(lower, upper)
        y_axis.setLabelFormat("%.1f")
        for axis in (x_axis, y_axis):
            axis.setLabelsFont(QFont(font_family, 10))
            axis.setTitleFont(QFont(font_family, 10))
        chart.addAxis(x_axis, Qt.AlignmentFlag.AlignBottom)
        chart.addAxis(y_axis, Qt.AlignmentFlag.AlignLeft)
        first = 0
        for phase, steps, _ in PHASES:
            series = QLineSeries()
            series.setName(phase.replace("_", " ").capitalize())
            series.setPen(QPen(QColor(PHASE_COLORS[phase]), 3.0))
            for sample in samples[first:first + steps + 1]:
                series.append(sample.time_s, accessor(sample))
            first += steps
            chart.addSeries(series)
            series.attachAxis(x_axis)
            series.attachAxis(y_axis)
        chart.legend().setVisible(show_legend)
        view = QChartView(chart)
        view.setRenderHint(QPainter.RenderHint.Antialiasing)
        layout.addWidget(view)

    figure.resize(960, 760)
    figure.show()
    app.processEvents()
    output = QSvgGenerator()
    output.setFileName(str(path))
    output.setSize(QSize(960, 760))
    output.setViewBox(QRect(0, 0, 960, 760))
    output.setResolution(96)
    output.setTitle("Flat-ground driving profile")
    figure.render(output)
    figure.close()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True, help="new directory for the CSV and SVG")
    args = parser.parse_args(argv)
    try:
        args.output_dir.mkdir(parents=True, exist_ok=False)
    except FileExistsError:
        parser.error("--output-dir already exists. Select a new directory to preserve the earlier run.")

    samples = drive_samples()
    csv_path, chart_path = (args.output_dir / f"driving-profile.{ext}" for ext in ("csv", "svg"))
    write_csv(csv_path, samples)
    write_chart(chart_path, samples)

    brake_start = next(previous for previous, sample in zip(samples, samples[1:]) if sample.phase == "brake")
    stopped = next(sample for sample in samples if sample.phase == "brake" and sample.vehicle.speed_mps == 0)
    distance = hypot(stopped.vehicle.x_m - brake_start.vehicle.x_m, stopped.vehicle.y_m - brake_start.vehicle.y_m)
    initial_speed = brake_start.vehicle.speed_mps
    print(f"Braking from {initial_speed:.3f} m/s to rest: {distance:.6f} m")
    print(f"Analytical stopping distance: {initial_speed**2 / (2 * BRAKE_DECEL_MPS2):.6f} m")
    print(f"First zero-speed sample after {(stopped.time_s - brake_start.time_s):.2f} s")
    print(f"CSV {csv_path}")
    print(f"SVG {chart_path}")


if __name__ == "__main__":
    main()
