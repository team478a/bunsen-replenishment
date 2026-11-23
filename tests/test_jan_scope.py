import csv,io,json,tempfile,tkinter as tk,unittest
from pathlib import Path
from datetime import date,datetime
from unittest.mock import patch
from bunsen.core import DataError,JST
from bunsen.service import Service
from bunsen.forecast_demo import MASTER,JAN,MAPPING,fictional_source
from bunsen.forecast_csv import analyse_bytes
from bunsen.jan_csv import analyse,export_csv
from bunsen.jan_demo import create_kit
from bunsen.pdf_demo import fictional_report
from bunsen.backup import validate_database,create_backup,restore_backup
from bunsen.product_scope import FILE,parse,text
from bunsen.ui import App
from bunsen.pdf_reader_ui import PDFInventoryDialog

DAY=date(2026,11,9);NOW=datetime(2026,11,9,20,tzinfo=JST);OTHER='4900000000002'

class JanScopeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name);self.kit=create_kit(self.path/'kit',DAY)
        self.root=tk.Tk();self.root.withdraw();self.app=App(self.root,self.kit/'home');self.s=self.app.service;self.s.clock=lambda:NOW
        self.app.operator.set('架空担当');self.s.save_jan_master(MASTER,'担当','TV架空登録',True,0,True)
        self.s.save_jan_master(dict(MASTER,product=OTHER,name='架空対象外候補'),'担当','非TV架空登録',True,self.s.jan_master()['revision'],True)
    def tearDown(self):self.s.close();self.root.destroy();self.temp.cleanup()
    def scope(self,active=False):return self.s.set_jan_active([OTHER],active,'担当','補充対象の確認',True,self.s.jan_master()['revision'])
    def popup(self,missing=False):
        d=PDFInventoryDialog(self.app)
        for w in ('KOBE','SUGITO'):
            p=self.path/(w+'.pdf');p.write_bytes(fictional_report(w,date='2026/11/08',loose=None))
            d.load(w,p);d.meta[w]['policy'].set('ケース・バラの空欄は0（確認済み）');d.change_policy(w)
        return d
    def test_blank_zero_does_not_make_an_absent_product_zero(self):
        d=self.popup()
        self.assertEqual(d.items[('KOBE',JAN)]['quantity'],70);self.assertIsNone(d.items[('KOBE',OTHER)]['quantity'])
        with self.assertRaisesRegex(DataError,'PDF未掲載'):d.save()
        self.assertEqual(len(self.s.jan_master()['products']),2);d.destroy()
    def test_pdf_can_exclude_non_tv_then_issue_without_fake_zero(self):
        d=self.popup();key=d.keys.index(('KOBE',OTHER));d.tree.selection_set(str(key));d.selected();d.reason.set('非TVで補充対象外')
        d.exclude_selected();self.assertFalse(d.items[('SUGITO',OTHER)]['target'])
        target=self.path/'saved';target.mkdir()
        with patch('bunsen.pdf_reader_ui.filedialog.askdirectory',return_value=str(target)):d.save()
        self.assertNotIn(OTHER,(target/'inventory.csv').read_text(encoding='utf-8-sig'));self.assertTrue((target/FILE).exists())
        # Original forecasts contain both JANs. Only explicit excluded JANs are filtered after source verification.
        r=fictional_source(DAY).decode('utf-8-sig');reader=csv.DictReader(io.StringIO(r));rows=list(reader);stream=io.StringIO();writer=csv.DictWriter(stream,fieldnames=reader.fieldnames,lineterminator='\n');writer.writeheader();writer.writerows(rows+[dict(x,JAN=OTHER) for x in rows])
        self.s.save_forecast_csv(analyse_bytes(stream.getvalue().encode()),MAPPING,'担当',True,0,0,target)
        self.s.save_common_settings(dict(cycle_days=1,lead_days=0,safety_days=1,coverage_days=2),'担当','架空条件',True,0)
        data=self.s.import_folder(target,workflow=2,use_common_settings=True);lines=self.s.calculate(data,DAY)
        self.assertEqual(len(lines),2);self.assertTrue(all(x['product']==JAN for x in lines))
        self.assertTrue(all(x['product']==JAN for x in data.tables['forecast']))
        final=self.s.issue(data,lines,DAY,'担当',True,True);self.assertIn(FILE,final['raw']);validate_database(self.s.db)
        plan=self.s.calculate_delivery_plan(data,DAY)
        self.scope(True)
        with self.assertRaises(DataError):self.s.calculate(data,DAY)
        with self.assertRaises(DataError):self.s.confirm_delivery_plan(plan['plan_id'],'担当',True)
        self.assertEqual(self.s.snapshot_data(final['raw'],final['digest']).tables['products'][0]['product'],JAN)
    def test_target_scope_requires_reason_and_handles_stale_master(self):
        rev=self.s.jan_master()['revision']
        with self.assertRaises(DataError):self.s.set_jan_active([OTHER],False,'担当','',True,rev)
        with self.assertRaises(DataError):self.s.set_jan_active(['4900000000009'],False,'担当','確認',True,rev)
        self.scope()
        with self.assertRaises(DataError):self.s.set_jan_active([OTHER],True,'担当','確認',True,rev)
    def test_inactive_details_remain_editable_and_can_be_reactivated(self):
        self.scope();state=self.s.jan_master();self.assertEqual(len(state['products']),1);self.assertEqual(len(state['all_products']),2)
        self.s.save_jan_master(dict(MASTER,product=OTHER,name='名称修正'),'担当','上書き',True,state['revision'],False)
        self.assertIn(OTHER,self.s.jan_master()['excluded']);self.scope(True)
        self.assertEqual(next(p for p in self.s.jan_master()['products'] if p['product']==OTHER)['name'],'名称修正');validate_database(self.s.db)
    def test_csv_replace_marks_omitted_inactive_merge_retains_and_reactivates(self):
        report=analyse(export_csv([dict(MASTER,name='CSVで上書き')]).encode())
        self.s.import_jan_csv(report,'担当','TV一覧への置換',True,self.s.jan_master()['revision'],mode='replace')
        self.assertIn(OTHER,self.s.jan_master()['excluded']);self.assertEqual(len(self.s.jan_master()['all_products']),2)
        self.s.import_jan_csv(analyse(export_csv([dict(MASTER,product=OTHER)]).encode()),'担当','再対象',True,self.s.jan_master()['revision'])
        self.assertEqual(len(self.s.jan_master()['products']),2);validate_database(self.s.db)
    def test_bad_replace_is_atomic_and_read_only_scope_is_blocked(self):
        revision=self.s.jan_master()['revision'];report=analyse(export_csv([dict(MASTER,kobe_p_per_pl=0)]).encode())
        with self.assertRaises(DataError):self.s.import_jan_csv(report,'担当','置換',True,revision,'replace')
        self.assertEqual(self.s.jan_master()['revision'],revision)
        self.s.read_only=True
        with self.assertRaises(DataError):self.scope()
    def test_replace_preview_lists_removed_products_and_master_ui_keeps_row(self):
        from bunsen.jan_csv_ui import JanCSVDialog
        p=self.path/'targets.csv';p.write_text(export_csv([MASTER]),encoding='utf-8-sig')
        d=JanCSVDialog(self.app);d.load(p);d.mode.set('replace')
        self.assertIn(OTHER,d.errors.get('1.0','end'));d.reason.set('対象一覧の整理');d.save()
        self.assertEqual(self.app.jan_tree.item(OTHER,'values')[-1],'対象外')
        self.app.jan_tree.selection_set(OTHER);self.app.select_jan();self.app.jan_reason.set('再対象');self.app.change_jan_scope(True)
        self.assertNotIn(OTHER,self.s.jan_master()['excluded'])
    def test_backup_retains_scope_and_detects_changed_reason(self):
        self.scope();validate_database(self.s.db)
        m=create_backup(self.s.db,self.app.home,self.path/'backup.zip',self.s.clock);home=restore_backup(self.path/'backup.zip',self.path/'restored',m['archive_sha256'],self.s.clock)
        other=Service(home/'history.db',self.s.clock)
        try:self.assertIn(OTHER,other.jan_master()['excluded'])
        finally:other.close()
        row=self.s.db.execute("SELECT id,payload FROM events WHERE kind='jan_master_change' ORDER BY id DESC LIMIT 1").fetchone();payload=json.loads(row[1]);payload['excluded'][OTHER]['reason']='改変'
        self.s.db.execute('UPDATE events SET payload=? WHERE id=?',(json.dumps(payload),row[0]));self.s.db.commit()
        with self.assertRaises(DataError):validate_database(self.s.db)
