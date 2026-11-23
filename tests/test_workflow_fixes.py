import csv,io,json,tempfile,tkinter as tk,unittest
from pathlib import Path
from datetime import datetime,date,time
from unittest.mock import patch
from bunsen.core import DataError,JST
from bunsen.forecast_demo import MASTER,JAN
from bunsen.jan_csv import analyse,read_csv,export_csv,HEADERS
from bunsen.jan_catalog import validate_jan
from bunsen.service import Service
from bunsen.backup import validate_database
from bunsen.ui import App

class WorkflowFixTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)
        self.service=Service(self.path/'history.db',lambda:datetime(2026,11,9,20,tzinfo=JST))
    def tearDown(self):self.service.close();self.temp.cleanup()
    def report(self,products=None):return analyse(export_csv(products or [dict(MASTER,name='ＬＬ惣菜ＡＢＣ１２３')]).encode('utf-8-sig'))
    def save(self,report=None,revision=None):return self.service.import_jan_csv(report or self.report(),'架空担当','CSV確認',True,self.service.jan_master()['revision'] if revision is None else revision)
    def test_full_width_name_is_preserved_in_validator_and_csv(self):
        name='ＬＬ ＡＢＣ１２３（全角）'
        self.assertEqual(validate_jan(dict(MASTER,name=name))['name'],name)
        report=analyse(export_csv([dict(MASTER,name=name)]).encode('cp932'))
        self.assertFalse(report['errors']);self.save(report)
        self.assertEqual(self.service.jan_master()['products'][0]['name'],name)
    def test_atomic_merge_retains_other_products_and_records_original(self):
        a=self.save();self.service.save_jan_master(dict(MASTER,product='4900000000002'),'担当','確認',True,a['revision'],True)
        self.save(self.report([dict(MASTER,name='更新ＬＬ')]))
        state=self.service.jan_master();self.assertEqual(len(state['products']),2)
        payload=json.loads(self.service.db.execute("SELECT payload FROM events ORDER BY id DESC LIMIT 1").fetchone()[0])
        self.assertTrue(payload['csv_report']['source_base64']);self.assertEqual(len(payload['changes']),1)
        validate_database(self.service.db)
        payload['changes'][0]['before']=None
        self.service.db.execute('UPDATE events SET payload=? WHERE id=?',(json.dumps(payload),state['revision']));self.service.db.commit()
        with self.assertRaises(DataError):validate_database(self.service.db)
    def test_invalid_row_prevents_all_writes(self):
        text=export_csv([MASTER,dict(MASTER,product='4900000000002')]).replace(',480',',0',1)
        report=analyse(text.encode());self.assertTrue(report['errors'])
        with self.assertRaises(DataError):self.save(report)
        self.assertEqual(self.service.jan_master()['revision'],0)
    def test_duplicate_jan_blocks_whole_import(self):
        r=analyse(export_csv([MASTER,MASTER]).encode());self.assertTrue(r['errors'])
        with self.assertRaises(DataError):self.save(r)
    def test_missing_column_and_empty_file_do_not_register_defaults(self):
        with self.assertRaises(DataError):analyse(b'JAN,name\n')
        with self.assertRaises(DataError):self.save(analyse(export_csv([]).encode()))
    def test_expected_revision_source_change_and_ack_are_checked(self):
        r=self.report();self.save(r)
        with self.assertRaises(DataError):self.save(r,revision=0)
        p=self.path/'master.csv';p.write_text(export_csv([MASTER]),encoding='utf-8-sig');r=read_csv(p);p.write_text(export_csv([dict(MASTER,name='変更')]),encoding='utf-8-sig')
        with self.assertRaises(DataError):self.save(r)
        with self.assertRaises(DataError):self.service.import_jan_csv(self.report(),'担当','確認',False,self.service.jan_master()['revision'])
    def test_read_only_and_tampered_preview_block(self):
        r=self.report();r['products'][0]['name']='tamper'
        with self.assertRaises(DataError):self.save(r)
        self.service.read_only=True
        with self.assertRaises(DataError):self.save()
    def test_forecast_can_be_saved_after_master_changes(self):
        from bunsen.forecast_csv import analyse_bytes
        from bunsen.forecast_demo import fictional_source,MAPPING
        report=analyse_bytes(fictional_source(date(2026,11,9)))
        self.save()
        self.service.save_forecast_csv(report,MAPPING,'担当',True,0,0,self.path)
        self.assertTrue((self.path/'forecast.csv').exists())
    def test_internal_columns_and_roundtrip_export(self):
        from bunsen.jan_catalog import FIELDS
        stream=io.StringIO();writer=csv.DictWriter(stream,fieldnames=FIELDS);writer.writeheader();writer.writerow(MASTER)
        report=analyse(stream.getvalue().encode());self.assertFalse(report['errors'])
        self.assertEqual(analyse(export_csv(report['products']).encode())['products'],[MASTER])
    def test_menu_and_manual_full_width_registration_and_csv_dialog(self):
        from bunsen.jan_csv_ui import JanCSVDialog
        root=tk.Tk();root.withdraw();app=App(root,self.path/'ui')
        try:
            menu=root.nametowidget(root['menu']);admin=root.nametowidget(menu.entrycget(1,'menu'))
            labels=[admin.entrycget(i,'label') for i in range(admin.index('end')+1) if admin.type(i) not in ('separator','tearoff')]
            self.assertIn('補充条件',labels);self.assertNotIn('旧v2商品マスタ',labels)
            app.open_admin(app.management_page);self.assertEqual(app.tabs.select(),app.management_page)
            app.jan_operator.set('担当');app.jan_reason.set('全角品名を画面登録')
            for k,v in dict(MASTER,name='ＬＬ惣菜ＡＢＣ１２３',expiry_type='賞味期限').items():app.jan_values[k].set(str(v))
            app.confirmed_action(app.jan_ack,app.save_jan)
            self.assertEqual(app.service.jan_master()['products'][0]['name'],'ＬＬ惣菜ＡＢＣ１２３')
            p=self.path/'ui.csv';p.write_text(export_csv([dict(MASTER,name='ＣＳＶ全角')]),encoding='utf-8-sig')
            dialog=JanCSVDialog(app);dialog.load(p);self.assertEqual(str(dialog.save_button['state']),'normal')
            dialog.reason.set('一括更新');dialog.save()
            self.assertEqual(app.service.jan_master()['products'][0]['name'],'ＣＳＶ全角');self.assertIsNone(app.data)
        finally:app.service.close();root.destroy()
