import os
import tempfile
import tkinter as tk
import unittest
from unittest.mock import patch

from bunsen.core import DataError, calculate
from bunsen.ui import App
from test_workflow import DAY, NOW, ROOT


class DraftResumeUITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.environment = patch.dict(os.environ, {'LOCALAPPDATA': self.temp.name})
        self.environment.start()
        self.start_app()
        data = self.app.service.import_folder(ROOT / 'samples/before_morning')
        lines = calculate(data, DAY)
        for row in lines:
            row.update(quantity_p=240, lot='L001')
        self.draft = self.app.service.issue(data, lines, DAY, '前日担当者', False)

    def start_app(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.app = App(self.root)
        self.app.service.clock = lambda: NOW

    def tearDown(self):
        self.app.close()
        self.environment.stop()
        self.temp.cleanup()

    def resume(self):
        self.app.history()
        index = next(i for i, row in enumerate(self.app.records) if row[0] == self.draft['number'])
        self.app.history_list.selection_set(index)
        self.app.resume_draft()

    def morning(self):
        with patch('bunsen.ui.filedialog.askdirectory', return_value=str(ROOT / 'samples/morning')):
            self.app.import_data()
        self.app.compute()

    def edit(self, index, qty):
        self.app.tree.selection_set(str(index))
        self.app.selected(None)
        self.app.qty.set(str(qty))
        self.app.lot.set('L001')
        self.app.edit()

    def test_restart_resume_morning_shortage_edit_and_final(self):
        self.app.close()
        self.start_app()
        self.app.operator.set('以前の入力')
        self.app.ack.set(True)
        self.resume()
        self.assertEqual(self.app.operator.get(), '')
        self.assertFalse(self.app.ack.get())
        self.assertEqual(self.app.draft_number, self.draft['number'])
        self.assertIn('保存原本から復元', self.app.info.get())
        self.morning()
        self.assertEqual(self.app.lines[0]['quantity_p'], 240)
        self.assertIn('再開元仮指示', self.app.detail.get('1.0', 'end'))
        self.app.operator.set('朝担当者')
        self.app.ack.set(True)
        with self.assertRaisesRegex(DataError, '二重割当'):
            self.app.issue(True)
        self.edit(0, 120)
        self.edit(1, 120)
        self.app.ack.set(True)
        with patch('bunsen.ui.webbrowser.open'):
            self.app.issue(True)
        self.assertIsNone(self.app.draft_number)
        final = self.app.service.get_issue('F-20261109-R001')
        self.assertEqual(final['source_draft']['number'], self.draft['number'])
        self.assertEqual(final['source_draft']['rows'][0]['current_quantity_p'], 120)

    def test_failed_import_blocks_old_data_and_preserves_working_instructions(self):
        self.resume()
        self.edit(0, 144)
        with patch('bunsen.ui.filedialog.askdirectory', return_value=self.temp.name), patch('bunsen.ui.messagebox.showerror'):
            self.app.run(self.app.import_data)
        self.assertIsNone(self.app.data)
        self.assertIsNone(self.app.lines)
        self.assertEqual(self.app.draft_working[0]['quantity_p'], 144)
        with self.assertRaisesRegex(DataError, 'データ取込'):
            self.app.compute()
        self.morning()
        self.assertEqual(self.app.lines[0]['quantity_p'], 144)

    def test_reimport_and_recompute_preserve_edits_and_require_applied_input(self):
        self.resume()
        self.morning()
        self.edit(0, 120)
        self.app.ack.set(True)
        self.app.compute()
        self.assertFalse(self.app.ack.get())
        self.assertEqual(self.app.lines[0]['quantity_p'], 120)
        self.morning()
        self.assertEqual(self.app.lines[0]['quantity_p'], 120)
        self.app.tree.selection_set('0')
        self.app.selected(None)
        self.app.qty.set('144')
        with self.assertRaisesRegex(DataError, '反映してください'):
            self.app.compute()
        with patch('bunsen.ui.filedialog.askdirectory') as picker:
            with self.assertRaisesRegex(DataError, '反映してください'):
                self.app.import_data()
            picker.assert_not_called()
        self.assertEqual(self.app.lines[0]['quantity_p'], 120)

    def test_new_provisional_revision_becomes_active_reference(self):
        self.resume()
        self.edit(0, 144)
        with patch('bunsen.ui.webbrowser.open'):
            self.app.issue(False)
        self.assertEqual(self.app.draft_number, 'P-20261109-R002')
        self.app.compute()
        self.assertEqual(self.app.lines[0]['quantity_p'], 144)
        self.app.day.set('2026-11-10')
        self.assertIsNone(self.app.draft_number)
        self.assertIsNone(self.app.lines)
        self.assertEqual(self.app.resume_info.get(), '新規計算')
