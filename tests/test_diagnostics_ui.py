import json
import os
from pathlib import Path
import tempfile
import tkinter as tk
import unittest
from unittest.mock import patch

from bunsen.backup import file_digest
from bunsen.core import DataError, load
from bunsen.ui import App
from test_workflow import NOW, ROOT


class DiagnosticUITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        self.env = patch.dict(os.environ, {'LOCALAPPDATA': self.temp.name})
        self.env.start()
        self.root = tk.Tk()
        self.root.withdraw()
        self.app = App(self.root)
        self.app.service.clock = lambda: NOW
        self.app.diagnostic_day.set('2026-11-09')

    def tearDown(self):
        self.app.close()
        self.env.stop()
        self.temp.cleanup()

    def test_read_only_diagnosis_does_not_replace_current_data_or_write_history(self):
        self.app.data = load(ROOT / 'samples/morning', NOW)
        self.app.day.set('2026-11-09')
        self.app.compute()
        data, lines = self.app.data, self.app.lines
        before = file_digest(self.app.service.db_path)
        self.app.ack.set(True)
        self.app.diagnostic_folder.set(str(ROOT / 'samples/before_morning'))
        self.app.run_diagnostic()
        self.assertFalse(self.app.ack.get())
        self.assertIs(self.app.data, data)
        self.assertIs(self.app.lines, lines)
        self.assertEqual(file_digest(self.app.service.db_path), before)
        self.assertTrue(self.app.diagnostic_tree.get_children())
        self.assertIn('正式発行可の判定ではありません', self.app.diagnostic_summary.get())
        output = self.folder / 'reports/diagnostic.json'
        with patch('bunsen.ui.filedialog.asksaveasfilename', return_value=str(output)), patch('bunsen.ui.webbrowser.open') as browser:
            self.app.save_diagnostic()
            browser.assert_called_once()
        result = json.loads(output.read_text(encoding='utf-8'))
        self.assertFalse(result['formal_issue_allowed'])
        self.assertEqual(file_digest(self.app.service.db_path), before)

    def test_changed_diagnostic_date_or_folder_requires_rerun(self):
        self.app.diagnostic_folder.set(str(ROOT / 'samples/morning'))
        self.app.run_diagnostic()
        self.assertIsNotNone(self.app.diagnostic_report)
        self.app.diagnostic_day.set('bad-date')
        self.assertIsNone(self.app.diagnostic_report)
        self.assertEqual(str(self.app.diagnostic_save.cget('state')), 'disabled')
        with patch('bunsen.ui.messagebox.showerror'):
            self.app.run(self.app.run_diagnostic)
        self.assertIsNone(self.app.diagnostic_report)
        with self.assertRaises(DataError):
            self.app.save_diagnostic()

    def test_mapping_template_and_input_folder_protection(self):
        self.app.diagnostic_folder.set(str(ROOT / 'samples/morning'))
        path = self.folder / 'mapping.json'
        before = file_digest(self.app.service.db_path)
        with patch('bunsen.ui.filedialog.asksaveasfilename', return_value=str(path)):
            self.app.save_mapping_template()
        template = json.loads(path.read_text(encoding='utf-8'))
        self.assertEqual(template['status'], 'unconfirmed')
        self.assertTrue(all(field['source_field'] is None for table in template['tables'] for field in table['fields']))
        self.assertEqual(file_digest(self.app.service.db_path), before)
        self.app.run_diagnostic()
        with patch('bunsen.ui.filedialog.asksaveasfilename', return_value=str(ROOT / 'samples/morning/diagnostic.json')):
            with self.assertRaisesRegex(DataError, '入力フォルダー'):
                self.app.save_diagnostic()
        self.assertFalse((ROOT / 'samples/morning/diagnostic.json').exists())
