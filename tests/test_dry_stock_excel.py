import copy,io,json,tempfile,unittest,zipfile,tkinter as tk
from pathlib import Path
from datetime import date,datetime
from bunsen.core import DataError,JST
from bunsen.dry_stock_excel import analyse_excel,FORMAT
from bunsen.dry_stock_excel_demo import fictional_excel,main as demo
from bunsen.dry_stock_intake import read_stock,checked_report,compare
from bunsen.service import Service
from bunsen.backup import validate_database

JAN='4900000000001';OTHER='4900000000002';NOW=datetime(2026,11,9,9,tzinfo=JST)
def line(code='DEMO-A',jan=JAN,q=240,status='入力済み'):
    return [code,'架空商品','75g',jan,80,20,80,q,None,status,'08:00']
def replace_part(content,name,fn):
    out=io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(content)) as source,zipfile.ZipFile(out,'w') as target:
        for item in source.infolist():target.writestr(item.filename,fn(source.read(item.filename)) if item.filename==name else source.read(item.filename))
    return out.getvalue()

class DryStockExcelTests(unittest.TestCase):
    def setUp(self):self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name);self.s=Service(self.path/'home/history.db',lambda:NOW)
    def tearDown(self):self.s.close();self.temp.cleanup()
    def save(self,content=None):return self.s.save_dry_stock(analyse_excel(content or fictional_excel()),'2026-11-09T08:10:00+09:00','架空担当',True,0)
    def test_available_packs_sum_by_jan_and_keep_source_product_codes(self):
        r=analyse_excel(fictional_excel());self.assertFalse(r['errors']);self.assertEqual(len(r['rows']),2)
        row=r['rows'][0];self.assertEqual(row['available_p'],720);self.assertEqual(row['source_rows'],[2,3])
        self.assertIsNone(row['stock_p']);self.assertIsNone(row['reserved_p'])
        self.assertEqual([x['product_code'] for x in r['details']],['DEMO-A','DEMO-B','DEMO-C'])
        self.assertEqual(r['quantity_basis'],'available_p');self.assertEqual(r['source_format'],FORMAT)
    def test_partial_jan_is_not_a_complete_total_and_blank_is_not_zero(self):
        r=analyse_excel(fictional_excel([line(),line('DEMO-B',q=None,status='未入力')]))
        self.assertFalse(r['errors']);self.assertEqual(r['rows'][0]['entered_p'],240);self.assertIsNone(r['rows'][0]['available_p'])
        zero=analyse_excel(fictional_excel([line(q=0)]));self.assertEqual(zero['rows'][0]['available_p'],0)
    def test_invalid_rows_stop_save_without_silent_repairs(self):
        cases=[[line(q=-1)],[line(q=1.5)],[line(q=None)],[line(status='未入力')],[line(jan='123')],[line(),line(q=1)]]
        for rows in cases:
            report=analyse_excel(fictional_excel(rows));self.assertTrue(report['errors'])
            with self.assertRaises(DataError):self.s.save_dry_stock(report,NOW.isoformat(),'担当',True,0)
        self.assertIsNone(self.s.dry_stock())
    def test_formula_and_changed_headers_are_not_trusted(self):
        for transform in [lambda b:b.replace('実在庫数'.encode(),'数量'.encode()),lambda b:b.replace(b'<v>240</v>',b'<f>1+239</f><v>240</v>')]:
            content=replace_part(fictional_excel(),'xl/worksheets/sheet1.xml',transform)
            with self.assertRaises(DataError):analyse_excel(content)
    def test_bad_container_external_sheet_and_oversized_zip_are_rejected(self):
        with self.assertRaises(DataError):analyse_excel(b'not excel')
        content=replace_part(fictional_excel(),'xl/_rels/workbook.xml.rels',lambda b:b.replace(b'Target=',b'TargetMode="External" Target='))
        with self.assertRaises(DataError):analyse_excel(content)
        out=io.BytesIO()
        with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as z:z.writestr('too-large.xml',b' '* (41*1024*1024))
        with self.assertRaises(DataError):analyse_excel(out.getvalue())
    def test_source_and_aggregate_tampering_and_original_changes_stop_save(self):
        for field in ('available_p','stock_p'):
            report=analyse_excel(fictional_excel());report['rows'][0][field]=999
            with self.assertRaises(DataError):self.s.save_dry_stock(report,NOW.isoformat(),'担当',True,0)
        report=analyse_excel(fictional_excel());report['quantity_basis']='physical'
        with self.assertRaises(DataError):checked_report(report)
        path=self.path/'source.xlsx';path.write_bytes(fictional_excel());report=read_stock(path);path.write_bytes(fictional_excel([line(q=1)]))
        with self.assertRaises(DataError):self.s.save_dry_stock(report,NOW.isoformat(),'担当',True,0)
    def test_no_double_reservation_subtraction_and_joint_warehouse_comparison(self):
        stock=self.save();planned=[dict(warehouse='KOBE',product=JAN,required_p=100,quantity_p=240),dict(warehouse='SUGITO',product=JAN,required_p=100,quantity_p=480)]
        row=compare(stock,planned,'2026-11-09',NOW.isoformat())[0]
        self.assertEqual((row['usable_p'],row['quantity_p'],row['shortage_p']),(720,720,0));self.assertIsNone(row['stock_p']);self.assertIsNone(row['reserved_p'])
        self.s.save_dry_stock_comparison(dict(data_digest='0'*64,dispatch='2026-11-09',planned=planned,stock_revision=stock['revision'],at=NOW.isoformat(),rows=[row]),'担当');validate_database(self.s.db)
        saved=json.loads(self.s.db.execute("SELECT payload FROM events WHERE kind='dry_stock_import'").fetchone()[0]);self.assertEqual(len(saved['report']['details']),3)
    def test_pending_missing_zero_and_wrong_date_are_distinct(self):
        stock=self.save(fictional_excel([line(q=0),line('DEMO-B',jan=OTHER,q=None,status='未入力')]))
        planned=[dict(warehouse='KOBE',product=JAN,required_p=1,quantity_p=240)]
        self.assertEqual(compare(stock,planned,'2026-11-09',NOW.isoformat())[0]['shortage_p'],240)
        planned[0]['product']=OTHER;row=compare(stock,planned,'2026-11-09',NOW.isoformat())[0]
        self.assertIsNone(row['shortage_p']);self.assertIn('未確認',row['status'])
        planned[0]['product']='4900000000003';self.assertIn('未掲載',compare(stock,planned,'2026-11-09',NOW.isoformat())[0]['status'])
        planned[0]['product']=JAN;self.assertIn('基準日違い',compare(stock,planned,'2026-11-10',NOW.isoformat())[0]['status'])
    def test_ui_and_backup_flow_keeps_demand_unchanged(self):demo(self.path/'demo')
    def test_ui_does_not_infer_asof_from_filename_and_shows_product_details(self):
        from bunsen.ui import App
        from bunsen.dry_stock_ui import DryStockDialog
        root=tk.Tk();root.withdraw();app=App(root,self.path/'ui')
        try:
            path=self.path/'zaiko_20261109.xlsx';path.write_bytes(fictional_excel());d=DryStockDialog(app);d.load(path)
            self.assertEqual(d.as_of.get(),'');self.assertEqual(len(d.tree.get_children()),2)
            self.assertIn('未入力を含むJAN 1件',d.info.get());d.show_details();self.assertTrue(any(w.winfo_class()=='Toplevel' for w in d.winfo_children()));d.destroy()
        finally:app.close()
