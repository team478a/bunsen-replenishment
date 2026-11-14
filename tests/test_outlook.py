import copy
import csv
from datetime import date, timedelta
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import tkinter as tk
import unittest
from unittest.mock import patch

from bunsen.core import DataError, load
from bunsen.outlook import build_outlook, export_outlook, report_html
from bunsen.ui import LegacyApp as App
from test_workflow import NOW, DAY, ROOT


class OutlookTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name).resolve()
        self.csv = self.folder / 'input'
        self.csv.mkdir()
        for p in (ROOT / 'samples/morning').glob('*.csv'):
            (self.csv / p.name).write_bytes(p.read_bytes())

    def tearDown(self):
        self.temp.cleanup()

    def data(self):
        return load(self.csv, NOW)

    def change(self, table, transform):
        path = self.csv / (table + '.csv')
        reader = csv.DictReader(io.StringIO(path.read_text(encoding='utf-8')))
        rows = list(reader)
        rows = transform(rows)
        with path.open('w', encoding='utf-8', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=reader.fieldnames)
            writer.writeheader(); writer.writerows(rows)

    def extend(self, quantity='60'):
        def append(rows):
            for warehouse in ('KOBE', 'SUGITO'):
                example = next(r for r in rows if r['warehouse'] == warehouse)
                for offset in range(6, 15):
                    rows.append(dict(example, date=(DAY + timedelta(days=offset)).isoformat(), quantity_p=quantity))
            return rows
        self.change('forecast', append)

    def kobe(self, report):
        return [r for r in report['rows'] if r['warehouse'] == 'KOBE']

    def test_daily_baseline_periods_and_explicit_reference_scope(self):
        self.extend()
        data = self.data()
        original = copy.deepcopy(data)
        report = build_outlook(data, DAY, NOW)
        rows = self.kobe(report)
        self.assertEqual(len(report['rows']), 30)
        self.assertEqual([rows[i]['period'] for i in (0, 1, 3, 4, 7, 8, 14)], ['当日', '1〜3日先', '1〜3日先', '4〜7日先', '4〜7日先', '8〜14日先', '8〜14日先'])
        self.assertEqual(rows[0]['opening_p'], -60)
        self.assertEqual(rows[0]['projected_closing_p'], -120)
        self.assertEqual(rows[14]['projected_closing_p'], -960)
        self.assertEqual(rows[0]['status'], 'shortage_risk')
        self.assertEqual(report['status'], 'reference_only')
        self.assertFalse(report['formal_issue_allowed'])
        self.assertFalse(report['planned_replenishment_included'])
        self.assertEqual(data, original)

    def test_missing_forecast_propagates_unknown_without_hiding_later_known_demand(self):
        self.extend()
        gap = str(DAY + timedelta(days=3))
        self.change('forecast', lambda rows: [r for r in rows if not (r['warehouse'] == 'KOBE' and r['date'] == gap)])
        report = build_outlook(self.data(), DAY, NOW)
        rows = self.kobe(report)
        self.assertIsNotNone(rows[2]['projected_closing_p'])
        self.assertIsNone(rows[3]['forecast_p'])
        self.assertTrue(all(r['projected_closing_p'] is None for r in rows[3:]))
        self.assertEqual(rows[4]['forecast_p'], 60)
        self.assertIn(gap, rows[14]['reason'])
        issue = next(i for i in report['issues'] if i['code'] == 'FORECAST_MISSING')
        self.assertEqual((issue['product'], issue['warehouse'], issue['date'], issue['field']), ('P001', 'KOBE', gap, 'quantity_p'))
        self.assertIsNone(issue['line'])
        self.assertTrue(all(r['projected_closing_p'] is not None for r in report['rows'] if r['warehouse'] == 'SUGITO'))

    def test_zero_forecast_is_known_and_does_not_break_balance(self):
        self.extend('0')
        self.change('forecast', lambda rows: [dict(r, quantity_p='0') for r in rows])
        report = build_outlook(self.data(), DAY, NOW)
        self.assertEqual(report['issues'], [])
        self.assertTrue(all(r['projected_closing_p'] == 0 and r['status'] == 'reference' for r in report['rows']))

    def test_missing_history_makes_entire_outlook_unknown_and_large_range_is_bounded(self):
        self.extend()
        self.change('forecast', lambda rows: [r for r in rows if r['date'] != '2026-11-08'])
        report = build_outlook(self.data(), DAY, NOW)
        self.assertTrue(all(r['opening_p'] is None for r in report['rows']))
        self.assertEqual({r['code'] for r in report['issues']}, {'FORECAST_HISTORY_MISSING'})
        data = self.data()
        for row in data.tables['inventory']:
            row['as_of'] = '0002-01-01T08:00:00+09:00'
        report = build_outlook(data, DAY, NOW)
        self.assertEqual(len(report['rows']), 30)

    def test_transit_on_exact_day_and_intraday_warning(self):
        self.extend()
        self.change('transit', lambda rows: [dict(shipment_id='T001', warehouse='KOBE', product='P001', quantity_p='500', arrival_date='2026-11-12', confirmed='yes')])
        rows = self.kobe(build_outlook(self.data(), DAY, NOW))
        self.assertEqual(rows[2]['projected_closing_p'], -240)
        self.assertEqual(rows[3]['transit_p'], 500)
        self.assertEqual(rows[3]['projected_closing_p'], 200)
        self.assertTrue(rows[3]['timing_unconfirmed'])
        self.assertEqual(rows[3]['shipments'][0]['shipment_id'], 'T001')

    def test_transit_basis_mismatch_and_duplicate_arrival_are_unknown(self):
        self.extend()
        self.change('policies', lambda rows: [dict(r, transit_checked_at='2026-11-08T07:00:00+09:00') if r['warehouse'] == 'KOBE' else r for r in rows])
        report = build_outlook(self.data(), DAY, NOW)
        self.assertTrue(all(r['transit_p'] is None and r['projected_closing_p'] is None for r in self.kobe(report)))
        issue = next(i for i in report['issues'] if i['code'] == 'TRANSIT_SNAPSHOT')
        self.assertEqual(issue['line'], 2)
        self.change('transit', lambda rows: [dict(shipment_id='T001', warehouse='SUGITO', product='P001', quantity_p='100', arrival_date='2026-11-08', confirmed='yes')])
        report = build_outlook(self.data(), DAY, NOW)
        self.assertIn('TRANSIT_ALREADY_ARRIVED', {i['code'] for i in report['issues']})

    def test_future_inventory_and_future_forecast_basis_block_reference(self):
        self.extend()
        data = self.data()
        report = build_outlook(data, DAY - timedelta(days=2), NOW)
        self.assertIn('INVENTORY_AFTER_START', {i['code'] for i in report['issues']})
        self.assertTrue(all(r['transit_p'] is None for r in report['rows']))
        target = next(r for r in data.tables['forecast'] if r['warehouse'] == 'KOBE' and r['date'] == str(DAY))
        target['as_of'] = '2026-11-10T08:00:00+09:00'
        report = build_outlook(data, DAY, NOW)
        issue = next(i for i in report['issues'] if i['code'] == 'FORECAST_AFTER_START')
        self.assertEqual(issue['line'], 3)
        self.assertTrue(all(r['projected_closing_p'] is None for r in self.kobe(report)))

    def test_dry_stock_and_shipping_settings_do_not_create_future_replenishment(self):
        self.extend()
        data = self.data()
        expected = build_outlook(data, DAY, NOW)
        data.tables['dry_stock'] = []
        for policy in data.tables['policies']:
            policy.update(safety_p=999999, coverage_days=999999)
        for warehouse in data.tables['warehouses']:
            warehouse['lead_days'] = 999999
        result = build_outlook(data, DAY, NOW)
        self.assertEqual(expected['rows'], result['rows'])

    def test_output_escape_protection_and_partial_write_rollback(self):
        data = self.data()
        data.tables['products'][0]['name'] = '<script>bad</script>'
        report = build_outlook(data, DAY, NOW)
        self.assertIn('&lt;script&gt;', report_html(report))
        self.assertNotIn('<script>', report_html(report))
        path = self.folder / 'reference.json'
        export_outlook(report, path, self.csv)
        with self.assertRaises(DataError):
            export_outlook(report, path, self.csv)
        with self.assertRaises(DataError):
            export_outlook(report, self.csv / 'reference.json', self.csv)
        with self.assertRaises(DataError):
            export_outlook(report, self.folder / 'bad.txt')
        original = Path.open
        def fail(path, *args, **kwargs):
            if path.suffix == '.html':
                raise OSError('disk error')
            return original(path, *args, **kwargs)
        with patch.object(Path, 'open', fail), self.assertRaises(OSError):
            export_outlook(report, self.folder / 'failed.json')
        self.assertFalse((self.folder / 'failed.json').exists())

    def test_date_upper_bound_is_clear_error(self):
        with self.assertRaises(DataError):
            build_outlook(self.data(), date.max, NOW)

    def test_cli_partial_output_is_two_and_never_opens_business_db(self):
        env = dict(os.environ, LOCALAPPDATA=str(self.folder / 'business'))
        command = [sys.executable, '-m', 'bunsen.tools', 'outlook', str(self.csv), '--start', str(DAY), '--output', str(self.folder / 'outlook.json')]
        result = subprocess.run(command, cwd=ROOT, env=env, capture_output=True)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertFalse(json.loads(result.stdout)['formal_issue_allowed'])
        self.assertTrue((self.folder / 'outlook.html').exists())
        self.assertFalse((self.folder / 'business').exists())

    def test_cli_complete_and_invalid_input_have_distinct_codes(self):
        self.extend()
        command = [sys.executable, '-m', 'bunsen.tools', 'outlook', str(self.csv), '--start', str(DAY), '--output', str(self.folder / 'complete.json')]
        result = subprocess.run(command, cwd=ROOT, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['status'], 'reference_only')
        (self.csv / 'inventory.csv').unlink()
        command[-1] = str(self.folder / 'invalid.json')
        result = subprocess.run(command, cwd=ROOT, capture_output=True)
        self.assertEqual(result.returncode, 1)
        self.assertFalse((self.folder / 'invalid.json').exists())

    def test_failed_import_clears_previous_outlook(self):
        root = tk.Tk(); root.withdraw()
        app = App(root, self.folder / 'home')
        app.service.clock = lambda: NOW
        try:
            app.data = self.data()
            app.day.set(str(DAY)); app.show_outlook()
            self.assertIsNotNone(app.outlook_report)
            (self.csv / 'inventory.csv').unlink()
            with patch('bunsen.ui.filedialog.askdirectory', return_value=str(self.csv)), self.assertRaises(DataError):
                app.import_data()
            self.assertIsNone(app.outlook_report)
            self.assertIsNone(app.data)
            with self.assertRaises(DataError):
                app.show_outlook()
        finally:
            app.close()

    def test_gui_does_not_modify_working_lines_or_db_and_invalidates_on_day_change(self):
        root = tk.Tk(); root.withdraw()
        app = App(root, self.folder / 'home')
        app.service.clock = lambda: NOW
        try:
            app.data = self.data()
            app.folder = str(self.csv)
            app.day.set(str(DAY)); app.compute()
            lines, data = app.lines, app.data
            before = app.service.db_path.read_bytes()
            app.ack.set(True)
            app.show_outlook()
            self.assertIs(app.lines, lines)
            self.assertIs(app.data, data)
            self.assertTrue(app.ack.get())
            self.assertEqual(app.service.db_path.read_bytes(), before)
            app.outlook_tree.selection_set('0'); app.select_outlook()
            self.assertIn('inventory_as_of', app.outlook_details.get('1.0', 'end'))
            path = self.folder / 'gui.json'
            with patch('bunsen.ui.filedialog.asksaveasfilename', return_value=str(path)), patch('bunsen.ui.webbrowser.open'):
                app.save_outlook()
            self.assertTrue(path.exists())
            app.day.set('2026-11-10')
            self.assertIsNone(app.outlook_report)
            self.assertEqual(str(app.outlook_save.cget('state')), 'disabled')
            self.assertFalse(app.outlook_tree.get_children())
            with self.assertRaises(DataError):
                app.save_outlook()
        finally:
            app.close()
