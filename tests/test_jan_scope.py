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
        self.assertEqual(d.items[('KOBE',JAN)]['quantity'],70);self.assertNotIn(('KOBE',OTHER),d.items)
        target=self.path/'saved';target.mkdir()
        with patch('bunsen.pdf_reader_ui.filedialog.askdirectory',return_value=str(target)):d.save()
        self.assertNotIn(OTHER,(target/'inventory.csv').read_text(encoding='utf-8-sig'))
        self.assertEqual(len(self.s.jan_master()['products']),2);self.assertFalse(self.s.jan_master()['excluded'])
    def test_pdf_can_exclude_non_tv_then_issue_without_fake_zero(self):
        self.scope();d=self.popup();self.assertNotIn(('SUGITO',OTHER),d.items)
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

    def test_skip_is_warehouse_local_and_reappearing_pdf_restores_target(self):
        d=self.popup();path=self.path/'sugito-other.pdf';path.write_bytes(fictional_report('SUGITO',jan=OTHER,date='2026/11/08'))
        d.load('SUGITO',path)
        target=self.path/'local';target.mkdir()
        with patch('bunsen.pdf_reader_ui.filedialog.askdirectory',return_value=str(target)):d.save()
        source=fictional_source(DAY).decode('utf-8-sig');reader=csv.DictReader(io.StringIO(source));rows=list(reader);stream=io.StringIO();writer=csv.DictWriter(stream,fieldnames=reader.fieldnames,lineterminator='\n');writer.writeheader();writer.writerows(rows+[dict(r,JAN=OTHER) for r in rows])
        self.s.save_forecast_csv(analyse_bytes(stream.getvalue().encode()),MAPPING,'担当',True,0,0,target)
        self.s.save_common_settings(dict(cycle_days=1,lead_days=0,safety_days=1,coverage_days=2),'担当','架空条件',True,0)
        data=self.s.import_folder(target,workflow=2,use_common_settings=True,use_registered_jan=True)
        lines=self.s.calculate(data,DAY)
        self.assertEqual({(r['warehouse'],r['product']) for r in lines},{('KOBE',JAN),('SUGITO',OTHER)})
        self.assertEqual(len(self.s.jan_master()['products']),2);self.assertFalse(self.s.jan_master()['excluded'])
        final=self.s.issue(data,lines,DAY,'担当',True,True);self.assertIn('pdf-presence.csv',final['raw']);validate_database(self.s.db)
        restored=self.s.snapshot_data(final['raw'],final['digest']);self.assertEqual(len(restored.tables['inventory']),2)
        d=self.popup();self.assertIn(('SUGITO',JAN),d.items);d.destroy()
    def test_skip_proof_cannot_hide_listed_product_or_be_removed(self):
        d=self.popup();target=self.path/'proof';target.mkdir()
        with patch('bunsen.pdf_reader_ui.filedialog.askdirectory',return_value=str(target)):d.save()
        from bunsen.pdf_presence import verify
        from bunsen.requirements_data import csv_text
        proof=target/'pdf-presence.csv';reader=csv.DictReader(io.StringIO(proof.read_text(encoding='utf-8-sig')));rows=list(reader)
        rows[0]['missing_json']=json.dumps([JAN,OTHER])
        with self.assertRaises(DataError):verify(csv_text(','.join(reader.fieldnames),rows),(target/'source_evidence.csv').read_text(encoding='utf-8-sig'))
        # Removing evidence must restore strict validation, never infer zero.
        from bunsen.requirements_data import inspect
        import shutil
        shutil.copyfile(self.kit/'csv/forecast.csv',target/'forecast.csv');shutil.copyfile(self.kit/'csv/requirements.csv',target/'requirements.csv')
        proof.unlink();self.assertTrue(inspect(target,NOW).issues)
    def test_listed_blank_still_stops_without_confirmed_zero_rule(self):
        d=self.popup();d.meta['KOBE']['policy'].set('空欄は未確認');d.change_policy('KOBE')
        with self.assertRaisesRegex(DataError,'確認数量が未確認'):d.save()
        d.destroy()

    def test_warehouse_with_no_target_rows_preserves_both_originals(self):
        self.scope();d=self.popup();path=self.path/'other.pdf';path.write_bytes(fictional_report('SUGITO',jan=OTHER,date='2026/11/08'));d.load('SUGITO',path)
        target=self.path/'empty-warehouse';target.mkdir()
        with patch('bunsen.pdf_reader_ui.filedialog.askdirectory',return_value=str(target)):d.save()
        self.s.save_forecast_csv(analyse_bytes(fictional_source(DAY)),MAPPING,'担当',True,0,0,target)
        self.s.save_common_settings(dict(cycle_days=1,lead_days=0,safety_days=1,coverage_days=2),'担当','架空条件',True,0)
        data=self.s.import_folder(target,workflow=2,use_common_settings=True);lines=self.s.calculate(data,DAY)
        self.assertEqual([(r['warehouse'],r['product']) for r in lines],[('KOBE',JAN)])
        self.s.snapshot_data(data.raw,data.digest)
        from bunsen.pdf_presence import verify
        proof=(target/'pdf-presence.csv').read_text(encoding='utf-8-sig');evidence=(target/'source_evidence.csv').read_text(encoding='utf-8-sig')
        bad=[dict(data.tables['inventory'][0],source_sha256='0'*64)]
        with self.assertRaises(DataError):verify(proof,evidence,bad)
