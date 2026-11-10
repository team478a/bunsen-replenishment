from datetime import date, datetime
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import tkinter as tk
import unittest
from unittest.mock import patch

from bunsen.acceptance import CASES, create_kit, prepare_trial_home
from bunsen.backup import create_backup, restore_backup
from bunsen.core import DataError, JST, calculate, load
from bunsen.service import Service, report_html
from bunsen.ui import App
from test_workflow import ROOT

DAY = date(2026, 11, 10)
NOW = datetime(2026, 11, 10, 9, tzinfo=JST)


class AcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.kit = self.root / '日本語 受入キット'

    def tearDown(self):
        self.temp.cleanup()

    def create(self):
        return create_kit(self.kit, DAY, NOW)

    def test_shift_preserves_quantities_relative_expiry_and_sources(self):
        original = {p: p.read_bytes() for p in (ROOT / 'samples').rglob('*.csv')}
        manifest = self.create()
        self.assertEqual(manifest['days_shifted'], 1)
        self.assertEqual(len(manifest['files']), 23)
        data = load(self.kit / 'samples/morning', NOW)
        previous = load(ROOT / 'samples/morning', datetime(2026, 11, 9, 9, tzinfo=JST))
        self.assertEqual([r['quantity_p'] for r in calculate(data, DAY)], [240, 240])
        for new, old in zip(data.tables['dry_stock'], previous.tables['dry_stock']):
            self.assertEqual(new['quantity_p'], old['quantity_p'])
            self.assertEqual((date.fromisoformat(new['best_before'])-DAY).days,
                             (date.fromisoformat(old['best_before'])-date(2026, 11, 9)).days)
            self.assertEqual(new['checked_at'], '2026-11-10T07:00:00+09:00')
        self.assertTrue(all(p.read_bytes() == b for p, b in original.items()))

    def test_records_are_unperformed_and_editable_without_breaking_launch(self):
        self.create()
        record = json.loads((self.kit / 'acceptance-record.json').read_text(encoding='utf-8'))
        self.assertEqual(record['overall_status'], 'not_performed')
        self.assertEqual(record['approval_status'], 'unconfirmed')
        self.assertEqual(len(record['cases']), len(CASES))
        self.assertTrue(all(c['status'] == 'not_performed' and c['actual'] is None for c in record['cases']))
        record['notes'] = '担当者が記入する試験メモ'
        (self.kit / 'acceptance-record.json').write_text(json.dumps(record), encoding='utf-8')
        home, day = prepare_trial_home(self.kit)
        self.assertEqual(day, DAY)
        self.assertEqual(home, self.kit / 'trial-data')
        self.assertFalse((home / 'history.db').exists())

    def test_existing_output_and_overflow_do_not_overwrite_or_create_kit(self):
        self.create()
        previous = (self.kit / 'kit.json').read_bytes()
        with self.assertRaises(DataError):
            self.create()
        self.assertEqual((self.kit / 'kit.json').read_bytes(), previous)
        for day in (date.min, date.max):
            path = self.root / day.isoformat()
            with self.assertRaises(DataError):
                create_kit(path, day, NOW)
            self.assertFalse(path.exists())

    def test_write_failure_removes_only_new_files(self):
        sentinel = self.root / 'unrelated.txt'
        sentinel.write_text('keep')
        original = Path.open
        def fail(path, *args, **kwargs):
            if path.name == 'GUIDE.md':
                raise OSError('simulated disk error')
            return original(path, *args, **kwargs)
        with patch.object(Path, 'open', fail), self.assertRaises(OSError):
            self.create()
        self.assertFalse(self.kit.exists())
        self.assertEqual(sentinel.read_text(), 'keep')

    def test_missing_or_tampered_or_unknown_files_block_before_db_creation(self):
        self.create()
        path = self.kit / 'samples/morning/inventory.csv'
        original = path.read_bytes()
        for content in (None, original.replace(b',0,', b',99,')):
            if content is None:
                path.unlink()
            else:
                path.write_bytes(content)
            with self.assertRaises(DataError):
                prepare_trial_home(self.kit)
            self.assertFalse((self.kit / 'trial-data').exists())
            path.write_bytes(original)
        (self.kit / 'samples/morning/orders.csv').write_text('orders')
        with self.assertRaises(DataError):
            prepare_trial_home(self.kit)
        self.assertFalse((self.kit / 'trial-data').exists())

    def test_unknown_manifest_and_existing_unmarked_home_rejected(self):
        self.create()
        manifest = self.kit / 'kit.json'
        original = manifest.read_bytes()
        for value in ('[]', '{"format":"other"}', '{"format":"bunsen-acceptance-kit","version":99}'):
            manifest.write_text(value)
            with self.assertRaises(DataError):
                prepare_trial_home(self.kit)
        manifest.write_bytes(original)
        home = self.kit / 'trial-data'
        home.mkdir()
        sentinel = home / 'history.db'
        sentinel.write_bytes(b'not a trial database')
        with self.assertRaises(DataError):
            prepare_trial_home(self.kit)
        self.assertEqual(sentinel.read_bytes(), b'not a trial database')

    def test_kit_relocation_and_repeated_start_keep_same_home(self):
        self.create()
        self.kit = self.kit.rename(self.root / '移動したキット')
        first, _ = prepare_trial_home(self.kit)
        second, _ = prepare_trial_home(self.kit)
        self.assertEqual(first, second)

    def test_marker_corruption_blocks_service_before_opening_database(self):
        self.create()
        home, _ = prepare_trial_home(self.kit)
        (home / 'trial-info.json').write_text('{}')
        with self.assertRaises(DataError):
            Service(home / 'history.db')
        self.assertFalse((home / 'history.db').exists())

    def test_trial_database_redirect_is_rejected(self):
        self.create()
        home, _ = prepare_trial_home(self.kit)
        outside = self.root / 'business.db'
        outside.write_bytes(b'business')
        # Exercise the resolved-path guard without Windows symlink privileges.
        original_resolve = Path.resolve
        def redirected(path, *args, **kwargs):
            return outside if path == home / 'history.db' else original_resolve(path, *args, **kwargs)
        with patch.object(Path, 'resolve', redirected), self.assertRaises(DataError):
            prepare_trial_home(self.kit)
        self.assertEqual(outside.read_bytes(), b'business')

    def test_trial_cli_launch_selects_isolated_home_and_day(self):
        self.create()
        from bunsen.ui import main
        with patch.object(sys, 'argv', ['bunsen', '--trial-kit', str(self.kit)]), patch('bunsen.ui.tk.Tk'), patch('bunsen.ui.App') as app:
            main()
        self.assertEqual(app.call_args.args[1], self.kit / 'trial-data')
        app.return_value.day.set.assert_called_once_with(str(DAY))
        self.assertNotIn('clock', app.call_args.kwargs)

    def test_trial_formal_gates_history_reprint_and_restore(self):
        self.create()
        home, _ = prepare_trial_home(self.kit)
        with patch.dict(os.environ, {'LOCALAPPDATA': str(self.root / 'business')}):
            service = Service(home / 'history.db', lambda: NOW)
            try:
                self.assertTrue(service.trial_only)
                before = service.import_folder(self.kit / 'samples/before_morning')
                lines = service.calculate(before, DAY)
                draft = service.issue(before, lines, DAY, '', False)
                self.assertTrue(draft['trial_only'])
                with self.assertRaises(DataError):
                    service.issue(before, lines, DAY, '試験者', True, True)
                data = service.import_folder(self.kit / 'samples/morning')
                lines = service.reconcile_draft(draft['number'], data)
                for row in lines:
                    row['lot'] = 'L001'
                with self.assertRaises(DataError):
                    service.issue(data, lines, DAY, '試験者', True, True)
                for row in lines:
                    row['quantity_p'] = 120
                final = service.issue(data, lines, DAY, '試験者', True, True, draft_number=draft['number'])
                self.assertTrue(final['trial_only'])
                self.assertIn('実出荷に使用しない', report_html(service.report_payload(final['number']), 'KOBE'))
                archive = self.root / 'backup.zip'
                manifest = create_backup(service.db, home, archive, lambda: NOW)
            finally:
                service.close()
            # Even if the filesystem marker is lost, DB event keeps trial status.
            (home / 'trial-info.json').unlink()
            service = Service(home / 'history.db', lambda: NOW)
            try:
                self.assertTrue(service.trial_only)
                self.assertEqual(service.get_issue(final['number']), final)
            finally:
                service.close()
            restored = restore_backup(archive, self.root / 'recovery', manifest['archive_sha256'], lambda: NOW)
            view = Service(restored / 'history.db', lambda: NOW)
            try:
                self.assertTrue(view.read_only and view.trial_only)
                self.assertIn('架空データの試用帳票', report_html(view.report_payload(final['number']), 'KOBE'))
            finally:
                view.close()
            self.assertFalse((self.root / 'business').exists())

    def test_real_clock_before_seven_or_wrong_day_not_bypassed(self):
        self.create()
        home, _ = prepare_trial_home(self.kit)
        service = Service(home / 'history.db', lambda: NOW)
        try:
            data = service.import_folder(self.kit / 'samples/morning')
            lines = service.calculate(data, DAY)
            for row in lines:
                row.update(quantity_p=120, lot='L001')
            for instant in (datetime(2026, 11, 10, 6, tzinfo=JST), datetime(2026, 11, 11, 9, tzinfo=JST)):
                service.clock = lambda: instant
                with self.assertRaises(DataError):
                    service.issue(data, lines, DAY, '試験者', True, True)
            self.assertEqual(service.history(), [])
        finally:
            service.close()

    def test_gui_uses_only_trial_home_and_returns_to_it(self):
        self.create()
        home, _ = prepare_trial_home(self.kit)
        business = self.root / 'business/BunsenReplenishment/history.db'
        business.parent.mkdir(parents=True)
        business.write_bytes(b'business sentinel')
        with patch.dict(os.environ, {'LOCALAPPDATA': str(self.root / 'business')}):
            root = tk.Tk(); root.withdraw()
            app = App(root, home)
            try:
                self.assertTrue(app.service.trial_only)
                self.assertIn('架空データ試用', root.title())
                self.assertEqual(app.normal_home, home)
                self.assertEqual(business.read_bytes(), b'business sentinel')
                self.assertFalse(app.service.read_only)
            finally:
                app.close()

    def test_cli_and_mutually_exclusive_launch_options(self):
        env = dict(os.environ, LOCALAPPDATA=str(self.root / 'unused'))
        command = [sys.executable, '-m', 'bunsen.tools', 'acceptance-kit', '--dispatch', str(DAY), '--output', str(self.kit)]
        result = subprocess.run(command, cwd=ROOT, env=env, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['acceptance_status'], 'not_performed')
        again = subprocess.run(command, cwd=ROOT, env=env, capture_output=True)
        self.assertNotEqual(again.returncode, 0)
        bad = subprocess.run([sys.executable, '-m', 'bunsen', '--trial-kit', str(self.kit), '--data-dir', str(self.root / 'other')], cwd=ROOT, env=env, capture_output=True)
        self.assertEqual(bad.returncode, 2)
        self.assertFalse((self.root / 'unused').exists())
        self.assertFalse((self.root / 'other').exists())
