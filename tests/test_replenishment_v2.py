import copy
import csv
from datetime import timedelta
import io
import json
from pathlib import Path
import tempfile
import tkinter as tk
from tkinter import ttk
import unittest
from unittest.mock import patch
from zipfile import ZipFile
from xml.etree import ElementTree as ET

from bunsen.core import DataError, V2_SCHEMA, calculate, load, case_p
from bunsen.service import Service, report_html, write_reports
from bunsen.backup import create_backup, restore_backup, validate_database
from bunsen.diagnostics import diagnose
from bunsen.shipping_output import write_documents, xlsx_bytes
from bunsen.ui import App
from test_workflow import NOW, DAY, ROOT


class ReplenishmentV2Tests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name).resolve();self.csv=self.root/'csv';self.csv.mkdir()
        for p in (ROOT/'samples/replenishment').glob('*.csv'):(self.csv/p.name).write_bytes(p.read_bytes())
        self.service=Service(self.root/'home/history.db',lambda:NOW)

    def tearDown(self):
        self.service.close();self.temp.cleanup()

    def data(self):return load(self.csv,NOW)

    def change(self,table,fn):
        p=self.csv/(table+'.csv');reader=csv.DictReader(io.StringIO(p.read_text(encoding='utf-8')));rows=list(reader)
        rows=fn(rows)
        with p.open('w',encoding='utf-8',newline='') as f:
            writer=csv.DictWriter(f,fieldnames=reader.fieldnames,lineterminator='\n');writer.writeheader();writer.writerows(rows)

    def formal(self):
        data=self.service.import_folder(self.csv);lines=self.service.calculate(data,DAY)
        return self.service.issue(data,lines,DAY,'架空担当者',True,True)

    def test_independent_warehouses_no_bunsen_data_and_formal_without_lots(self):
        data=self.data();self.assertEqual(data.workflow,2)
        self.assertNotIn('dry_stock',data.tables)
        lines=self.service.calculate(data,DAY)
        self.assertEqual([(r['warehouse'],r['required_p'],r['suggested_p']) for r in lines],[('KOBE',120,120),('SUGITO',48,48)])
        final=self.service.issue(data,lines,DAY,'架空担当者',True,True)
        self.assertEqual(final['workflow'],2);self.assertEqual(final['meaning'],'replenishment_instruction_not_shipment_actual')
        self.assertNotIn('実ロット未確認',str(final));self.assertEqual(final['validation'],[])

    def test_bunsen_inventory_is_ignored_even_when_invalid(self):
        baseline=calculate(self.data(),DAY)
        (self.csv/'dry_stock.csv').write_bytes(b'not even valid UTF-8\xff')
        (self.csv/'vehicles.csv').write_bytes(b'not used')
        data=self.data();self.assertNotIn('dry_stock.csv',data.raw)
        self.assertEqual(calculate(data,DAY),baseline)
        self.service.issue(data,baseline,DAY,'架空担当者',True,True)

    def test_excess_stock_gives_zero(self):
        self.change('inventory',lambda rs:[dict(r,quantity_p='9999') for r in rs])
        self.assertTrue(all(r['required_p']==r['suggested_p']==0 for r in calculate(self.data(),DAY)))

    def test_one_warehouse_update_does_not_change_the_other(self):
        original=calculate(self.data(),DAY)
        self.change('inventory',lambda rs:[dict(r,quantity_p='9999') if r['warehouse']=='SUGITO' else r for r in rs])
        current=calculate(self.data(),DAY)
        self.assertEqual(original[0],current[0]);self.assertEqual(current[1]['required_p'],0)

    def test_pl_plus_case_does_not_round_everything_up_to_whole_pallet(self):
        self.change('inventory',lambda rs:[dict(r,quantity_p='1') for r in rs])
        self.change('forecast',lambda rs:[dict(r,quantity_p='61') for r in rs])
        row=calculate(self.data(),DAY)[0]
        self.assertEqual(row['required_p'],243);self.assertEqual(row['suggested_p'],264)
        self.assertEqual(divmod(row['suggested_p']//24,10),(1,1))

    def test_missing_setting_or_conversion_does_not_become_zero(self):
        self.change('products',lambda rs:[dict(r,p_per_ball='') for r in rs])
        with self.assertRaises(DataError):self.formal()
        self.assertEqual(self.service.history(),[])

    def test_stale_revision_cannot_issue_again(self):
        self.formal();data=self.data();lines=calculate(data,DAY)
        with self.assertRaises(DataError):self.service.issue(data,lines,DAY,'担当',True,True,expected_revision=0)
        self.assertEqual(len(self.service.history()),1)

    def test_future_dispatch_does_not_require_future_bunsen_stock(self):
        data=self.data();day=DAY+timedelta(days=1);lines=self.service.calculate(data,day)
        final=self.service.issue(data,lines,day,'架空担当者',True,True)
        self.assertEqual(final['dispatch'],str(day))

    def test_existing_full_product_master_is_reused_without_unused_shipping_constraints(self):
        from bunsen.core import SCHEMA
        old=load(ROOT/'samples/morning',NOW).tables['products'][0]
        old['weight_g']='';old['min_remaining_days']=''
        with (self.csv/'products.csv').open('w',encoding='utf-8',newline='') as f:
            writer=csv.DictWriter(f,fieldnames=SCHEMA['products'].split(','));writer.writeheader();writer.writerow(old)
        self.assertEqual(calculate(self.data(),DAY)[0]['suggested_p'],120)

    def test_new_business_import_rejects_legacy_data_without_success_event(self):
        with self.assertRaises(DataError):self.service.import_folder(ROOT/'samples/morning',workflow=2)
        self.assertEqual([r[0] for r in self.service.db.execute('SELECT kind FROM events')],['import_failed'])

    def test_late_receipt_does_not_hide_earlier_safety_shortage(self):
        self.change('inventory',lambda rs:[dict(r,quantity_p='0') for r in rs])
        self.change('transit',lambda rs:[dict(rs[0],warehouse='KOBE',quantity_p='999',arrival_date='2026-11-11')])
        row=next(r for r in calculate(self.data(),DAY) if r['warehouse']=='KOBE')
        self.assertEqual(row['required_p'],180)
        self.assertIn('2026-11-09',row['warning'])
        self.assertEqual(row['timeline'][0]['transit_p'],0)
        self.assertEqual(row['timeline'][2]['transit_p'],999)

    def test_cycle_and_safety_day_configuration_are_used(self):
        old=calculate(self.data(),DAY)[0]['required_p']
        self.change('replenishment',lambda rs:[dict(r,cycle_days='4',safety_days='2') for r in rs])
        row=calculate(self.data(),DAY)[0]
        self.assertEqual(row['end'],'2026-11-13');self.assertGreater(row['required_p'],old)
        self.assertEqual(row['safety_p'],120)

    def test_confirmed_receipt_counts_once_on_its_date(self):
        row=next(r for r in calculate(self.data(),DAY) if r['warehouse']=='SUGITO')
        self.assertEqual(sum(r['transit_p'] for r in row['timeline']),24)
        self.change('transit',lambda rs:rs+[dict(rs[0])])
        with self.assertRaises(DataError):self.data()

    def test_receipt_in_confirmed_inventory_is_rejected(self):
        self.change('transit',lambda rs:[dict(r,arrival_date='2026-11-08') for r in rs])
        with self.assertRaises(DataError) as e:calculate(self.data(),DAY)
        self.assertIn('TRANSIT_ALREADY_ARRIVED',{i.code for i in e.exception.issues})

    def test_closing_inventory_does_not_consume_that_days_forecast_twice(self):
        self.change('inventory',lambda rs:[dict(r,as_of='2026-11-08T23:00:00+09:00',basis='closing') for r in rs])
        self.change('replenishment',lambda rs:[dict(r,transit_checked_at='2026-11-08T23:00:00+09:00') for r in rs])
        rows=calculate(self.data(),DAY)
        self.assertEqual(rows[0]['timeline'][0]['date'],'2026-11-09')
        self.assertEqual(rows[0]['required_p'],120)

    def test_noncommon_master_conversion_and_pl_case_remainder(self):
        self.change('products',lambda rs:[dict(r,p_per_ball='3',balls_per_case='5',cases_per_pl='8') for r in rs])
        self.change('inventory',lambda rs:[dict(r,quantity_p='1') for r in rs])
        row=calculate(self.data(),DAY)[0]
        self.assertEqual(row['required_p'],239);self.assertEqual(row['suggested_p'],240)
        self.assertEqual(case_p(self.data().tables['products'][0]),15)
        final=self.formal();sheet=ET.fromstring(ZipFile(io.BytesIO(xlsx_bytes(final,'KOBE'))).read('xl/worksheets/sheet1.xml'))
        ns={'s':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
        self.assertEqual(sheet.find('.//s:c[@r="J7"]/s:v',ns).text,'2')
        self.assertEqual(sheet.find('.//s:c[@r="K7"]/s:v',ns).text,'0')

    def test_forecast_missing_is_not_zero_and_no_issue_saved(self):
        self.change('forecast',lambda rs:[r for r in rs if r['date']!='2026-11-11'])
        with self.assertRaises(DataError):self.formal()
        self.assertEqual(self.service.history(),[])

    def test_safety_forecast_missing_blocks_calculation(self):
        self.change('forecast',lambda rs:[r for r in rs if r['date']!='2026-11-12'])
        with self.assertRaises(DataError):calculate(self.data(),DAY)

    def test_provisional_only_by_explicit_selection_and_never_formal(self):
        self.change('forecast',lambda rs:[dict(r,status='imputed') if r['date']=='2026-11-10' else r for r in rs])
        data=self.data()
        with self.assertRaises(DataError):calculate(data,DAY)
        lines=self.service.calculate(data,DAY,allow_provisional=True)
        draft=self.service.issue(data,lines,DAY,'架空担当者',False)
        self.assertFalse(draft['formal'])
        with self.assertRaises(DataError):self.service.issue(data,lines,DAY,'架空担当者',True,True)
        self.assertEqual(len(self.service.history()),1)

    def test_edit_history_keeps_original_calculation_and_reason(self):
        data=self.data();lines=self.service.calculate(data,DAY)
        self.service.edit_quantity(data,lines,('KOBE','P001'),144,'担当者による補充増量','架空担当者')
        final=self.service.issue(data,lines,DAY,'架空担当者',True,True)
        row=final['lines'][0];self.assertEqual((row['required_p'],row['suggested_p'],row['quantity_p']),(120,120,144))
        event=json.loads(self.service.db.execute("SELECT payload FROM events WHERE kind='edit'").fetchone()[0])
        self.assertEqual(event['before']['quantity_p'],120);self.assertEqual(event['after']['quantity_p'],144)
        self.assertEqual(event['reason'],'担当者による補充増量')

    def test_manual_quantity_requires_reason_case_and_ack(self):
        data=self.data();lines=calculate(data,DAY);lines[0]['quantity_p']=144
        for operator,ack in [('',True),('担当',False),('担当',True)]:
            with self.assertRaises(DataError):self.service.issue(data,lines,DAY,operator,True,ack)
        lines[0]['edit_reason']='理由';lines[0]['quantity_p']=145
        with self.assertRaises(DataError):self.service.issue(data,lines,DAY,'担当',True,True)

    def test_reference_cannot_silently_drop_pack_remainder_in_pl_case_display(self):
        data=self.data();lines=calculate(data,DAY);lines[0].update(quantity_p=145,edit_reason='理由')
        with self.assertRaises(DataError):self.service.issue(data,lines,DAY,'担当',False)
        with self.assertRaises(DataError):self.service.edit_quantity(data,lines,('KOBE','P001'),145,'理由','担当')

    def test_tampering_calculation_or_mode_does_not_bypass_revalidation(self):
        data=self.data();lines=calculate(data,DAY);lines[0]['required_p']=0;lines[0]['forecast_confirmed']=True
        final=self.service.issue(data,lines,DAY,'担当',True,True)
        self.assertEqual(final['lines'][0]['required_p'],120)

    def test_settings_history_and_no_source_overwrite(self):
        data=self.data();old=(self.csv/'replenishment.csv').read_bytes()
        new=self.service.update_settings(data,('KOBE','P001'),dict(cycle_days=3,coverage_days=3,safety_days=2,lead_days=2),'配送条件確認','架空管理者')
        self.assertNotEqual(new.digest,data.digest);self.assertEqual((self.csv/'replenishment.csv').read_bytes(),old)
        self.assertEqual(len(self.service.db.execute("SELECT payload FROM events WHERE kind='settings_change'").fetchall()),1)
        with self.assertRaises(DataError):self.service.update_settings(data,('KOBE','P001'),dict(cycle_days=0,coverage_days=3,safety_days=2,lead_days=2),'理由','管理者')

    def test_new_backup_restore_preserves_legacy_database_schema(self):
        final=self.formal();write_reports(self.service.report_payload(final['number']),self.root/'home/reports')
        self.assertEqual(validate_database(self.service.db)['issues'],1)
        path=self.root/'backup.zip';meta=create_backup(self.service.db,self.root/'home',path,lambda:NOW)
        restored=restore_backup(path,self.root/'restore',meta['archive_sha256'],lambda:NOW)
        view=Service(restored/'history.db',lambda:NOW)
        try:
            self.assertEqual(view.get_issue(final['number']),final)
            self.assertIn('復元履歴の参考帳票',report_html(view.report_payload(final['number']),'KOBE'))
            with self.assertRaises(DataError):view.issue(None,None,None,'',True,True)
        finally:view.close()

    def test_direct_excel_and_pdf_per_warehouse_no_template_or_formula_dependency(self):
        from pypdf import PdfReader
        from openpyxl import load_workbook
        final=self.formal();paths=write_documents(self.service.report_payload(final['number']),self.root/'exports')
        self.assertEqual(len(paths),6)
        for warehouse in ('KOBE','SUGITO'):
            pdf=next(p for p in paths if p.name.endswith(warehouse+'.pdf'))
            self.assertGreater(len(PdfReader(pdf).pages),0)
            xlsx=next(p for p in paths if p.name.endswith(warehouse+'.xlsx'))
            with ZipFile(xlsx) as z:
                xml=z.read('xl/worksheets/sheet1.xml').decode()
                self.assertNotIn('<f>',xml);self.assertIn('実出荷実績',xml)
            workbook=load_workbook(xlsx,data_only=False)
            sheet=workbook['出荷指示']
            self.assertEqual(sheet['A7'].value,'P001')
            self.assertEqual(sheet['I7'].value,120 if warehouse=='KOBE' else 48)
            self.assertEqual(sheet['A6'].font.bold,True)
            self.assertEqual(sheet.print_title_rows,'$1:$6')
            self.assertEqual(sheet.page_setup.orientation,'landscape')
            workbook.close()
            self.assertIn(warehouse,pdf.name)
        with self.assertRaises(DataError):write_documents(final,self.root/'exports')

    def test_diagnostic_has_no_bunsen_stock_requirement(self):
        report=diagnose(self.csv,DAY,NOW)
        self.assertTrue(report['calculation_possible']);self.assertFalse(report['morning_stock_checked'])
        self.assertFalse(any(i['code']=='MORNING_MISSING' for i in report['issues']))

    def test_output_failure_removes_only_new_files(self):
        final=self.formal();folder=self.root/'exports';folder.mkdir();sentinel=folder/'keep.txt';sentinel.write_text('keep')
        original=Path.open
        def fail(path,*args,**kwargs):
            if path.suffix=='.pdf' and args and args[0]=='xb':raise OSError('disk failure')
            return original(path,*args,**kwargs)
        with patch.object(Path,'open',fail),self.assertRaises(OSError):write_documents(final,folder)
        self.assertEqual([p.name for p in folder.iterdir()],['keep.txt'])

    def test_one_existing_report_is_not_overwritten_or_partially_exported(self):
        final=self.formal();folder=self.root/'exports';folder.mkdir()
        existing=folder/(final['number']+'-KOBE.pdf');existing.write_bytes(b'keep')
        with self.assertRaises(DataError):write_documents(final,folder)
        self.assertEqual(existing.read_bytes(),b'keep');self.assertEqual(len(list(folder.iterdir())),1)

    def test_new_ui_failed_import_does_not_keep_old_confirmable_data(self):
        root=tk.Tk();root.withdraw();app=App(root,self.root/'ui-home');app.service.clock=lambda:NOW
        try:
            app.data=self.data();app.day.set(str(DAY));app.compute();app.ack.set(True)
            (self.csv/'forecast.csv').unlink()
            with patch('bunsen.ui.filedialog.askdirectory',return_value=str(self.csv)),self.assertRaises(DataError):app.import_data()
            self.assertIsNone(app.data);self.assertIsNone(app.lines);self.assertFalse(app.ack.get())
            with self.assertRaises(DataError):app.issue(True)
        finally:app.close()

    def test_operational_ui_has_no_lot_or_morning_controls(self):
        root=tk.Tk();root.withdraw();app=App(root,self.root/'ui-home');app.service.clock=lambda:NOW
        try:
            with patch('bunsen.ui.filedialog.askdirectory',return_value=str(self.csv)):app.import_data()
            app.day.set(str(DAY));app.compute();self.assertEqual(len(app.lines),2)
            def widgets(w):return [w]+[v for c in w.winfo_children() for v in widgets(c)]
            labels=[w.cget('text') for w in widgets(app.daily) if isinstance(w,(tk.Label,ttk.Label,ttk.Button,ttk.Checkbutton))]
            self.assertFalse(any('実ロット' in str(t) or '朝照合' in str(t) and '不要' not in str(t) for t in labels))
            app.operator.set('架空担当者');app.ack.set(True);app.issue(True)
            self.assertIsNotNone(app.last_number)
            self.assertEqual(app.service.get_issue(app.last_number)['workflow'],2)
            self.assertTrue(app.finalized)
            self.assertEqual(app.tree.item('0')['values'][8],120)
            with self.assertRaises(DataError):app.issue(True)
            app.tree.selection_set('0');app.selected();app.qty.set('144')
            app.compute()
            self.assertFalse(app.finalized)
        finally:app.close()
