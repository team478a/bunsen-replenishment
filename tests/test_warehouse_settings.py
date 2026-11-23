import copy,json,tempfile,tkinter as tk,unittest
from datetime import date,datetime
from pathlib import Path
from unittest.mock import patch
from bunsen.core import DataError,JST
from bunsen.jan_demo import create_kit
from bunsen.ui import App
from bunsen.common_settings import proof,parse,validate,requirements
from bunsen.backup import validate_database
from bunsen.warehouse_demo import VALUES

FLAT=dict(cycle_days=1,lead_days=1,safety_days=1,coverage_days=2)
class WarehouseSettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name);self.kit=create_kit(self.path/'kit',date(2026,11,9))
        self.root=tk.Tk();self.root.withdraw();self.app=App(self.root,self.kit/'home')
        self.app.service.clock=lambda:datetime(2026,11,9,9,tzinfo=JST)
    def tearDown(self):self.app.close();self.temp.cleanup()
    def save(self,values=VALUES):
        for w,conditions in values.items():
            for k,v in conditions.items():self.app.setting_values[(w,k)].set(str(v))
        self.app.operator.set('架空担当');self.app.setting_reason.set('架空の倉庫別確認')
        self.app.confirmed_action(self.app.setting_checked,self.app.apply_settings)
    def load(self):return self.app.service.import_folder(self.kit/'csv',workflow=2,use_common_settings=True,require_warehouse_settings=True)
    def test_different_leads_change_only_respective_warehouse(self):
        self.save();data=self.load();rows=self.app.service.calculate(data,date(2026,11,9))
        self.assertEqual([r['arrival'] for r in rows],['2026-11-09','2026-11-11'])
        self.assertEqual([r['required_p'] for r in rows],[60,102])
        values=copy.deepcopy(VALUES);values['SUGITO']['lead_days']=3;self.save(values)
        new=self.app.service.calculate(self.load(),date(2026,11,9))
        self.assertEqual(rows[0],new[0]);self.assertEqual(new[1]['arrival'],'2026-11-12');self.assertEqual(new[1]['required_p'],132)
    def test_missing_warehouse_or_field_never_saves_or_defaults(self):
        for value in [dict(KOBE=FLAT),dict(VALUES,SUGITO={}),dict(VALUES,SUGITO=dict(FLAT,lead_days=None)),dict(VALUES,SUGITO=dict(FLAT,lead_days=-1))]:
            with self.assertRaises(DataError):self.app.service.save_common_settings(value,'担当','確認',True,0)
        self.app.setting_values[('KOBE','lead_days')].set('1')
        with self.assertRaises(DataError):self.app.confirmed_action(self.app.setting_checked,self.app.apply_settings)
        self.assertIsNone(self.app.service.common_settings()['values'])
    def test_old_flat_conditions_and_issued_history_survive_without_automatic_reuse(self):
        self.app.service.save_common_settings(FLAT,'架空担当','旧共通条件',True,0)
        old=self.app.service.import_folder(self.kit/'csv',workflow=2,use_common_settings=True)
        final=self.app.service.issue(old,self.app.service.calculate(old,date(2026,11,9)),date(2026,11,9),'架空担当',True,True)
        old_proof=old.raw['common-settings.csv'];self.app.display_settings()
        self.assertTrue(all(v.get()=='' for v in self.app.setting_values.values()))
        self.assertIn('旧共通条件',self.app.setting_status.get())
        with self.assertRaises(DataError):self.load()
        self.save();self.assertEqual(self.app.service.get_issue(final['number']),final)
        self.assertEqual(self.app.service.snapshot_data(old.raw,old.digest).raw['common-settings.csv'],old_proof)
        self.assertEqual(parse(old_proof)['values'],FLAT);validate_database(self.app.service.db)
    def test_proof_detects_duplicate_warehouse_missing_row_and_mismatched_requirements(self):
        self.save();data=self.load();text=data.raw['common-settings.csv'];self.assertEqual(proof(parse(text)),text)
        for bad in [text.replace('SUGITO,','KOBE,'),'\n'.join(text.splitlines()[:-1])+'\n',text.replace('SUGITO,','UNKNOWN,')]:
            with self.assertRaises(DataError):parse(bad)
        raw=dict(data.raw);raw['requirements.csv']=raw['requirements.csv'].replace('SUGITO,4900000000001,1,2,','SUGITO,4900000000001,1,0,')
        with self.assertRaises(DataError):self.app.service.snapshot_data(raw)
    def test_ui_identifies_warehouses_and_only_displays_confirmed_source_numbers(self):
        self.assertIn('KOBE',self.app.setting_warehouses['KOBE'].get());self.assertIn('ムロオ杉戸',self.app.setting_warehouses['SUGITO'].get())
        self.assertIn('未登録',self.app.setting_warehouses['SUGITO'].get())
        with patch.object(self.app.service,'forecast_mapping',return_value=dict(revision=1,mapping={'架空01':'KOBE','架空02':'SUGITO'})):self.app.display_settings()
        self.assertIn('架空01',self.app.setting_warehouses['KOBE'].get());self.assertNotIn('架空01',self.app.setting_warehouses['SUGITO'].get())
    def test_per_warehouse_cycle_safety_coverage_and_immutable_revision(self):
        values=copy.deepcopy(VALUES);values['SUGITO'].update(cycle_days=3,safety_days=2,coverage_days=4);self.save(values)
        data=self.load();settings={r['warehouse']:r for r in data.tables['requirements']}
        for w in values:self.assertEqual({k:settings[w][k] for k in values[w]},values[w])
        rows=self.app.service.calculate(data,date(2026,11,9));plan=self.app.service.calculate_delivery_plan(data,date(2026,11,9))
        self.save()
        with self.assertRaises(DataError):self.app.service.issue(data,rows,date(2026,11,9),'担当',True,True)
        with self.assertRaises(DataError):self.app.service.confirm_delivery_plan(plan['plan_id'],'担当',True)
