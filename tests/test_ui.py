"""Native Tk smoke tests with isolated history; no real business data."""
import os
import tempfile
import tkinter as tk
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from bunsen.core import JST, load
from bunsen.ui import App

ROOT = Path(__file__).resolve().parent.parent


class UITests(unittest.TestCase):
    def test_edit_invalidation_and_failed_import(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {'LOCALAPPDATA': folder}):
            root = tk.Tk()
            root.withdraw()
            app = App(root)
            try:
                app.data = load(ROOT / 'samples/morning', datetime(2026, 11, 9, 9, tzinfo=JST))
                app.day.set('2026-11-09')
                app.compute()
                root.update()
                self.assertEqual(len(app.tree.get_children()), 2)
                app.tree.selection_set('0')
                app.selected(None)
                app.qty.set('120')
                app.lot.set('L001')
                app.edit()
                self.assertEqual(app.lines[0]['quantity_p'], 120)
                app.ack.set(True)
                app.qty.set('144')
                self.assertFalse(app.ack.get())
                app.day.set('2026-11-10')
                self.assertIsNone(app.lines)
                self.assertEqual(len(app.tree.get_children()), 0)
                # A failed reimport must make the previously imported data unusable.
                with patch('bunsen.ui.filedialog.askdirectory', return_value=folder), patch('bunsen.ui.messagebox.showerror') as error:
                    app.run(app.import_data)
                    self.assertIsNone(app.data)
                    self.assertIsNone(app.lines)
                    error.assert_called_once()
            finally:
                app.close()

    def test_formal_issue_requires_recompute_and_old_report_is_marked(self):
        fixed_now = datetime(2026, 11, 9, 9, tzinfo=JST)
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {'LOCALAPPDATA': folder}), patch('bunsen.ui.webbrowser.open') as browser:
            root = tk.Tk()
            root.withdraw()
            app = App(root)
            app.service.clock = lambda: fixed_now
            try:
                app.data = load(ROOT / 'samples/morning', fixed_now)
                app.day.set('2026-11-09')
                app.operator.set('架空担当者')
                for revision in (1, 2):
                    app.compute()
                    self.assertEqual(app.expected_revision, revision - 1)
                    for line in app.lines:
                        line.update(quantity_p=120, lot='L001')
                    app.ack.set(True)
                    app.issue(True)
                    self.assertIsNone(app.lines)
                    self.assertIsNone(app.expected_revision)
                    self.assertFalse(app.ack.get())
                    self.assertEqual(len(app.tree.get_children()), 0)
                self.assertIn('最新改訂', app.history_list.get(0))
                self.assertIn('旧版・差替え済み', app.history_list.get(1))
                app.history_list.selection_set(1)
                app.reprint()
                report = app.home / 'reports/F-20261109-R001-KOBE.html'
                self.assertIn('旧版・差替え済み — 出荷に使用しない', report.read_text(encoding='utf-8'))
                self.assertEqual(browser.call_count, 6)
            finally:
                app.close()
