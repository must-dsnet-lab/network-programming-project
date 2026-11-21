import argparse
import csv
import re
import sys
from collections import Counter
from decimal import Decimal, InvalidOperation
from pathlib import Path


TRIAL_GROUPS = ('n0', 'n1', 'n2', 'n3', 'n4-n5')
TRIAL_FILE = re.compile(r'(?P<scenario>.+)-(?P<source>vehicle|relay)-(?P<round>\d+)\.csv')
VEHICLE_FIELDS = (
    'accepted', 'sequence_missing', 'sequence_gap_pct', 'max_accepted_interval_ms',
    'zero_latched_observed', 'last_accept_to_first_lock_ms', 'control_rejected_reasons',
)
RELAY_FIELDS = (
    'forwarded', 'dropped', 'reordered', 'duplicate', 'outages_ended', 'outages_with_drops',
)
REPORT_FIELDS = ('group', 'scenario', 'round', 'status', *VEHICLE_FIELDS, *RELAY_FIELDS)


def _records(path: Path, *fields: str):
    with path.open(encoding='utf-8-sig', newline='') as source:
        reader = csv.DictReader(source, strict=True)
        required = {'elapsed_s', 'event', 'detail', *fields}
        missing = required.difference(reader.fieldnames or ())
        if missing:
            raise ValueError(f"{path.name} missing columns {', '.join(sorted(missing))}")
        for row in reader:
            location = f'{path.name} line {reader.line_num}'
            if None in row or any(value is None for value in row.values()) or not row['event']:
                raise ValueError(f'{location} has an incomplete record')
            yield location, row


def _decimal(value: str, location: str, field: str) -> Decimal:
    try:
        number = Decimal(value)
    except InvalidOperation:
        raise ValueError(f'{location} has invalid {field}') from None
    if not number.is_finite():
        raise ValueError(f'{location} has invalid {field}')
    return number


def _nonnegative_int(value: str, location: str, field: str) -> int:
    if not value.isascii() or not value.isdecimal():
        raise ValueError(f'{location} has invalid {field}')
    return int(value)


def _milliseconds(interval: Decimal | None) -> str:
    return '' if interval is None else f'{interval * 1000:.3f}'


def _summarize_vehicle(path: Path):
    accepted = 0
    sequenced_accepts = 0
    missing = 0
    max_interval = None
    first_lock = None
    lock_interval = None
    previous_accept = None
    previous_seq = None
    latest_session_event = None
    reasons = Counter()

    for location, row in _records(path, 'seq', 'speed_mps', 'fault_latched'):
        event = row['event']
        if event == 'session':
            latest_session_event = row['detail']
            previous_accept = None
            previous_seq = None
        elif event == 'control_accepted':
            elapsed = _decimal(row['elapsed_s'], location, 'elapsed_s')
            accepted += 1
            if previous_accept is not None:
                interval = elapsed - previous_accept
                if interval < 0:
                    raise ValueError(f'{location} has decreasing accepted timestamps')
                max_interval = interval if max_interval is None else max(max_interval, interval)
            previous_accept = elapsed
            if row['seq']:
                seq = _nonnegative_int(row['seq'], location, 'seq')
                sequenced_accepts += 1
                if previous_seq is not None:
                    if seq <= previous_seq:
                        raise ValueError(f'{location} has non-increasing accepted sequence')
                    missing += seq - previous_seq - 1
                previous_seq = seq
            else:
                previous_seq = None
        elif event == 'state' and first_lock is None and row['fault_latched'].lower() == 'true':
            if _decimal(row['speed_mps'], location, 'speed_mps') != 0:
                continue
            first_lock = _decimal(row['elapsed_s'], location, 'elapsed_s')
            if previous_accept is not None:
                lock_interval = first_lock - previous_accept
                if lock_interval < 0:
                    raise ValueError(f'{location} precedes the last accepted command')
        elif event == 'control_rejected':
            reasons[row['detail'] or '(unspecified)'] += 1

    denominator = sequenced_accepts + missing
    metrics = {
        'accepted': accepted,
        'sequence_missing': missing if sequenced_accepts else '',
        'sequence_gap_pct': f'{Decimal(missing * 100) / denominator:.3f}' if denominator else '',
        'max_accepted_interval_ms': _milliseconds(max_interval),
        'zero_latched_observed': 'yes' if first_lock is not None else 'no records',
        'last_accept_to_first_lock_ms': _milliseconds(lock_interval),
        'control_rejected_reasons': ', '.join(
            f'{reason}={count}' for reason, count in sorted(reasons.items())
        ) or 'no records',
    }
    return metrics, latest_session_event == 'closed'


