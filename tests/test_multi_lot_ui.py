import json
import os
import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import patch

from bunsen.core import DataError, load
from bunsen.ui import App
from test_workflow import NOW

ROOT = Path(__file__).resolve().parent.parent


class MultiLotUITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.environment = patch.dict(os.environ, {'LOCALAPPDATA': self.temp.name})
        self.environment.start()
        self.root = tk.Tk()
        self.root.withdraw()
        self.app = App(self.root)
        self.app.service.clock = lambda: NOW
        self.app.data = load(ROOT / 'samples/multi_lot', NOW)
        self.app.day.set('2026-11-09')
        self.app.compute()

    def tearDown(self):
        self.app.close()
        self.environment.stop()
        self.temp.cleanup()

    def open(self, index):
        self.app.tree.selection_set(str(index))
        self.app.selected(None)
        self.app.open_allocations()
        return self.app.lot_dialog

    @staticmethod
    def add(dialog, lot, quantity):
        dialog.lot.set(lot)
        dialog.quantity.set(str(quantity))
        dialog.add()

    def test_manual_split_stock_visibility_and_formal_issue(self):
        first = self.open(0)
        self.assertEqual(first.lot.get(), '')
        self.assertEqual(first.assigned, [])
        first.total.set('120')
        self.add(first, 'L001', 72)
        self.add(first, 'L002', 48)
        first.quantity.set('24')
        with self.assertRaisesRegex(DataError, '追加・更新'):
            first.save()
        first.quantity.set('')
        first.save()
        self.assertFalse(self.app.ack.get())
        self.assertEqual(self.app.lines[0]['quantity_p'], 120)
        self.assertEqual(len(self.app.lines[0]['allocations']), 2)
        first_event = json.loads(self.app.service.db.execute("SELECT payload FROM events WHERE kind='edit' ORDER BY id DESC").fetchone()[0])
        self.assertNotIn('allocations', first_event['before'])
        self.assertEqual(first_event['after']['allocations'][0]['quantity_p'], 72)
        second = self.open(1)
        stock_values = second.stock_tree.item('0', 'values')
        self.assertEqual(str(stock_values[2]), '72')
        self.assertEqual(str(stock_values[3]), '72')
        second.total.set('120')
        self.add(second, 'L001', 72)
        self.add(second, 'L002', 48)
        second.save()
        self.app.operator.set('架空担当者')
        self.app.ack.set(True)
        with patch('bunsen.ui.webbrowser.open') as browser:
            self.app.issue(True)
            self.assertEqual(browser.call_count, 2)
        self.assertIsNone(self.app.lines)
        issued = self.app.service.get_issue('F-20261109-R001')
        self.assertEqual(issued['lines'][1]['allocations'][1]['lot'], 'L002')
        text = (self.app.home / 'reports/F-20261109-R001-KOBE.html').read_text(encoding='utf-8')
        self.assertIn('<td>72</td>', text)
        self.assertIn('<td>48</td>', text)

    def test_add_update_delete_and_cancel_do_not_change_saved_line(self):
        self.app.ack.set(True)
        dialog = self.open(0)
        self.assertFalse(self.app.ack.get())
        self.add(dialog, 'L001', 72)
        self.add(dialog, 'L001', 96)
        self.assertEqual(len(dialog.assigned), 1)
        self.assertEqual(dialog.assigned[0]['quantity_p'], 96)
        dialog.assignment_tree.selection_set('0')
        dialog.remove()
        self.assertEqual(dialog.assigned, [])
        self.add(dialog, 'L002', 48)
        dialog.destroy()
        self.assertNotIn('allocations', self.app.lines[0])
        self.assertEqual(self.app.lines[0]['quantity_p'], 240)

    def test_incomplete_split_is_visible_and_cannot_issue_formally(self):
        dialog = self.open(0)
        dialog.total.set('120')
        self.add(dialog, 'L001', 72)
        self.assertIn('合計不一致', dialog.result.get('1.0', 'end'))
        dialog.save()
        self.assertIn('合計不一致', self.app.detail.get('1.0', 'end'))
        self.app.operator.set('架空担当者')
        self.app.ack.set(True)
        with self.assertRaisesRegex(DataError, '合計不一致'):
            self.app.issue(True)
        self.assertEqual(self.app.service.history(), [])
        self.app.tree.selection_set('0')
        self.app.selected(None)
        with self.assertRaisesRegex(DataError, '複数ロット指定済み'):
            self.app.edit()
