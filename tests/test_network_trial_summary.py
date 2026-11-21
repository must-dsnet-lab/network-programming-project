import csv
import io
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from examples.summarize_network_trials import main, summarize_trials


VEHICLE_FIELDS = ('elapsed_s', 'event', 'seq', 'speed_mps', 'fault_latched', 'detail')
RELAY_FIELDS = ('elapsed_s', 'event', 'dropped_packets', 'detail')


class TestNetworkTrialSummary(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)

    def write_rows(self, name, fields, rows):
        destination = self.root / 'n3' / name
        destination.parent.mkdir(exist_ok=True)
        with destination.open('w', encoding='utf-8', newline='') as output:
            writer = csv.writer(output)
            writer.writerow(fields)
            writer.writerows(rows)

    def test_reports_within_session_gaps_and_first_lock_with_matching_logs(self):
        self.write_rows('n3-example-vehicle-01.csv', VEHICLE_FIELDS, [
            ('0.1', 'session', '', '', '', 'opened'),
            ('0.2', 'control_accepted', '4', '', '', ''),
            ('0.3', 'control_accepted', '7', '', '', ''),
            ('0.8', 'state', '', '0.0', 'true', ''),
            ('0.9', 'session', '', '', '', 'closed'),
            ('1.0', 'session', '', '', '', 'opened'),
            ('1.1', 'control_accepted', '0', '', '', ''),
            ('1.3', 'control_accepted', '1', '', '', ''),
            ('1.4', 'control_rejected', '1', '', '', 'sequence'),
            ('1.5', 'control_rejected', '1', '', '', 'sequence'),
            ('1.6', 'session', '', '', '', 'closed'),
        ])
        self.write_rows('n3-example-relay-01.csv', RELAY_FIELDS, [
            ('0', 'relay_started', '', ''),
            ('0.1', 'packet_forwarded', '', 'original'),
            ('0.2', 'packet_forwarded', '', 'reordered'),
            ('0.3', 'packet_forwarded', '', 'duplicate'),
            ('0.4', 'packet_forwarded', '', 'original'),
            ('0.5', 'packet_dropped', '1', ''),
            ('0.6', 'outage_ended', '1', ''),
            ('0.7', 'outage_ended', '0', ''),
            ('0.8', 'relay_stopped', '', ''),
        ])

        report, = summarize_trials(self.root)

        self.assertEqual(report['status'], 'closed')
        self.assertEqual(report['accepted'], 4)
        self.assertEqual(report['sequence_missing'], 2)
        self.assertEqual(report['sequence_gap_pct'], '33.333')
        self.assertEqual(report['max_accepted_interval_ms'], '200.000')
        self.assertEqual(report['last_accept_to_first_lock_ms'], '500.000')
        self.assertEqual(report['control_rejected_reasons'], 'sequence=2')
        self.assertEqual(report['forwarded'], 4)
        self.assertEqual(report['dropped'], 1)
        self.assertEqual(report['reordered'], 1)
        self.assertEqual(report['duplicate'], 1)
        self.assertEqual(report['outages_ended'], 2)
        self.assertEqual(report['outages_with_drops'], 1)

    def test_keeps_missing_measurements_blank_and_identifies_incomplete_logs(self):
        self.write_rows('n3-example-vehicle-01.csv', VEHICLE_FIELDS, [
            ('0', 'session', '', '', '', 'opened'),
            ('0.5', 'state', '', '0.0', 'true', ''),
        ])
        self.write_rows('n3-example-relay-02.csv', RELAY_FIELDS, [
            ('0', 'relay_started', '', ''),
        ])

        first, second = summarize_trials(self.root)

        self.assertEqual(first['accepted'], 0)
        self.assertEqual(first['last_accept_to_first_lock_ms'], '')
        self.assertEqual(first['max_accepted_interval_ms'], '')
        self.assertEqual(first['sequence_gap_pct'], '')
        self.assertEqual(first['control_rejected_reasons'], 'no records')
        self.assertIn('relay log missing', first['status'])
        self.assertIn('vehicle session close missing', first['status'])
        self.assertIn('vehicle log missing', second['status'])
        self.assertIn('relay stop missing', second['status'])

    def test_cli_csv_output_uses_exclusive_creation(self):
        self.write_rows('n3-example-vehicle-01.csv', VEHICLE_FIELDS, [
            ('0', 'session', '', '', '', 'opened'),
            ('0.5', 'session', '', '', '', 'closed'),
        ])
        destination = self.root / 'report.csv'
        args = ['--root', str(self.root), '--output', str(destination)]

        with redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(args), 0)
        self.assertIn('n3-example', output.getvalue())
        first_output = destination.read_bytes()
        self.assertIn(b'last_accept_to_first_lock_ms', first_output)
        with redirect_stderr(io.StringIO()) as error:
            self.assertNotEqual(main(args), 0)
        self.assertIn('File exists', error.getvalue())
        self.assertEqual(destination.read_bytes(), first_output)
