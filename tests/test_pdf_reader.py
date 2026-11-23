"""Fictional fixtures reproduce inspected geometry, never client data."""
import base64
import io
import json
from pathlib import Path
import tempfile
import tkinter as tk
import unittest
from datetime import datetime
from unittest.mock import patch
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from bunsen.core import DataError, JST
from bunsen.pdf_reader import read_bytes,read_report,suggested_quantity,write_reviewed
from bunsen.service import Service

JAN='4900000000001'
MASTER=dict(product=JAN,name='架空商品',expiry_type='best_before',shelf_life_days=12,kobe_p_per_pl=150,sugito_p_per_pl=300)
NOW=datetime(2026,11,21,9,tzinfo=JST)

from bunsen.pdf_demo import fictional_report as fixture

class PDFReaderTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)
    def tearDown(self):self.temp.cleanup()
    def report(self,warehouse='KOBE',**kwargs):return read_bytes(fixture(warehouse,**kwargs))
    def reviewed(self,**kwargs):
        return {w:dict(report=self.report(w,**kwargs),as_of='2026-11-20T18:00:00+09:00',basis='closing',blank_policy='unknown') for w in ('KOBE','SUGITO')}
    def decisions(self,quantity=73,reason=''):return {(w,JAN):dict(quantity_p=quantity,reason=reason) for w in ('KOBE','SUGITO')}
    def test_full_jan_outside_media_box_and_current_total(self):
        report=self.report();row=report['rows'][0]
        self.assertEqual(row['product'],JAN);self.assertEqual(row['cases'],7);self.assertEqual(row['loose'],3)
        self.assertEqual(suggested_quantity(row),73);self.assertEqual(report['warehouse'],'KOBE')
    def test_blank_never_implicitly_becomes_zero(self):
        row=self.report(loose=None)['rows'][0]
        self.assertIsNone(suggested_quantity(row));self.assertEqual(suggested_quantity(row,'loose_zero'),70)
        row=self.report(cases=None,loose=None)['rows'][0]
        self.assertIsNone(suggested_quantity(row,'loose_zero'));self.assertEqual(suggested_quantity(row,'all_zero'),0)
    def test_explicit_zero_is_valid(self):self.assertEqual(suggested_quantity(self.report(cases='0',loose='0')['rows'][0]),0)
    def test_cross_page_total_only_once(self):
        report=self.report(page_count=2);self.assertEqual(len(report['rows']),1)
        self.assertEqual(report['rows'][0]['pages'],[1,2]);self.assertEqual(suggested_quantity(report['rows'][0]),73)
    def test_missing_or_reordered_pages_are_rejected(self):
        from pypdf import PdfReader,PdfWriter
        source=PdfReader(io.BytesIO(fixture(page_count=2)))
        for pages in [[source.pages[0]],[source.pages[1],source.pages[0]]]:
            writer=PdfWriter()
            for page in pages:writer.add_page(page)
            stream=io.BytesIO();writer.write(stream)
            with self.assertRaises(DataError):read_bytes(stream.getvalue())
    def test_next_day_creation_is_not_substituted_for_stock_date(self):
        report=self.report(printed_day='2026/11/21')
        self.assertEqual(report['stock_date'],'2026-11-20');self.assertIn('2026/11/21',report['printed_at'])
    def test_six_digit_jan_is_not_reconstructed(self):
        with self.assertRaises(DataError):self.report(jan='000001')
    def test_missing_total_stops(self):
        with self.assertRaises(DataError):self.report(total=False)
    def test_negative_or_fractional_or_multiple_number_stops(self):
        for value in ['-1','1.5','1 2']:
            with self.subTest(value=value),self.assertRaises(DataError):self.report(cases=value)
    def test_missing_factor_is_unconfirmed(self):self.assertIsNone(suggested_quantity(self.report(factor=None)['rows'][0]))
    def test_invalid_or_image_pdf_stops(self):
        with self.assertRaises(DataError):read_bytes(b'%PDF-invalid')
        buf=io.BytesIO();c=canvas.Canvas(buf);c.showPage();c.save()
        with self.assertRaises(DataError):read_bytes(buf.getvalue())
    def test_reports_cannot_be_swapped(self):
        reports=self.reviewed();reports['KOBE']['report']=self.report('SUGITO')
        with self.assertRaises(DataError):write_reviewed(reports,self.decisions(),[MASTER],'担当',self.path)
    def test_reviewed_output_preserves_raw_and_correction_reason(self):
        reports=self.reviewed();paths,audit=write_reviewed(reports,self.decisions(80,'原本再確認'),[MASTER],'担当',self.path)
        self.assertEqual(len(paths),5)
        self.assertEqual(audit['decisions'][0]['suggested_p'],73);self.assertEqual(audit['decisions'][0]['quantity_p'],80)
        self.assertEqual(base64.b64decode(reports['KOBE']['report']['content_base64']),fixture())
        with self.assertRaises(FileExistsError):write_reviewed(reports,self.decisions(),[MASTER],'担当',self.path)
    def test_missing_or_modified_quantity_requires_reason(self):
        for reports,decisions in [(self.reviewed(loose=None),self.decisions(70)),(self.reviewed(),self.decisions(80))]:
            with self.assertRaises(DataError):write_reviewed(reports,decisions,[MASTER],'担当',self.path)
    def test_tampered_extraction_is_rejected(self):
        reports=self.reviewed();reports['KOBE']['report']['rows'][0]['cases']=999
        with self.assertRaises(DataError):write_reviewed(reports,self.decisions(),[MASTER],'担当',self.path)
    def test_date_mismatch_is_rejected(self):
        reports=self.reviewed();reports['KOBE']['as_of']='2026-11-19T18:00:00+09:00'
        with self.assertRaises(DataError):write_reviewed(reports,self.decisions(),[MASTER],'担当',self.path)
    def test_source_change_is_rejected(self):
        source=self.path/'source.pdf';source.write_bytes(fixture());report=read_report(source);source.write_bytes(fixture(cases='8'))
        reports=self.reviewed();reports['KOBE']['report']=report
        with self.assertRaises(DataError):write_reviewed(reports,self.decisions(),[MASTER],'担当',self.path)
    def test_zip_reads_only_selected_member_and_never_extracts_paths(self):
        import zipfile
        source=self.path/'reports.zip'
        with zipfile.ZipFile(source,'w') as z:z.writestr('../chosen.pdf',fixture());z.writestr('other.pdf',fixture(cases='8'))
        self.assertEqual(read_report(source,'../chosen.pdf')['rows'][0]['cases'],7)
        self.assertFalse((self.path.parent/'chosen.pdf').exists())
    def test_service_ack_revision_and_review_history(self):
        service=Service(self.path/'home/history.db',lambda:NOW)
        try:
            service.save_jan_master(MASTER,'担当','架空確認',True,0,True);revision=service.jan_master()['revision']
            with self.assertRaises(DataError):service.save_pdf_inventory(self.reviewed(),self.decisions(),'担当',False,revision,self.path)
            with self.assertRaises(DataError):service.save_pdf_inventory(self.reviewed(),self.decisions(),'担当',True,0,self.path)
            service.save_pdf_inventory(self.reviewed(),self.decisions(),'担当',True,revision,self.path)
            payload=json.loads(service.db.execute("SELECT payload FROM events WHERE kind='pdf_inventory_review'").fetchone()[0])
            self.assertEqual(payload['decisions'][0]['source']['cases'],7);self.assertEqual(payload['decisions'][0]['quantity_p'],73)
        finally:service.close()
    def test_db_failure_removes_only_new_outputs(self):
        import sqlite3
        service=Service(self.path/'home/history.db',lambda:NOW)
        target=self.path/'output';target.mkdir();keep=target/'keep.txt';keep.write_text('keep')
        try:
            service.save_jan_master(MASTER,'担当','架空確認',True,0,True)
            service.db.execute("CREATE TEMP TRIGGER reject_pdf BEFORE INSERT ON events WHEN NEW.kind='pdf_inventory_review' BEGIN SELECT RAISE(ABORT,'test failure'); END")
            with self.assertRaises(sqlite3.IntegrityError):service.save_pdf_inventory(self.reviewed(),self.decisions(),'担当',True,service.jan_master()['revision'],target)
            self.assertEqual(list(target.iterdir()),[keep])
        finally:service.close()
    def test_future_basis_and_read_only_cannot_save(self):
        service=Service(self.path/'home/history.db',lambda:NOW)
        try:
            service.save_jan_master(MASTER,'担当','架空確認',True,0,True);revision=service.jan_master()['revision']
            reports=self.reviewed()
            reports['KOBE']['as_of']='2026-11-22T18:00:00+09:00'
            with self.assertRaises(DataError):service.save_pdf_inventory(reports,self.decisions(),'担当',True,revision,self.path)
            service.read_only=True
            with self.assertRaises(DataError):service.save_pdf_inventory(self.reviewed(),self.decisions(),'担当',True,revision,self.path)
            self.assertFalse((self.path/'inventory.csv').exists())
        finally:service.close()
    def test_ui_loaded_candidates_are_not_saved_and_wrong_warehouse_clears(self):
        from bunsen.ui import App
        from bunsen.pdf_reader_ui import PDFInventoryDialog
        root=tk.Tk();root.withdraw();app=App(root,self.path/'home')
        try:
            app.service.save_jan_master(MASTER,'担当','架空確認',True,0,True)
            dialog=PDFInventoryDialog(app);source=self.path/'source.pdf';source.write_bytes(fixture())
            dialog.load('KOBE',source);self.assertEqual(dialog.items[('KOBE',JAN)]['quantity'],73)
            self.assertEqual(app.service.db.execute("SELECT count(*) FROM events WHERE kind='pdf_inventory_review'").fetchone()[0],0)
            dialog.tree.selection_set('0');dialog.selected();dialog.quantity.set('80')
            with self.assertRaises(DataError):dialog.pending()
            dialog.reason.set('原本再確認');dialog.apply_edit();self.assertEqual(dialog.items[('KOBE',JAN)]['quantity'],80)
            source.write_bytes(fixture('SUGITO'))
            with self.assertRaises(DataError):dialog.load('KOBE',source)
            self.assertNotIn('KOBE',dialog.reports);dialog.destroy()
        finally:app.service.close();root.destroy()
    def test_ui_explicit_save_and_cancel_with_simple_time_fields(self):
        from bunsen.ui import App
        from bunsen.pdf_reader_ui import PDFInventoryDialog
        root=tk.Tk();root.withdraw();app=App(root,self.path/'home')
        try:
            app.service.clock=lambda:NOW;app.operator.set('担当')
            app.service.save_jan_master(MASTER,'担当','架空確認',True,0,True)
            dialog=PDFInventoryDialog(app)
            for warehouse in ('KOBE','SUGITO'):
                source=self.path/(warehouse+'.pdf');source.write_bytes(fixture(warehouse));dialog.load(warehouse,source)
                self.assertEqual(dialog.meta[warehouse]['as_of'].get(),'18:00:01')
                self.assertNotIn('basis',dialog.meta[warehouse])
                dialog.meta[warehouse]['as_of'].set('18:00')
            with patch('bunsen.pdf_reader_ui.filedialog.askdirectory',return_value=''):dialog.save()
            self.assertEqual(app.service.db.execute("SELECT count(*) FROM events WHERE kind='pdf_inventory_review'").fetchone()[0],0)
            target=self.path/'outputs';target.mkdir()
            with patch('bunsen.pdf_reader_ui.filedialog.askdirectory',return_value=str(target)):dialog.save()
            self.assertEqual(app.service.db.execute("SELECT count(*) FROM events WHERE kind='pdf_inventory_review'").fetchone()[0],1)
            self.assertIn('2026-11-20T18:00:00+09:00',(target/'inventory.csv').read_text(encoding='utf-8-sig'))
        finally:app.service.close();root.destroy()
