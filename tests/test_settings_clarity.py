import json,tempfile,tkinter as tk,unittest
from pathlib import Path
from datetime import date,datetime
from unittest.mock import patch
from bunsen.core import DataError,JST
from bunsen.jan_demo import create_kit
from bunsen.ui import App
from bunsen.backup import validate_database,create_backup,restore_backup
from bunsen.service import Service

VALUES=dict(cycle_days=1,lead_days=0,safety_days=1,coverage_days=2)
class CommonSettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name);self.kit=create_kit(self.path/'kit',date(2026,11,9))
        self.root=tk.Tk();self.root.withdraw();self.app=App(self.root,self.kit/'home');self.app.service.clock=lambda:datetime(2026,11,9,9,tzinfo=JST)
        self.app.day.set('2026-11-09')
    def tearDown(self):self.app.service.close();self.root.destroy();self.temp.cleanup()
    def save(self,values=None):
        for k,v in (values or VALUES).items():self.app.setting_values[k].set(str(v))
        self.app.operator.set('架空担当');self.app.setting_reason.set('架空の共通条件')
        self.app.confirmed_action(self.app.setting_checked,self.app.apply_settings)
    def load(self):
        with patch('bunsen.replenishment_ui.filedialog.askdirectory',return_value=str(self.kit/'csv')):self.app.open_and_compute()
    def test_first_screen_needs_no_csv_or_product_selection(self):
        self.assertFalse(hasattr(self.app,'setting_picker'));self.assertFalse(hasattr(self.app,'setting_key'))
        self.assertTrue(all(v.get()=='' for v in self.app.setting_values.values()));self.assertIsNone(self.app.data)
        self.assertEqual(str(self.app.setting_save_button['state']),'normal')
        self.save();self.assertEqual(self.app.service.common_settings()['values'],VALUES)
    def test_no_requirements_csv_is_needed_and_both_warehouses_use_common_values(self):
        self.save();(self.kit/'csv/requirements.csv').unlink();self.load()
        self.assertEqual(len(self.app.lines),2)
        for r in self.app.data.tables['requirements']:
            self.assertEqual({k:r[k] for k in VALUES},VALUES)
        self.app.ack.set(True);self.app.issue(True)
        payload=self.app.service.get_issue(self.app.last_number)
        self.assertIn('common-settings.csv',payload['raw'])
        validate_database(self.app.service.db)
    def test_existing_product_specific_csv_is_preserved_and_not_used_by_new_flow(self):
        p=self.kit/'csv/requirements.csv';before=p.read_bytes();self.save();self.load()
        self.assertEqual(p.read_bytes(),before)
        self.assertTrue(all(r['lead_days']==0 for r in self.app.data.tables['requirements']))
    def test_unset_or_invalid_conditions_do_not_create_defaults(self):
        with self.assertRaises(DataError):self.load()
        self.app.setting_values['cycle_days'].set('0')
        with self.assertRaises(DataError):self.app.confirmed_action(self.app.setting_checked,self.app.apply_settings)
        self.assertIsNone(self.app.service.common_settings()['values'])
        for values in [dict(VALUES,cycle_days=0),dict(VALUES,safety_days=-1),dict(VALUES,lead_days=True)]:
            with self.assertRaises(DataError):self.app.service.save_common_settings(values,'担当','確認',True,0)
    def test_change_stops_old_calculation_and_formal_plan_confirmation(self):
        self.save();self.load();data=self.app.data;lines=self.app.lines
        plan=self.app.service.calculate_delivery_plan(data,date(2026,11,9))
        self.save(dict(VALUES,lead_days=1));self.assertIsNone(self.app.data);self.assertFalse(self.app.lines)
        with self.assertRaises(DataError):self.app.service.issue(data,lines,date(2026,11,9),'担当',True,True)
        with self.assertRaises(DataError):self.app.service.confirm_delivery_plan(plan['plan_id'],'担当',True)
    def test_snapshot_rejects_tampering_but_keeps_old_history(self):
        self.save();self.load();data=self.app.data
        raw=dict(data.raw);raw['requirements.csv']=raw['requirements.csv'].replace(',0,',',1,',1)
        with self.assertRaises(DataError):self.app.service.snapshot_data(raw)
        self.save(dict(VALUES,lead_days=1))
        restored=self.app.service.snapshot_data(data.raw,data.digest)
        self.assertTrue(all(r['lead_days']==0 for r in restored.tables['requirements']))
    def test_persistence_backup_and_read_only(self):
        self.save();self.load();self.app.ack.set(True);self.app.issue(True)
        db=self.app.service.db;home=self.app.home;manifest=create_backup(db,home,self.path/'backup.zip',self.app.service.clock)
        restored=restore_backup(self.path/'backup.zip',self.path/'restore',manifest['archive_sha256'],self.app.service.clock)
        service=Service(restored/'history.db',self.app.service.clock)
        try:
            self.assertEqual(service.common_settings()['values'],VALUES)
            with self.assertRaises(DataError):service.save_common_settings(VALUES,'担当','確認',True,service.common_settings()['revision'])
        finally:service.close()
        self.app.service.read_only=True;self.app.update_settings_state();self.assertEqual(str(self.app.setting_save_button['state']),'disabled')
    def test_revision_conflict_and_audit_corruption_are_detected(self):
        self.save()
        with self.assertRaises(DataError):self.app.service.save_common_settings(VALUES,'担当','確認',True,0)
        row=self.app.service.db.execute("SELECT id,payload FROM events WHERE kind='common_settings_change'").fetchone();payload=json.loads(row[1]);payload['before']={}
        self.app.service.db.execute('UPDATE events SET payload=? WHERE id=?',(json.dumps(payload),row[0]));self.app.service.db.commit()
        with self.assertRaises(DataError):validate_database(self.app.service.db)
