import json,tempfile,tkinter as tk,unittest
from pathlib import Path
from datetime import date,datetime
from unittest.mock import patch
from bunsen.core import DataError,JST
from bunsen.jan_demo import create_kit
from bunsen.ui import App

class SettingsClarityTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name);self.kit=create_kit(self.path/'kit',date(2026,11,9))
        self.root=tk.Tk();self.root.withdraw();self.app=App(self.root,self.kit/'home');self.app.service.clock=lambda:datetime(2026,11,9,9,tzinfo=JST)
        self.app.day.set('2026-11-09')
    def tearDown(self):self.app.service.close();self.root.destroy();self.temp.cleanup()
    def load(self):
        with patch('bunsen.replenishment_ui.filedialog.askdirectory',return_value=str(self.kit/'csv')):self.app.open_settings_data()
    def test_empty_screen_has_no_business_defaults_and_blocks_save(self):
        self.app.open_admin(self.app.management_page)
        self.assertIn('データ未取込',self.app.setting_status.get())
        self.assertTrue(all(v.get()=='' for v in self.app.setting_values.values()))
        self.assertEqual(str(self.app.setting_save_button['state']),'disabled')
        self.assertFalse(self.app.expiry_frame.winfo_manager());self.assertFalse(self.app.settings.winfo_manager());self.assertFalse(self.app.history_list.winfo_manager())
        with self.assertRaises(DataError):self.app.confirmed_action(self.app.setting_checked,self.app.apply_settings)
    def test_read_data_then_show_named_target_and_existing_values(self):
        self.load();self.assertEqual(self.app.tabs.select(),self.app.management_page)
        self.assertIn('ムロオ神戸',self.app.setting_key.get());self.assertIn('4900000000001',self.app.setting_key.get())
        self.assertEqual(self.app.setting_values['cycle_days'].get(),'1')
        self.assertEqual(str(self.app.setting_save_button['state']),'normal')
        self.assertFalse(self.app.expiry_frame.winfo_manager())
    def test_save_changes_only_selected_warehouse_keeps_selection_and_requires_recalculation(self):
        self.load();self.app.compute();self.assertTrue(self.app.lines)
        selected=next(k for k,v in self.app.setting_targets.items() if v[0]=='SUGITO');self.app.setting_key.set(selected);self.app.select_settings()
        before={r['warehouse']:dict(r) for r in self.app.data.tables['replenishment']}
        self.app.setting_values['lead_days'].set('2');self.app.setting_reason.set('架空配送日数の変更');self.app.operator.set('架空担当')
        self.app.confirmed_action(self.app.setting_checked,self.app.apply_settings)
        after={r['warehouse']:r for r in self.app.data.tables['replenishment']}
        self.assertEqual(after['KOBE'],before['KOBE']);self.assertEqual(after['SUGITO']['lead_days'],2)
        self.assertEqual(self.app.setting_key.get(),selected);self.assertFalse(self.app.lines)
        self.assertIn('再計算',self.app.setting_status.get());self.assertFalse(self.app.setting_reason.get())
        row=self.app.service.db.execute("SELECT payload FROM events WHERE kind='settings_change'").fetchone();self.assertIsNotNone(row)
        self.assertEqual(json.loads(row[0])['after']['warehouse'],'SUGITO')
    def test_invalid_target_and_blank_are_not_inferred(self):
        self.load();self.app.setting_values['safety_days'].set('');self.app.setting_reason.set('確認');self.app.operator.set('担当')
        with self.assertRaisesRegex(DataError,'安全在庫'):self.app.confirmed_action(self.app.setting_checked,self.app.apply_settings)
        self.assertIsNone(self.app.service.db.execute("SELECT id FROM events WHERE kind='settings_change'").fetchone())
        self.app.setting_values['safety_days'].set('2');self.app.setting_key.set('未選択')
        with self.assertRaisesRegex(DataError,'倉庫・商品'):self.app.confirmed_action(self.app.setting_checked,self.app.apply_settings)
    def test_cancel_preserves_loaded_state_failed_import_disables_it(self):
        self.load();data=self.app.data
        with patch('bunsen.replenishment_ui.filedialog.askdirectory',return_value=''):self.app.open_settings_data()
        self.assertIs(self.app.data,data)
        bad=self.path/'bad';bad.mkdir()
        with patch('bunsen.replenishment_ui.filedialog.askdirectory',return_value=str(bad)):
            with self.assertRaises(DataError):self.app.open_settings_data()
        self.assertIsNone(self.app.data);self.assertEqual(str(self.app.setting_save_button['state']),'disabled')
        self.assertTrue(all(v.get()=='' for v in self.app.setting_values.values()))
    def test_read_only_disables_editing_and_saving(self):
        self.load();self.app.service.read_only=True;self.app.update_settings_state()
        self.assertEqual(str(self.app.setting_save_button['state']),'disabled')
        self.assertTrue(all(str(w['state'])=='disabled' for w in self.app.setting_entries))
