import tempfile
from pathlib import Path
import tkinter as tk
import unittest
from unittest.mock import patch
from datetime import date, datetime, time
from bunsen.core import DataError, JST
from bunsen.jan_demo import create_kit
from bunsen.ui import App

DAY=date(2026,11,9)
class SimpleUITests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)
        create_kit(self.path/'kit',DAY)
        self.root=tk.Tk();self.root.withdraw();self.app=App(self.root,self.path/'kit/home')
        self.app.service.clock=lambda:datetime.combine(DAY,time(9),JST)
        self.app.day.set(str(DAY))
        self.app.service.save_common_settings(dict(cycle_days=1,lead_days=0,safety_days=1,coverage_days=2),'架空担当','架空共通条件',True,0)
    def tearDown(self):
        self.app.service.close();self.root.destroy();self.temp.cleanup()
    def load(self):
        with patch('bunsen.replenishment_ui.filedialog.askdirectory',return_value=str(self.path/'kit/csv')):self.app.open_and_compute()
        self.app.operator.set('試験担当')
    def test_navigation_and_auto_calculation(self):
        self.assertEqual([self.app.tabs.tab(p,'text') for p in self.app.tabs.tabs() if self.app.tabs.tab(p,'state')!='hidden'],['補充出荷','履歴'])
        self.load();self.assertEqual(len(self.app.lines),2)
        self.assertFalse(self.app.jan_master_mode.winfo_manager());self.assertFalse(self.app.editor.winfo_manager())
        self.assertEqual(self.app.tree['displaycolumns'],('warehouse','product','inventory','required','units','arrival'))
    def test_cancel_does_not_issue(self):
        self.load()
        with patch('bunsen.simple_ui.filedialog.askdirectory',return_value=''):self.app.confirm_and_export()
        self.assertEqual(self.app.service.history(),[]);self.assertFalse(self.app.ack.get())
    def test_registered_master_fallback_requires_both_csvs_absent(self):
        master=dict(product='4900000000001',name='架空JAN',expiry_type='best_before',shelf_life_days=10,kobe_p_per_pl=240,sugito_p_per_pl=480)
        self.app.service.save_jan_master(master,'担当','架空確認',True,0,create=True)
        (self.path/'kit/csv/products.csv').unlink()
        with patch('bunsen.replenishment_ui.filedialog.askdirectory',return_value=str(self.path/'kit/csv')):
            with self.assertRaises(DataError):self.app.open_and_compute()
        self.assertFalse(self.app.use_registered_jan.get());self.assertIsNone(self.app.data)
        (self.path/'kit/csv/pallets.csv').unlink()
        self.load();self.assertTrue(self.app.use_registered_jan.get())
        self.assertEqual(self.app.data.jan_master_revision,self.app.service.jan_master()["revision"])
    def test_explicit_click_outputs_and_retry_reuses_number(self):
        self.load()
        with patch('bunsen.simple_ui.filedialog.askdirectory',return_value=str(self.path/'reports')),patch('bunsen.simple_ui.write_documents',side_effect=OSError('disk')):
            with self.assertRaises(OSError):self.app.confirm_and_export()
        number=self.app.last_number
        self.assertTrue(self.app.finalized);self.assertFalse(self.app.ack.get())
        with patch('bunsen.simple_ui.filedialog.askdirectory',return_value=str(self.path/'reports')):self.app.confirm_and_export()
        self.assertEqual(len(self.app.service.history()),1);self.assertEqual(self.app.last_number,number)
        self.assertEqual(len(list((self.path/'reports').glob('*.pdf'))),2)
    def test_pl_edit_and_unapplied_changes(self):
        self.load();self.app.tree.selection_set('0');self.app.selected()
        self.app.edit_amount.set('2');self.app.reason.set('試験変更')
        with self.assertRaises(DataError):self.app.ensure_no_pending_edit()
        self.app.edit_display_quantity();self.assertEqual(self.app.lines[0]['quantity_p'],480)
    def test_api_still_requires_acknowledgement(self):
        self.load()
        with self.assertRaises(DataError):self.app.issue(True)
    def test_save_confirmation_is_not_retained_after_failure(self):
        def fail():
            self.assertTrue(self.app.jan_ack.get());raise ValueError('invalid')
        with self.assertRaises(ValueError):self.app.confirmed_action(self.app.jan_ack,fail)
        self.assertFalse(self.app.jan_ack.get())
    def test_missing_data_blocks_without_save_dialog(self):
        with patch('bunsen.simple_ui.filedialog.askdirectory') as dialog:
            with self.assertRaises(DataError):self.app.confirm_and_export()
            dialog.assert_not_called()
