import hashlib
import json
import os
from pathlib import Path
import tempfile
import tkinter as tk
import unittest
from unittest.mock import patch

from bunsen.core import DataError, calculate, inspect_folder, load
from bunsen.diagnostics import diagnose, export_report, report_html
from bunsen.input_guard import InputGuard
from bunsen.service import Service
from bunsen.ui import LegacyApp as App
from test_workflow import ROOT, DAY, NOW


class InputGuardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.csv = self.root / 'csv'
        self.csv.mkdir()
        for path in (ROOT / 'samples/morning').glob('*.csv'):
            (self.csv / path.name).write_bytes(path.read_bytes())

    def tearDown(self):
        self.temp.cleanup()

    def hook(self, trigger, action, count=1):
        original = Path.read_bytes
        seen = {}
        def read(path):
            content = original(path)
            if path.parent == self.csv:
                seen[path.name] = seen.get(path.name, 0) + 1
                if path.name == trigger and seen[path.name] == count:
                    action()
            return content
        return patch.object(Path, 'read_bytes', read)

    def change_inventory(self):
        path = self.csv / 'inventory.csv'
        initial = path.stat()
        path.write_bytes(path.read_bytes().replace(b',0,', b',1,'))
        # Deliberately observable metadata change, independent of filesystem clock precision.
        os.utime(path, ns=(initial.st_atime_ns, initial.st_mtime_ns + 1_000_000_000))

    def assert_changed(self, result, name):
        self.assertIsNone(result.dataset)
        self.assertIn(name, result.read_consistency['changed_files'])
        self.assertIn('INPUT_CHANGED', {i.code for i in result.issues})
        self.assertEqual(next(f for f in result.files if f['filename'] == name)['status'], 'source_changed')

    def test_stable_input_preserves_normalized_raw_hash_and_observation_limits(self):
        raw = {p.name: p.read_text(encoding='utf-8-sig') for p in self.csv.iterdir()}
        expected = hashlib.sha256(json.dumps(raw, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        before = {p.name: p.stat().st_mtime_ns for p in self.csv.iterdir()}
        result = inspect_folder(self.csv, NOW)
        self.assertEqual(result.dataset.digest, expected)
        self.assertEqual(result.dataset.raw, raw)
        self.assertEqual(result.read_consistency['status'], 'stable_observed')
        self.assertFalse(result.read_consistency['atomic_snapshot'])
        self.assertEqual(result.read_consistency['changed_files'], [])
        self.assertEqual(before, {p.name: p.stat().st_mtime_ns for p in self.csv.iterdir()})

    def test_already_read_file_change_is_rejected(self):
        with self.hook('forecast.csv', self.change_inventory):
            result = inspect_folder(self.csv, NOW)
        self.assert_changed(result, 'inventory.csv')

    def test_not_yet_read_file_change_is_rejected_by_initial_metadata(self):
        with self.hook('products.csv', self.change_inventory):
            result = inspect_folder(self.csv, NOW)
        self.assert_changed(result, 'inventory.csv')

    def test_same_size_and_restored_mtime_still_compares_actual_bytes(self):
        path = self.csv / 'inventory.csv'
        initial = path.stat()
        def change():
            self.change_inventory()
            os.utime(path, ns=(initial.st_atime_ns, initial.st_mtime_ns))
        # Simulate all observed metadata also remaining unchanged on the filesystem.
        with patch('bunsen.input_guard._signature', return_value=(1, 1, 1, 1, 1, 1)), self.hook('forecast.csv', change):
            result = inspect_folder(self.csv, NOW)
        self.assert_changed(result, 'inventory.csv')
        self.assertIn('内容', next(i.message for i in result.issues if i.code == 'INPUT_CHANGED'))
        self.assertEqual(path.stat().st_size, initial.st_size)
        self.assertEqual(path.stat().st_mtime_ns, initial.st_mtime_ns)

    def test_identical_bytes_with_changed_metadata_are_not_silently_accepted(self):
        path = self.csv / 'inventory.csv'
        initial = path.stat()
        def touch():
            os.utime(path, ns=(initial.st_atime_ns, initial.st_mtime_ns + 1_000_000_000))
        with self.hook('forecast.csv', touch):
            result = inspect_folder(self.csv, NOW)
        self.assert_changed(result, 'inventory.csv')

    def test_replacement_with_same_bytes_and_mtime_is_rejected(self):
        path = self.csv / 'inventory.csv'
        initial = path.stat()
        content = path.read_bytes()
        def replace():
            replacement = self.csv / 'replacement.tmp'
            replacement.write_bytes(content)
            os.utime(replacement, ns=(initial.st_atime_ns, initial.st_mtime_ns))
            os.replace(replacement, path)
        with self.hook('forecast.csv', replace):
            result = inspect_folder(self.csv, NOW)
        self.assert_changed(result, 'inventory.csv')

    def test_added_csv_is_listed_but_not_imported(self):
        def add():
            (self.csv / 'orders.csv').write_bytes(b'unknown')
        with self.hook('forecast.csv', add):
            result = inspect_folder(self.csv, NOW)
        self.assert_changed(result, 'orders.csv')
        file = next(f for f in result.files if f['filename'] == 'orders.csv')
        self.assertIsNone(file['sha256'])
        self.assertIsNone(file['rows'])

    def test_optional_morning_stock_appearing_mid_read_stops_import(self):
        path = self.csv / 'dry_stock.csv'
        original = path.read_bytes()
        path.unlink()
        with self.hook('vehicles.csv', lambda: path.write_bytes(original)):
            result = inspect_folder(self.csv, NOW)
        self.assert_changed(result, 'dry_stock.csv')

    def test_required_or_optional_csv_removed_mid_read_stops_import(self):
        for name in ('inventory.csv', 'dry_stock.csv'):
            with self.subTest(name=name):
                path = self.csv / name
                original = path.read_bytes()
                with self.hook('dry_stock.csv', path.unlink):
                    result = inspect_folder(self.csv, NOW)
                self.assert_changed(result, name)
                path.write_bytes(original)

    def test_change_after_earlier_file_was_rechecked_is_detected_by_final_inventory(self):
        with self.hook('dry_stock.csv', self.change_inventory, count=2):
            result = inspect_folder(self.csv, NOW)
        self.assert_changed(result, 'inventory.csv')

    def test_recheck_read_failure_is_not_a_successful_observation(self):
        original = Path.read_bytes
        seen = 0
        def read(path):
            nonlocal seen
            if path == self.csv / 'inventory.csv':
                seen += 1
                if seen == 2:
                    raise PermissionError('writer holds file')
            return original(path)
        with patch.object(Path, 'read_bytes', read):
            result = inspect_folder(self.csv, NOW)
        self.assertIsNone(result.dataset)
        self.assertEqual(result.read_consistency['status'], 'unverifiable')
        self.assertIn('inventory.csv', result.read_consistency['unverified_files'])
        self.assertIn('INPUT_RECHECK_FAILED', {i.code for i in result.issues})

    def test_directory_recheck_failure_blocks_dataset(self):
        original = InputGuard._inventory
        seen = 0
        def inventory(guard):
            nonlocal seen
            seen += 1
            if seen == 2:
                raise OSError('directory not accessible')
            return original(guard)
        with patch.object(InputGuard, '_inventory', inventory):
            result = inspect_folder(self.csv, NOW)
        self.assertIsNone(result.dataset)
        self.assertEqual(result.read_consistency['status'], 'unverifiable')

    def test_non_csv_side_files_do_not_invalidate_input(self):
        with self.hook('forecast.csv', lambda: (self.csv / 'notes.txt').write_text('note')):
            result = inspect_folder(self.csv, NOW)
        self.assertIsNotNone(result.dataset)
        self.assertEqual(result.read_consistency['status'], 'stable_observed')

    def test_first_read_failure_is_unverifiable_and_names_affected_file(self):
        original = Path.read_bytes
        def read(path):
            if path == self.csv / 'inventory.csv':
                raise PermissionError('writer holds file')
            return original(path)
        with patch.object(Path, 'read_bytes', read):
            result = inspect_folder(self.csv, NOW)
        self.assertIsNone(result.dataset)
        self.assertEqual(result.read_consistency['status'], 'unverifiable')
        self.assertIn('inventory.csv', result.read_consistency['unverified_files'])

    def test_folder_replacement_is_not_mistaken_for_stable_contents(self):
        old = self.root / 'previous-input'
        # Both absolute targets are confined to this test's temporary directory.
        self.assertTrue(self.csv.is_relative_to(self.root) and old.is_relative_to(self.root))
        def replace():
            self.csv.rename(old)
            self.csv.mkdir()
            for path in old.iterdir():
                (self.csv / path.name).write_bytes(path.read_bytes())
        with self.hook('dry_stock.csv', replace):
            result = inspect_folder(self.csv, NOW)
        self.assertIsNone(result.dataset)
        self.assertEqual(result.read_consistency['status'], 'unverifiable')

    def test_missing_folder_observation_is_not_marked_stable(self):
        result = inspect_folder(self.root / 'absent', NOW)
        self.assertIsNone(result.dataset)
        self.assertEqual(result.read_consistency['status'], 'not_checked')
        self.assertFalse(result.read_consistency['atomic_snapshot'])

    def test_legacy_diagnostic_html_has_no_invented_read_check(self):
        report = diagnose(self.csv, DAY, NOW)
        report.pop('read_consistency')
        rendered = report_html(report)
        self.assertIn('未実施', rendered)
        self.assertIn('原子的な一括取得ではありません', rendered)

    def test_diagnostic_displays_change_and_skips_downstream_checks(self):
        with self.hook('forecast.csv', self.change_inventory):
            report = diagnose(self.csv, DAY, NOW)
        self.assertFalse(report['contract_compatible'])
        self.assertFalse(report['calculation_checked'])
        self.assertFalse(report['morning_stock_checked'])
        self.assertFalse(report['formal_issue_allowed'])
        self.assertIsNone(report['dataset_digest'])
        output = self.root / 'diagnostic.json'
        paths = export_report(report, output)
        self.assertEqual(json.loads(output.read_text(encoding='utf-8'))['read_consistency']['status'], 'changed')
        self.assertIn('変更検出・取込停止', paths[1].read_text(encoding='utf-8'))

    def test_service_logs_failed_import_without_successful_import(self):
        service = Service(self.root / 'home/history.db', lambda: NOW)
        try:
            with self.hook('forecast.csv', self.change_inventory), self.assertRaises(DataError):
                service.import_folder(self.csv)
            kinds = [r[0] for r in service.db.execute('SELECT kind FROM events')]
            self.assertEqual(kinds, ['import_failed'])
            self.assertEqual(service.history(), [])
        finally:
            service.close()

    def test_formal_snapshot_reparse_also_rejects_read_changes(self):
        data = load(self.csv, NOW)
        lines = calculate(data, DAY)
        for row in lines:
            row.update(quantity_p=120, lot='L001')
        service = Service(self.root / 'home/history.db', lambda: NOW)
        original = Path.read_bytes
        changed = False
        def read(path):
            nonlocal changed
            result = original(path)
            if path.name == 'forecast.csv' and path.parent != self.csv and not changed:
                changed = True
                inv = path.parent / 'inventory.csv'
                inv.write_bytes(original(inv).replace(b',0,', b',1,'))
            return result
        try:
            with patch.object(Path, 'read_bytes', read), self.assertRaises(DataError) as exc:
                service.issue(data, lines, DAY, '架空担当者', True, True)
            self.assertTrue(changed)
            self.assertIn('INPUT_CHANGED', {i.code for i in exc.exception.issues})
            self.assertEqual(service.history(), [])
        finally:
            service.close()

    def test_gui_failed_import_invalidates_old_data_and_outlook(self):
        root = tk.Tk(); root.withdraw()
        app = App(root, self.root / 'gui-home')
        app.service.clock = lambda: NOW
        try:
            app.data = load(self.csv, NOW)
            app.day.set(str(DAY)); app.compute(); app.show_outlook()
            app.ack.set(True)
            with patch('bunsen.ui.filedialog.askdirectory', return_value=str(self.csv)), self.hook('forecast.csv', self.change_inventory), self.assertRaises(DataError):
                app.import_data()
            self.assertIsNone(app.data)
            self.assertIsNone(app.lines)
            self.assertIsNone(app.outlook_report)
            self.assertFalse(app.ack.get())
            with self.assertRaises(DataError):
                app.issue(True)
            self.assertEqual(app.service.history(), [])
        finally:
            app.close()
