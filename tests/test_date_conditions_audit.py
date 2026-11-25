"""Fixed-date regressions supporting the operational date-condition manual."""
import csv,io,tempfile,tkinter as tk,unittest
from pathlib import Path
from unittest.mock import patch
from datetime import date,datetime,timedelta
from bunsen.core import DataError,JST,calculate
from bunsen.jan_demo import create_kit
from bunsen.requirements_data import SCHEMA,csv_text
from bunsen.service import Service
from bunsen.ui import App
from bunsen.shipping_output import rows

DAY=date(2026,11,25);STOCK=DAY-timedelta(days=1);NOW=datetime(2026,11,25,12,tzinfo=JST)
SETTINGS={w:dict(cycle_days=1,lead_days=lead,safety_days=2,coverage_days=3) for w,lead in [('KOBE',1),('SUGITO',2)]}

class DateConditionsAuditTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name);self.kit=create_kit(self.path/'kit',STOCK);self.csv=self.kit/'csv'
        self.s=Service(self.kit/'home/history.db',lambda:NOW)
        self.change('inventory',lambda rr:[dict(r,as_of='2026-11-24T18:00:00+09:00',basis='closing') for r in rr])
        self.change('requirements',lambda rr:[dict(r,**SETTINGS[r['warehouse']]) for r in rr])
    def tearDown(self):self.s.close();self.temp.cleanup()
    def change(self,table,fn):
        p=self.csv/(table+'.csv');rr=list(csv.DictReader(io.StringIO(p.read_text(encoding='utf-8-sig'))))
        p.write_text(csv_text(SCHEMA[table],fn(rr)),encoding='utf-8-sig')
    def data(self):return self.s.import_folder(self.csv,workflow=2)
    def test_required_forecast_range_matches_manual_example(self):
        self.change('forecast',lambda rr:[r for r in rr if '2026-11-25'<=r['date']<='2026-12-01'])
        result=calculate(self.data(),DAY)
        self.assertEqual([(r['warehouse'],r['arrival'],r['end']) for r in result],[('KOBE','2026-11-26','2026-11-28'),('SUGITO','2026-11-27','2026-11-29')])
        self.assertTrue(all(r['timeline'][0]['date']=='2026-11-25' for r in result))
        self.change('forecast',lambda rr:[r for r in rr if r['date']!='2026-12-01'])
        with self.assertRaises(DataError) as exc:calculate(self.data(),DAY)
        self.assertTrue(any(i.warehouse=='SUGITO' and i.code=='FORECAST_MISSING' and i.date=='2026-12-01' for i in exc.exception.issues))
    def test_stock_and_forecast_generation_dates_need_not_be_equal(self):
        self.change('forecast',lambda rr:[dict(r,as_of='2026-11-25T08:00:00+09:00') for r in rr])
        self.assertEqual(len(calculate(self.data(),DAY)),2)
        with self.assertRaises(DataError) as exc:calculate(self.data(),STOCK)
        self.assertIn('FORECAST_AFTER_DISPATCH',{i.code for i in exc.exception.issues})
    def test_same_day_closing_stock_with_zero_lead_has_no_supported_demand_start(self):
        self.change('requirements',lambda rr:[dict(r,lead_days=0) for r in rr])
        with self.assertRaises(DataError) as exc:calculate(self.data(),STOCK)
        self.assertIn('INVENTORY_AFTER_DISPATCH',{i.code for i in exc.exception.issues})
    def test_explicit_zero_forecast_is_valid_but_missing_date_stops(self):
        self.change('forecast',lambda rr:[dict(r,quantity_p=0) for r in rr])
        self.assertEqual([r['quantity_p'] for r in calculate(self.data(),DAY)],[0,0])
        self.change('forecast',lambda rr:[r for r in rr if r['date']!='2026-11-25'])
        with self.assertRaises(DataError) as exc:calculate(self.data(),DAY)
        self.assertIn('FORECAST_MISSING',{i.code for i in exc.exception.issues})
    def test_past_day_calculation_and_formal_issue_are_different_checks(self):
        data=self.data();past=DAY-timedelta(days=1);rr=calculate(data,past)
        with self.assertRaisesRegex(DataError,'過去日'):self.s.issue(data,rr,past,'架空担当',True,True)
        future=DAY+timedelta(days=1);rr=calculate(data,future)
        self.assertTrue(self.s.issue(data,rr,future,'架空担当',True,True)['formal'])
    def test_current_output_includes_zero_quantity_and_has_no_shipping_only_switch(self):
        self.change('inventory',lambda rr:[dict(r,quantity_p=99999 if r['warehouse']=='KOBE' else 0) for r in rr])
        data=self.data();rr=calculate(data,DAY);payload=self.s.issue(data,rr,DAY,'架空担当',True,True)
        self.assertEqual(len(rows(payload,'KOBE')),1);self.assertEqual(rows(payload,'KOBE')[0][9],0)
        self.assertGreater(rows(payload,'SUGITO')[0][9],0)
    def test_gui_date_change_requires_recalculation_then_uses_new_date(self):
        self.s.save_common_settings(SETTINGS,'架空担当','日付条件監査用',True,0)
        root=tk.Tk();root.withdraw();app=App(root,self.kit/'home');app.service.clock=lambda:NOW
        try:
            app.day.set(str(DAY));app.data=app.service.import_folder(self.csv,workflow=2,use_common_settings=True);app.compute();before=app.lines
            app.ack.set(True);app.day.set('2026-11-26')
            self.assertIsNone(app.lines);self.assertFalse(app.ack.get())
            with self.assertRaises(DataError):app.confirm_and_export()
            for w in ('KOBE','SUGITO'):(self.csv/('F-20261125-R001-'+w+'.pdf')).write_bytes(b'%PDF-fictional-shipping-output')
            with patch('bunsen.replenishment_ui.filedialog.askdirectory',return_value=str(self.csv)):app.open_and_compute()
            self.assertEqual(app.dispatch,date(2026,11,26));self.assertNotEqual(app.lines,before)
            self.assertEqual(app.lines[0]['arrival'],'2026-11-27')
        finally:app.close()
