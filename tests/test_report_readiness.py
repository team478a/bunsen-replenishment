from datetime import date, datetime, timedelta
import io
import json
from pathlib import Path
import tempfile
import unittest

from openpyxl import load_workbook
from pypdf import PdfReader

from bunsen.acceptance import V2_CASES, create_kit, prepare_trial_home
from bunsen.core import DataError, JST, load
from bunsen.delivery_output import export_plan, plan_pdf
from bunsen.service import Service
from bunsen.shipping_output import xlsx_bytes, pdf_bytes, write_documents
from test_workflow import ROOT

DAY=date(2026,11,10)
NOW=datetime(2026,11,10,9,tzinfo=JST)


class ReportReadinessTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.kit=self.root/'架空 試験'
        self.manifest=create_kit(self.kit,DAY,NOW,workflow=2)
        self.home,_=prepare_trial_home(self.kit)
        self.service=Service(self.home/'history.db',lambda:NOW)

    def tearDown(self):
        self.service.close();self.temp.cleanup()

    def data(self):return load(self.kit/'samples/report-many-products',NOW)

    def payload(self,reason='架空の長い確認理由・印刷試験専用。'*10):
        data=self.data();lines=self.service.calculate(data,DAY)
        self.service.edit_quantity(data,lines,('KOBE','M001'),480,reason,'架空担当者')
        final=self.service.issue(data,lines,DAY,'架空担当者',True,True)
        return self.service.report_payload(final['number'])

    def test_new_kit_covers_delivery_and_many_products_with_expected_results(self):
        self.assertEqual(len(self.manifest['files']),20)
        self.assertTrue((self.kit/'samples/delivery-plan/products.csv').is_file())
        self.assertEqual(len(self.data().tables['products']),8)
        record=json.loads((self.kit/'acceptance-record.json').read_text(encoding='utf-8'))
        self.assertEqual(len(record['cases']),len(V2_CASES))
        self.assertTrue(all(c['expected'] and c['status']=='not_performed' and c['actual'] is None and c['evidence'] is None for c in record['cases']))
        self.assertEqual(record['approval_status'],'unconfirmed')
        self.assertIn('実CSV',(self.kit/'OPERATIONAL-READINESS.md').read_text(encoding='utf-8'))

    def test_new_scenarios_shift_dates_preserve_expiry_days_and_master_units(self):
        data=self.data();product=data.tables['products'][0]
        self.assertEqual(product['shelf_life_days'],10)
        self.assertEqual([p['cases_per_pl'] for p in data.tables['products']],[8,10,12,8,10,12,8,10])
        self.assertEqual({r['as_of'] for r in data.tables['inventory']},{'2026-11-10T07:00:00+09:00'})
        source=load(ROOT/'samples/report-many-products',NOW)
        self.assertEqual(data.tables['products'],source.tables['products'])
        self.assertEqual({r['arrival_date'] for r in data.tables['transit']},{'2026-11-12'})

    def test_many_product_expected_quantities_and_capacity_are_independently_fixed(self):
        data=self.data();lines=self.service.calculate(data,DAY)
        self.assertEqual(sum(r['quantity_p'] for r in lines if r['warehouse']=='KOBE'),3456)
        self.assertEqual(sum(r['quantity_p'] for r in lines if r['warehouse']=='SUGITO'),1728)
        report=self.service.calculate_delivery_plan(data,DAY)
        first=[t for t in report['trips'] if t['dispatch']==str(DAY) and t['warehouse']=='KOBE']
        self.assertEqual([t['pallets'] for t in first],[16,3])
        self.assertEqual(sum(i['quantity_p'] for t in first for i in t['items']),3456)
        self.assertEqual({r['estimated_expiry'] for r in report['shipments']},{str(DAY+timedelta(days=10))})

    def test_tampered_new_scenario_prevents_trial_home_creation(self):
        # Use another unopened kit to verify failure occurs before database/home creation.
        kit=self.root/'unopened';create_kit(kit,DAY,NOW,workflow=2)
        path=kit/'samples/report-many-products/products.csv'
        path.write_bytes(path.read_bytes().replace(b',10,yes',b',11,yes'))
        with self.assertRaises(DataError):prepare_trial_home(kit)
        self.assertFalse((kit/'trial-data').exists())

    def test_operations_do_not_mark_human_acceptance_as_passed(self):
        before=(self.kit/'acceptance-record.json').read_bytes()
        data=self.data();report=self.service.calculate_delivery_plan(data,DAY)
        self.service.confirm_delivery_plan(report['plan_id'],'架空担当者',True)
        self.assertEqual((self.kit/'acceptance-record.json').read_bytes(),before)

    def test_excel_wrap_height_full_names_and_reasons_and_wide_warning_rows(self):
        payload=self.payload();workbook=load_workbook(io.BytesIO(xlsx_bytes(payload,'KOBE')))
        try:
            sheet=workbook.active
            self.assertEqual(sheet['B7'].value,payload['tables']['products'][0]['name'])
            self.assertEqual(sheet['L7'].value,payload['lines'][0]['edit_reason'])
            self.assertGreater(sheet.row_dimensions[7].height,100)
            self.assertTrue(sheet['B7'].alignment.wrap_text)
            self.assertEqual(sheet['I7'].value,480)
            self.assertEqual(sheet.print_title_rows,'$1:$6')
            self.assertEqual(sheet.page_setup.fitToHeight,0)
            self.assertIn('A2:M2',{str(r) for r in sheet.merged_cells})
        finally:workbook.close()

    def test_excel_oversized_print_row_fails_without_silently_truncating_or_partial_files(self):
        payload=self.payload('架空理由'*1000);folder=self.root/'oversized'
        with self.assertRaises(DataError) as caught:write_documents(payload,folder)
        self.assertIn('L7',str(caught.exception));self.assertIn('行高上限',str(caught.exception))
        self.assertFalse(folder.exists())

    def test_shipping_pdf_splits_very_long_reason_and_keeps_tail_and_page_notices(self):
        reason='架空の非常に長い確認理由。'*220+'確認理由の終端'
        payload=self.payload(reason);reader=PdfReader(io.BytesIO(pdf_bytes(payload,'KOBE')))
        self.assertGreater(len(reader.pages),2)
        texts=[p.extract_text() for p in reader.pages]
        self.assertIn('確認理由の終端',''.join(''.join(texts).split()))
        self.assertTrue(all('架空試用' in t for t in texts))
        self.assertGreaterEqual(sum('商品' in t and '修正理由' in t for t in texts),2)
        for i in range(1,9):self.assertIn(f'M{i:03}',''.join(texts))

    def test_multi_page_plan_pdf_keeps_all_items_headers_and_identification(self):
        report=self.service.calculate_delivery_plan(self.data(),DAY)
        report=self.service.confirm_delivery_plan(report['plan_id'],'架空担当者',True)
        reader=PdfReader(io.BytesIO(plan_pdf(report,'KOBE')));texts=[p.extract_text() for p in reader.pages]
        self.assertGreater(len(texts),3)
        self.assertTrue(all('架空試用' in t and 'KOBE' in t and report['plan_id'] in t for t in texts))
        combined=''.join(texts)
        for trip in report['trips']:
            if trip['warehouse']=='KOBE':self.assertIn(trip['trip_id'],combined)
        for i in range(1,9):self.assertIn(f'M{i:03}',combined)
        self.assertGreaterEqual(sum('概算期限' in t and '数量換算' in t for t in texts),2)

    def test_multi_product_plan_exports_complete_workbook_and_does_not_overwrite(self):
        report=self.service.calculate_delivery_plan(self.data(),DAY);paths=export_plan(report,self.root/'plan')
        workbook=load_workbook(next(p for p in paths if p.name.endswith('KOBE.xlsx')),read_only=True)
        try:
            values=list(workbook.active.values)
            actual=[r for r in values if r and isinstance(r[0],str) and r[0].startswith('T')]
            expected=[i for t in report['trips'] if t['warehouse']=='KOBE' for i in t['items']]
            self.assertEqual(len(actual),len(expected))
            self.assertEqual(sum(r[5] for r in actual),sum(r['quantity_p'] for r in expected))
            self.assertEqual({r[3] for r in actual},{f'M{i:03}' for i in range(1,9)})
        finally:workbook.close()
        with self.assertRaises(DataError):export_plan(report,self.root/'plan')
        self.assertEqual(len(list((self.root/'plan').iterdir())),7)


if __name__=='__main__':unittest.main()
