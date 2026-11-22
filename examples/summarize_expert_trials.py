"""Compare the advanced and expert watchdog trials using recorded vehicle events."""

import argparse
import csv
import re
import sys
from dataclasses import dataclass
from decimal import Decimal, DecimalException
from pathlib import Path
from statistics import median


MODES = ('advanced', 'expert')
TRIAL_DIRECTORY = re.compile(r'(advanced|expert)-([0-9]{2,})')
VEHICLE_FIELDS = {'elapsed_s', 'event', 'seq', 'speed_mps', 'fault_latched', 'detail'}
REPORT_FIELDS = ('mode', 'run', 'watchdog_ms')
EXPECTED_STATES = ((0, False), (1, False), (0, False), (1, False), (0, True))


@dataclass(frozen=True)
class TrialResult:
    mode: str
    run: int
    watchdog_ms: Decimal


@dataclass(frozen=True)
class VehicleEvent:
    elapsed_s: Decimal
    name: str
    speed_mps: Decimal
    fault_latched: bool


def _decimal(value: str, field: str, location: str) -> Decimal:
    try:
        number = Decimal(value)
    except DecimalException:
        raise ValueError(f'{location} has invalid {field}') from None
    if not number.is_finite():
        raise ValueError(f'{location} has invalid {field}')
    return number


def _vehicle_events(path: Path) -> list[VehicleEvent]:
    events = []
    try:
        with path.open(encoding='utf-8-sig', newline='') as source:
            reader = csv.DictReader(source, strict=True)
            columns = reader.fieldnames or ()
            missing = VEHICLE_FIELDS.difference(columns)
            if missing:
                raise ValueError(f"{path} missing CSV columns {', '.join(sorted(missing))}")
            if len(columns) != len(set(columns)):
                raise ValueError(f'{path} has duplicate CSV columns')

            for row in reader:
                location = f'{path} line {reader.line_num}'
                if None in row or any(value is None for value in row.values()) or not row['event']:
                    raise ValueError(f'{location} has an incomplete record')
                elapsed_s = _decimal(row['elapsed_s'], 'elapsed_s', location)
                speed_mps = _decimal(row['speed_mps'], 'speed_mps', location)
                if elapsed_s < 0:
                    raise ValueError(f'{location} has negative elapsed_s')
                fault = row['fault_latched'].lower()
                if fault not in ('true', 'false'):
                    raise ValueError(f'{location} has invalid fault_latched')
                events.append(VehicleEvent(elapsed_s, row['event'], speed_mps, fault == 'true'))
    except csv.Error as error:
        raise ValueError(f'{path} has invalid CSV ({error})') from error

    return sorted(events, key=lambda event: event.elapsed_s)


def _watchdog_ms(path: Path) -> Decimal:
    events = _vehicle_events(path)
    states = [event for event in events if event.name == 'state']
    observed = tuple((event.speed_mps, event.fault_latched) for event in states[:5])
    if observed != EXPECTED_STATES:
        raise ValueError(f'{path} is missing the expected state sequence 0 → 1 → 0 → 1 → 0 (latched)')

    first_motion, normal_stop, second_motion, lock = states[1:5]
    if not first_motion.elapsed_s < normal_stop.elapsed_s < second_motion.elapsed_s < lock.elapsed_s:
        raise ValueError(f'{path} has overlapping state timestamps')

    accepted = [event for event in events if event.name == 'control_accepted']
    if not any(event.speed_mps == 0 and not event.fault_latched
               and first_motion.elapsed_s < event.elapsed_s <= normal_stop.elapsed_s for event in accepted):
        raise ValueError(f'{path} is missing the accepted normal stop command')
    last_accept = next(
        (event for event in reversed(accepted) if normal_stop.elapsed_s < event.elapsed_s < lock.elapsed_s),
        None,
    )
    if last_accept is None:
        raise ValueError(f'{path} is missing an accepted moving command before the latch')
    if last_accept.speed_mps != 1 or last_accept.fault_latched:
        raise ValueError(f'{path} has an inconsistent last accepted command before the latch')
    if any(event.name == 'authority_lease_expired' and last_accept.elapsed_s <= event.elapsed_s <= lock.elapsed_s
           for event in events):
        raise ValueError(f'{path} has a lease expiry before the watchdog latch')
    try:
        return (lock.elapsed_s - last_accept.elapsed_s) * 1000
    except DecimalException:
        raise ValueError(f'{path} has invalid watchdog timing') from None


def summarize_trials(root: Path, *, runs: int | None = None) -> list[TrialResult]:
    if runs is not None and runs < 1:
        raise ValueError('--runs must be positive')

    directories: dict[str, dict[int, Path]] = {mode: {} for mode in MODES}
    for directory in root.iterdir():
        match = TRIAL_DIRECTORY.fullmatch(directory.name)
        if match and directory.is_dir():
            mode, run_text = match.groups()
            number = int(run_text)
            if number < 1 or number in directories[mode]:
                raise ValueError(f'{directory} has an invalid or duplicate trial number')
            directories[mode][number] = directory

    if runs is None:
        runs = max((number for mode in directories.values() for number in mode), default=0)
    if not runs:
        raise ValueError(f'No advanced or expert trial directories in {root}')

    trials = []
    for mode, paths in directories.items():
        for expected, run in enumerate(sorted(paths), 1):
            if run > runs or expected != run:
                if expected < run and expected <= runs:
                    raise ValueError(f'Missing trial {root / f"{mode}-{expected:02d}"}')
                raise ValueError(f'Unexpected trial {paths[run]} with --runs {runs}')
            path = paths[run] / 'vehicle-events.csv'
            trials.append(TrialResult(mode, run, _watchdog_ms(path)))
        if len(paths) < runs:
            raise ValueError(f'Missing trial {root / f"{mode}-{len(paths) + 1:02d}"}')
    return trials


def _print_trials(trials: list[TrialResult]) -> None:
    print('Mode      Run  Watchdog ms')
    for trial in trials:
        print(f'{trial.mode:9} {trial.run:02d}   {trial.watchdog_ms:11.3f}')
    print('\nMode      n     Min ms   Median ms      Max ms')
    for mode in MODES:
        values = [trial.watchdog_ms for trial in trials if trial.mode == mode]
        print(f'{mode:9} {len(values):2} {min(values):10.3f} {median(values):11.3f} {max(values):11.3f}')
    print('Watchdog time is measured from the last accepted moving command to the first zero-latched state.')


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True, help='directory containing advanced-01 and expert-01')
    parser.add_argument('--runs', type=int, help='expected number of rounds per mode')
    parser.add_argument('--output', type=Path, help='optional path for a new per-run CSV')
    args = parser.parse_args(argv)

    try:
        trials = summarize_trials(args.root, runs=args.runs)
        if args.output is not None:
            with args.output.open('x', encoding='utf-8', newline='') as output:
                writer = csv.writer(output)
                writer.writerow(REPORT_FIELDS)
                writer.writerows((trial.mode, f'{trial.run:02d}', f'{trial.watchdog_ms:.3f}') for trial in trials)
    except (OSError, ValueError) as error:
        print(error, file=sys.stderr)
        return 1

    _print_trials(trials)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
