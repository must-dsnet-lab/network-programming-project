import csv
import io
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from decimal import Decimal
from pathlib import Path

from examples.summarize_expert_trials import main, summarize_trials


FIELDS = ('elapsed_s', 'event', 'seq', 'speed_mps', 'fault_latched', 'detail')


def trial_rows(lock: str = '1.740') -> list[tuple[str, ...]]:
    return [
        ('0.000', 'state', '', '0.0', 'false', 'initial'),
        ('0.150', 'control_accepted', '0', '1.0', 'false', ''),
        ('0.160', 'state', '', '1.0', 'false', ''),
        ('0.500', 'control_accepted', '1', '0.0', 'false', ''),
        ('0.510', 'state', '', '0.0', 'false', ''),
        ('1.000', 'control_accepted', '2', '1.0', 'false', ''),
        ('1.010', 'state', '', '1.0', 'false', ''),
        ('1.220', 'control_accepted', '3', '1.0', 'false', ''),
        (lock, 'state', '', '0.0', 'true', ''),
        ('2.100', 'state', '', '0.0', 'false', ''),
        ('2.300', 'control_accepted', '0', '1.0', 'false', ''),
        ('2.310', 'state', '', '1.0', 'false', ''),
    ]


class TestExpertTrialSummary(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)

    def write_trial(self, directory, rows, fields=FIELDS):
        path = self.root / directory / 'vehicle-events.csv'
        path.parent.mkdir(exist_ok=True)
        with path.open('w', encoding='utf-8', newline='') as stream:
            writer = csv.writer(stream)
            writer.writerow(fields)
            writer.writerows(rows)

    def test_uses_car_timestamps_and_first_latch_in_each_mode(self):
        self.write_trial('advanced-01', trial_rows('1.726'))
        rows = trial_rows('1.751')
        rows[7], rows[8] = rows[8], rows[7]
        self.write_trial('expert-01', rows)

        advanced, expert = summarize_trials(self.root, runs=1)

        self.assertEqual((advanced.mode, advanced.run), ('advanced', 1))
        self.assertEqual(advanced.watchdog_ms, Decimal('506'))
        self.assertEqual((expert.mode, expert.run), ('expert', 1))
        self.assertEqual(expert.watchdog_ms, Decimal('531'))

    def test_does_not_require_another_accepted_row_after_start_of_second_motion(self):
        rows = trial_rows('1.506')
        del rows[7]
        self.write_trial('advanced-01', rows)
        self.write_trial('expert-01', rows)

        self.assertEqual([trial.watchdog_ms for trial in summarize_trials(self.root)],
                         [Decimal('506'), Decimal('506')])

    def test_reports_missing_rounds_or_vehicle_logs(self):
        self.write_trial('advanced-01', trial_rows())
        self.write_trial('expert-01', trial_rows())
        with self.assertRaisesRegex(ValueError, 'advanced-02'):
            summarize_trials(self.root, runs=2)
        (self.root / 'advanced-02').mkdir()
        self.write_trial('expert-02', trial_rows())
        with redirect_stderr(io.StringIO()) as errors:
            self.assertEqual(main(['--root', str(self.root), '--runs', '2']), 1)
        self.assertIn('advanced-02', errors.getvalue())
        self.assertIn('vehicle-events.csv', errors.getvalue())

    def test_rejects_invalid_csv_and_incomplete_timeline(self):
        self.write_trial('advanced-01', trial_rows())
        invalid_runs = (
            (trial_rows(), FIELDS[:-1], 'missing CSV column'),
            ([('NaN', *trial_rows()[0][1:]), *trial_rows()[1:]], FIELDS, 'elapsed_s'),
            ([*trial_rows()[:3], *trial_rows()[5:]], FIELDS, 'state sequence'),
            ([*trial_rows()[:5], *trial_rows()[6:7], *trial_rows()[8:]], FIELDS, 'accepted moving command'),
        )
        for rows, fields, error in invalid_runs:
            with self.subTest(error=error):
                self.write_trial('expert-01', rows, fields)
                with self.assertRaisesRegex(ValueError, error):
                    summarize_trials(self.root)

    def test_rejects_inconsistent_final_acceptance_before_latch(self):
        self.write_trial('advanced-01', trial_rows())
        for speed, fault in (('0.0', 'false'), ('1.0', 'true')):
            with self.subTest(speed=speed, fault=fault):
                rows = trial_rows()
                rows.insert(8, ('1.250', 'control_accepted', '4', speed, fault, ''))
                self.write_trial('expert-01', rows)
                with self.assertRaisesRegex(ValueError, 'last accepted command'):
                    summarize_trials(self.root)

    def test_prints_mode_statistics_and_creates_chart_csv_without_overwriting(self):
        for mode, locks in (
            ('advanced', ('1.722', '1.733', '1.741')),
            ('expert', ('1.725', '1.740', '1.735')),
        ):
            for number, lock in enumerate(locks, 1):
                self.write_trial(f'{mode}-{number:02d}', trial_rows(lock))

        destination = self.root / 'watchdogs.csv'
        arguments = ['--root', str(self.root), '--runs', '3', '--output', str(destination)]
        with redirect_stdout(io.StringIO()) as report:
            self.assertEqual(main(arguments), 0)
        self.assertRegex(report.getvalue(), r'advanced\s+3\s+502\.000\s+513\.000\s+521\.000')
        self.assertRegex(report.getvalue(), r'expert\s+3\s+505\.000\s+515\.000\s+520\.000')
        with destination.open(encoding='utf-8', newline='') as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual(len(rows), 6)
        self.assertEqual(rows[0], {'mode': 'advanced', 'run': '01', 'watchdog_ms': '502.000'})
        with redirect_stderr(io.StringIO()):
            self.assertEqual(main(arguments), 1)
        with destination.open(encoding='utf-8', newline='') as stream:
            self.assertEqual(list(csv.DictReader(stream)), rows)