def _summarize_relay(path: Path):
    events = Counter()
    forwarded = Counter()
    outages_with_drops = 0
    latest_event = None
    for location, row in _records(path):
        latest_event = row['event']
        events[latest_event] += 1
        if latest_event == 'packet_forwarded':
            forwarded[row['detail']] += 1
        elif latest_event == 'outage_ended':
            dropped = _nonnegative_int(row.get('dropped_packets', ''), location, 'dropped_packets')
            outages_with_drops += dropped > 0

    metrics = {
        'forwarded': events['packet_forwarded'],
        'dropped': events['packet_dropped'],
        'reordered': forwarded['reordered'],
        'duplicate': forwarded['duplicate'],
        'outages_ended': events['outage_ended'],
        'outages_with_drops': outages_with_drops,
    }
    return metrics, latest_event == 'relay_stopped'


def summarize_trials(root: Path) -> list[dict[str, str | int]]:
    files = {}
    for group in TRIAL_GROUPS:
        for path in (root / group).glob('*.csv'):
            match = TRIAL_FILE.fullmatch(path.name)
            if match and path.is_file():
                key = (group, match['scenario'], match['round'])
                files.setdefault(key, {})[match['source']] = path

    reports = []
    for (group, scenario, round_id), paths in sorted(files.items()):
        report = dict.fromkeys(REPORT_FIELDS, '')
        report.update(group=group, scenario=scenario, round=round_id)
        issues = []
        for source, summarize, missing_marker in (
            ('vehicle', _summarize_vehicle, 'vehicle session close missing'),
            ('relay', _summarize_relay, 'relay stop missing'),
        ):
            path = paths.get(source)
            if path is None:
                issues.append(f'{source} log missing')
                continue
            try:
                metrics, closed = summarize(path)
            except (OSError, ValueError, csv.Error) as error:
                issues.append(f'{source} log unreadable ({error})')
                continue
            report.update(metrics)
            if not closed:
                issues.append(missing_marker)

        accepts, forwards = report['accepted'], report['forwarded']
        if isinstance(accepts, int) and isinstance(forwards, int) and accepts > forwards:
            issues.append('vehicle accepts exceed relay forwards')
        report['status'] = f"incomplete ({'; '.join(issues)})" if issues else 'closed'
        reports.append(report)
    return reports


def _print_report(reports: list[dict[str, str | int]]) -> None:
    columns = (
        ('Trial', 'scenario', 18), ('Run', 'round', 4), ('Accept', 'accepted', 7),
        ('Gap', 'sequence_missing', 5), ('Gap%', 'sequence_gap_pct', 7),
        ('Max ms', 'max_accepted_interval_ms', 9),
        ('Lock ms', 'last_accept_to_first_lock_ms', 9),
        ('Fwd', 'forwarded', 7), ('Drop', 'dropped', 6), ('Order', 'reordered', 6),
        ('Dup', 'duplicate', 5), ('Out', 'outages_ended', 5),
        ('Hit', 'outages_with_drops', 5),
    )
    print(' '.join(f'{label:<{width}}' for label, _, width in columns).rstrip())
    for report in reports:
        fields = [f'{str(report[key]) or "-":<{width}}' for _, key, width in columns]
        print(' '.join(fields).rstrip())
        print(f"  Log status {report['status']} | Zero-latched {report['zero_latched_observed'] or '-'}"
              f" | Control rejection reasons {report['control_rejected_reasons'] or '-'}")
    print('Gap and Max ms use within-session accepts. Lock ms runs from last accept to first zero-latched state.')
    print('Order and Dup are forwarded copies. Out is outages ended. Hit is outages with recorded drops.')


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Summarize recorded network trial CSVs offline.')
    parser.add_argument('--root', type=Path, default=Path('local/n-tests'), help='network trial directory')
    parser.add_argument('--output', type=Path, help='optional path for a new summary CSV')
    args = parser.parse_args(argv)

    try:
        reports = summarize_trials(args.root)
        if not reports:
            raise ValueError(f'No network trial logs found in {args.root}')
        if args.output is not None:
            with args.output.open('x', encoding='utf-8', newline='') as output:
                writer = csv.DictWriter(output, fieldnames=REPORT_FIELDS)
                writer.writeheader()
                writer.writerows(reports)
    except (OSError, ValueError) as error:
        print(error, file=sys.stderr)
        return 1

    _print_report(reports)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
