import copy
import csv
from datetime import date, datetime, timedelta, timezone
import io
import json
from pathlib import Path
import tempfile
import tkinter as tk
import unittest
from unittest.mock import patch

from bunsen.core import DataError, load
from bunsen.delivery import build_plan, pack_trips
from bunsen.delivery_output import export_plan
from bunsen.service import Service
from bunsen.backup import create_backup, restore_backup
from bunsen.ui import App
from test_workflow import ROOT, DAY, NOW


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.csv=self.root/'csv';self.csv.mkdir()
        for p in (ROOT/'samples/delivery-plan').glob('*.csv'):(self.csv/p.name).write_bytes(p.read_bytes())
        self.service=Service(self.root/'home/history.db',lambda:NOW)

    def tearDown(self):
        self.service.close();self.temp.cleanup()

    def data(self):return load(self.csv,NOW)

    def change(self,table,fn):
        path=self.csv/(table+'.csv');reader=csv.DictReader(io.StringIO(path.read_text(encoding='utf-8')));rows=fn(list(reader))
        with path.open('w',encoding='utf-8',newline='') as f:
            writer=csv.DictWriter(f,fieldnames=reader.fieldnames,lineterminator='\n');writer.writeheader();writer.writerows(rows)

    def plan(self):return self.service.calculate_delivery_plan(self.data(),DAY)

    def test_fixed_calculation_date_not_each_future_dispatch(self):
        report=self.plan();self.assertEqual(report['assumed_manufacture_date'],str(DAY))
        self.assertEqual({r['estimated_expiry'] for r in report['shipments']},{str(DAY+timedelta(days=10))})
        self.assertTrue(any(r['remaining_arrival_days']<0 for r in report['shipments']))
        for r in report['shipments']:
            self.assertEqual(r['remaining_arrival_days'],(DAY+timedelta(days=10)-date.fromisoformat(r['arrival'])).days)

    def test_no_bunsen_or_manufacturing_data_required(self):
        (self.csv/'dry_stock.csv').write_bytes(b'bad\xff')
        report=self.plan();self.assertTrue(report['trips']);self.assertNotIn('dry_stock.csv',self.data().raw)
        self.assertFalse(any(k in report for k in ('production_quantity','lots','expired_stock_p','waste_p')))

    def test_old_master_replenishment_still_works_but_plan_lists_missing_fields(self):
        (self.csv/'products.csv').write_bytes((ROOT/'samples/replenishment/products.csv').read_bytes())
        data=self.data()
        with self.assertRaises(DataError) as caught:build_plan(data,DAY,NOW)
        self.assertEqual({i.field for i in caught.exception.issues},{'expiry_type','shelf_life_days'})
        self.assertTrue(all(i.product=='P001' for i in caught.exception.issues))

    def test_invalid_master_zero_negative_unknown_type_not_guessed(self):
        for value in ('0','-1','unknown',''):
            with self.subTest(value=value):
                self.change('products',lambda rs:[dict(r,shelf_life_days=value) for r in rs])
                with self.assertRaises(DataError):self.data()

    def test_expiry_type_is_preserved(self):
        self.change('products',lambda rs:[dict(r,expiry_type='use_by') for r in rs])
        self.assertEqual({r['expiry_type'] for r in self.plan()['shipments']},{'use_by'})

    def test_plan_rounding_uses_product_master_and_cycle(self):
        report=self.plan()
        for row in report['shipments']:
            self.assertEqual(row['quantity_p']%24,0);self.assertEqual(row['p_per_pl'],240)
            self.assertGreaterEqual(row['quantity_p'],row['required_p'])
        for warehouse in ('KOBE','SUGITO'):
            ds=[r['dispatch'] for r in report['shipments'] if r['warehouse']==warehouse]
            self.assertEqual(len(ds),len(set(ds)))
            self.assertEqual(len([r for r in report['timeline'] if r['warehouse']==warehouse]),14)

    def test_daily_plan_and_confirmed_inbound_not_double_counted(self):
        report=self.plan()
        k=[r for r in report['timeline'] if r['warehouse']=='KOBE']
        s=[r for r in report['timeline'] if r['warehouse']=='SUGITO']
        self.assertEqual(sum(r['transit_p'] for r in s),24)
        self.assertEqual(sum(r['planned_inbound_p'] for r in k),sum(r['quantity_p'] for r in report['shipments'] if r['warehouse']=='KOBE' and r['arrival']<=report['end']))
        for row in k:
            elapsed=(date.fromisoformat(row['date'])-DAY).days+1
            inbound=sum(r['quantity_p'] for r in report['shipments'] if r['warehouse']=='KOBE' and r['arrival']<=row['date'])
            self.assertEqual(row['projected_after_plan_p'],120-60*elapsed+inbound)
        self.assertEqual(report['confirmed_inbound'],self.data().tables['transit'])

    def test_warehouses_independent(self):
        before=self.plan()
        self.change('inventory',lambda rs:[dict(r,quantity_p='99999') if r['warehouse']=='KOBE' else r for r in rs])
        after=self.plan()
        self.assertEqual([r for r in before['shipments'] if r['warehouse']=='SUGITO'],[r for r in after['shipments'] if r['warehouse']=='SUGITO'])
        self.assertFalse(any(r['warehouse']=='KOBE' for r in after['shipments']))

    def test_capacity_16_boundary_split_and_partial_exclusive_product_slots(self):
        template=self.plan()['shipments'][0]
        for count,expected in ((16,[16]),(17,[16,1]),(33,[16,16,1])):
            trips=pack_trips([dict(template,quantity_p=240*count)])
            self.assertEqual([t['pallets'] for t in trips],expected)
        trips=pack_trips([dict(template,product='A',quantity_p=24),dict(template,product='B',quantity_p=24)])
        self.assertEqual(trips[0]['pallets'],2)
        self.assertEqual([i['pallets'] for i in trips[0]['items']],[1,1])
        self.assertEqual(sum(i['quantity_p'] for t in trips for i in t['items']),48)

    def test_overage_is_reference_comparison_not_expired_stock_or_waste(self):
        self.change('products',lambda rs:[dict(r,shelf_life_days='2') for r in rs])
        report=self.plan()
        self.assertTrue(any(r['excess_vs_forecast_p']>0 for r in report['expiry_risks']))
        self.assertTrue(any('廃棄量ではない' in w for w in report['warnings']))

    def test_missing_long_life_forecasts_do_not_invent_expired_quantities(self):
        self.change('products',lambda rs:[dict(r,shelf_life_days='365') for r in rs])
        report=self.plan()
        self.assertTrue(all(r['excess_vs_forecast_p'] is None and r['forecast_until_expiry_p'] is None for r in report['expiry_risks']))
        self.assertTrue(any('確認不能' in w for w in report['warnings']))

    def test_missing_horizon_forecast_stops_whole_plan_without_saved_success(self):
        self.change('forecast',lambda rs:[r for r in rs if r['date']!=str(DAY+timedelta(days=13))])
        with self.assertRaises(DataError):self.plan()
        self.assertEqual(self.service.delivery_plans(),[])

    def test_provisional_is_explicit_and_never_formally_confirmed(self):
        self.change('forecast',lambda rs:[dict(r,status='provisional') for r in rs])
        with self.assertRaises(DataError):self.plan()
        report=self.service.calculate_delivery_plan(self.data(),DAY,allow_provisional=True)
        with self.assertRaises(DataError):self.service.confirm_delivery_plan(report['plan_id'],'架空担当者',True)
        self.assertFalse(self.service.delivery_plan(report['plan_id'])['formal'])

    def test_formal_requires_ack_and_operator_saved_plan_ignores_mutated_caller(self):
        report=self.plan();pid=report['plan_id'];report['shipments'][0]['quantity_p']=999999
        for operator,ack in (('',True),('担当',False),('担当',1)):
            with self.assertRaises(DataError):self.service.confirm_delivery_plan(pid,operator,ack)
        confirmed=self.service.confirm_delivery_plan(pid,'架空担当者',True)
        self.assertTrue(confirmed['formal']);self.assertNotEqual(confirmed['shipments'][0]['quantity_p'],999999)
        with self.assertRaises(DataError):self.service.confirm_delivery_plan(pid,'担当',True)

    def test_reopen_and_later_confirmation_keep_original_calculation_day(self):
        report=self.service.calculate_delivery_plan(self.data(),DAY+timedelta(days=1));pid=report['plan_id']
        self.service.close();self.service=Service(self.root/'home/history.db',lambda:NOW+timedelta(days=1))
        final=self.service.confirm_delivery_plan(pid,'架空担当者',True)
        self.assertEqual(final['calculated_at'],NOW.isoformat());self.assertEqual(final['assumed_manufacture_date'],str(DAY))
        self.assertEqual(final['shipments'],report['shipments'])

    def test_past_plan_confirmation_is_rejected(self):
        report=self.plan();self.service.clock=lambda:NOW+timedelta(days=1)
        with self.assertRaises(DataError):self.service.confirm_delivery_plan(report['plan_id'],'担当',True)

    def test_expiry_management_changes_copy_with_reason_operator_and_history(self):
        data=self.data();original=(self.csv/'products.csv').read_bytes()
        fresh=self.service.update_product_expiry(data,'P001','use_by',9,'確認した期限','架空担当者')
        self.assertEqual(fresh.tables['products'][0]['shelf_life_days'],9)
        self.assertEqual((self.csv/'products.csv').read_bytes(),original)
        self.assertNotEqual(data.digest,fresh.digest)
        payload=json.loads(self.service.db.execute("SELECT payload FROM events WHERE kind='product_expiry_change'").fetchone()[0])
        self.assertEqual(payload['before']['shelf_life_days'],'10');self.assertEqual(payload['after']['shelf_life_days'],'9')

    def test_old_master_can_be_promoted_without_fabricating_other_product_expiry(self):
        base=(ROOT/'samples/replenishment/products.csv').read_text(encoding='utf-8');(self.csv/'products.csv').write_text(base,encoding='utf-8')
        fresh=self.service.update_product_expiry(self.data(),'P001','best_before',10,'確認','担当')
        self.assertEqual(fresh.tables['products'][0]['shelf_life_days'],10)

    def test_backup_restore_preserves_plan_and_read_only_gate(self):
        report=self.plan();self.service.confirm_delivery_plan(report['plan_id'],'担当',True)
        archive=self.root/'backup.zip';manifest=create_backup(self.service.db,self.root/'home',archive,lambda:NOW)
        restored=restore_backup(archive,self.root/'restored',manifest['archive_sha256'],lambda:NOW)
        viewer=Service(restored/'history.db',lambda:NOW)
        try:
            r=viewer.delivery_plan(report['plan_id']);self.assertTrue(r['formal']);self.assertTrue(r['restored_view'])
            with self.assertRaises(DataError):viewer.confirm_delivery_plan(report['plan_id'],'担当',True)
        finally:viewer.close()

    def test_direct_exports_values_not_actual_lots_and_no_overwrite(self):
        report=self.plan();folder=self.root/'reports';paths=export_plan(report,folder)
        self.assertEqual(len(paths),7)
        from openpyxl import load_workbook
        from pypdf import PdfReader
        workbook=load_workbook(next(p for p in paths if p.suffix=='.xlsx'),read_only=True)
        try:
            sheet=workbook.active;self.assertEqual(sheet['I7'].value,str(DAY+timedelta(days=10)))
            self.assertIn('未確定',sheet['A2'].value)
        finally:workbook.close()
        text=''.join(p.extract_text() for p in PdfReader(next(p for p in paths if p.suffix=='.pdf')).pages)
        self.assertIn('概算期限',text);self.assertIn('仮製造日',text)
        with self.assertRaises(DataError):export_plan(report,folder)
        self.assertEqual(len(list(folder.iterdir())),7)

    def test_ui_confirm_invalidation_and_saved_history(self):
        root=tk.Tk();root.withdraw();app=App(root,self.root/'ui')
        try:
            app.service.clock=lambda:NOW;app.data=self.data();app.day.set(str(DAY));app.compute_delivery()
            self.assertTrue(app.delivery_tree.get_children());app.operator.set('架空担当者');app.delivery_ack.set(True)
            app.day.set(str(DAY+timedelta(days=1)));self.assertIsNone(app.delivery_report);self.assertFalse(app.delivery_ack.get())
            app.day.set(str(DAY));app.compute_delivery();app.delivery_ack.set(True);app.confirm_delivery()
            self.assertTrue(app.delivery_report['formal']);self.assertFalse(app.delivery_ack.get())
        finally:app.close()

    def test_calculation_day_uses_japan_timezone_and_overflow_is_clear(self):
        instant=datetime(2026,11,8,16,tzinfo=timezone.utc)
        report=build_plan(self.data(),DAY,instant)
        self.assertEqual(report['assumed_manufacture_date'],str(DAY))
        self.change('products',lambda rs:[dict(r,shelf_life_days='999999999999') for r in rs])
        with self.assertRaises(DataError):self.plan()

    def test_tampered_saved_plan_fails_recalculation_and_remains_unconfirmed(self):
        report=self.plan();pid=report['plan_id']
        saved=json.loads(self.service.db.execute("SELECT payload FROM events WHERE kind='delivery_plan'").fetchone()[0])
        saved['report']['shipments'][0]['estimated_expiry']='2099-01-01'
        with self.service.db:self.service.db.execute("UPDATE events SET payload=? WHERE kind='delivery_plan'",(json.dumps(saved),))
        with self.assertRaises(DataError):self.service.confirm_delivery_plan(pid,'担当',True)
        self.assertFalse(self.service.delivery_plan(pid)['formal'])

    def test_multiple_products_can_be_reviewed_one_at_a_time_without_zero_fill(self):
        self.change('products',lambda rs:[dict(rs[0],expiry_type='',shelf_life_days=''),dict(rs[0],product='P002',expiry_type='',shelf_life_days='')])
        for table in ('inventory','replenishment'):
            self.change(table,lambda rs:rs+[dict(r,product='P002') for r in rs])
        data=self.data();fresh=self.service.update_product_expiry(data,'P001','best_before',12,'確認','担当')
        self.assertNotIn('shelf_life_days',fresh.tables['products'][1])
        with self.assertRaises(DataError) as caught:build_plan(fresh,DAY,NOW)
        self.assertTrue(any(i.product=='P002' and i.field=='shelf_life_days' for i in caught.exception.issues))
        fresh=self.service.update_product_expiry(fresh,'P002','use_by',8,'確認','担当')
        self.assertEqual(fresh.tables['products'][1]['shelf_life_days'],8)

    def test_product_specific_pallet_factors_and_separate_warehouse_trips(self):
        report=self.plan();template=report['shipments'][0]
        trips=pack_trips([dict(template,product='A',quantity_p=3840,p_per_pl=240),
                          dict(template,product='B',quantity_p=96,p_per_pl=48),dict(template,warehouse='SUGITO',quantity_p=24)])
        self.assertEqual([t['pallets'] for t in trips],[16,2,1])
        self.assertEqual([t['warehouse'] for t in trips],['KOBE','KOBE','SUGITO'])

    def test_export_failure_does_not_leave_partial_reports_or_remove_existing_files(self):
        report=self.plan();folder=self.root/'failed';folder.mkdir();(folder/'keep').write_text('keep')
        original=Path.open
        def fail(path,*args,**kwargs):
            if path.suffix=='.pdf' and args and args[0]=='xb':raise OSError('disk error')
            return original(path,*args,**kwargs)
        with patch.object(Path,'open',fail),self.assertRaises(OSError):export_plan(report,folder)
        self.assertEqual([p.name for p in folder.iterdir()],['keep'])


if __name__=='__main__':unittest.main()
