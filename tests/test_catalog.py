import json
from pathlib import Path
import tempfile
import tkinter as tk
import unittest
from unittest.mock import patch

from bunsen.core import DataError, load
from bunsen.service import Service
from bunsen.backup import create_backup, restore_backup, validate_database
from bunsen.ui import App
from test_workflow import ROOT, NOW, DAY


PRODUCT=dict(product='P001',name='架空画面登録そうめん',p_per_ball=12,balls_per_case=2,cases_per_pl=10,
             ship_unit='PL',expiry_type='best_before',shelf_life_days=10)


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.folder=self.root/'csv';self.folder.mkdir()
        for p in (ROOT/'samples/delivery-plan').glob('*.csv'):(self.folder/p.name).write_bytes(p.read_bytes())
        self.service=Service(self.root/'home/history.db',lambda:NOW)

    def tearDown(self):self.service.close();self.temp.cleanup()

    def save(self,values=None,create=True,revision=None):
        return self.service.save_product_master(values or PRODUCT,'架空の確認済み資料','架空担当者',True,
                                                expected_revision=self.service.product_master()['revision'] if revision is None else revision,create=create)

    def imported(self):return self.service.import_folder(self.folder,workflow=2,use_registered_products=True)

    def test_standalone_registration_and_reopen_does_not_require_csv_or_inventory(self):
        state=self.save();self.assertEqual(state['products'][0]['name'],PRODUCT['name']);self.assertGreater(state['revision'],0)
        self.service.close();self.service=Service(self.root/'home/history.db',lambda:NOW)
        self.assertEqual(self.service.product_master(),state)
        self.assertFalse(any(self.service.db.execute('SELECT * FROM issues')))

    def test_no_defaults_missing_zero_negative_bool_or_unknown_units(self):
        for field,value in [('name',''),('product',''),('product','A|B'),('p_per_ball',0),('balls_per_case',-1),('cases_per_pl',True),('shelf_life_days','10'),('expiry_type',''),('ship_unit','BOX')]:
            with self.subTest(field=field,value=value),self.assertRaises(DataError):self.save(dict(PRODUCT,**{field:value}))
        self.assertEqual(self.service.product_master()['revision'],0)

    def test_operator_reason_and_explicit_bool_confirmation_are_required(self):
        for operator,reason,ack in [('', '確認',True),('担当','',True),('担当','確認',False),('担当','確認',1)]:
            with self.assertRaises(DataError):self.service.save_product_master(PRODUCT,reason,operator,ack,expected_revision=0,create=True)
        self.assertEqual(self.service.product_master()['products'],[])

    def test_duplicate_add_and_missing_edit_are_rejected(self):
        self.save()
        with self.assertRaises(DataError):self.save()
        with self.assertRaises(DataError):self.save(dict(PRODUCT,product='NEW'),create=False)
        self.assertEqual(len(self.service.product_master()['products']),1)

    def test_edits_preserve_before_after_reason_operator_and_revisions(self):
        first=self.save();second=self.save(dict(PRODUCT,name='架空改訂品',cases_per_pl=8),create=False)
        events=[json.loads(r[0]) for r in self.service.db.execute("SELECT payload FROM events WHERE kind='product_master_change' ORDER BY id")]
        self.assertEqual(events[-1]['before'],first['products'][0]);self.assertEqual(events[-1]['after'],second['products'][0])
        self.assertEqual(events[-1]['operator'],'架空担当者');self.assertEqual(events[-1]['base_revision'],first['revision'])
        self.assertGreater(second['revision'],first['revision']);validate_database(self.service.db)

    def test_concurrent_old_revision_cannot_overwrite(self):
        state=self.save();other=Service(self.root/'home/history.db',lambda:NOW)
        try:
            other.save_product_master(dict(PRODUCT,name='別画面の確認値'),'確認','別担当',True,expected_revision=state['revision'],create=False)
            with self.assertRaises(DataError):self.save(dict(PRODUCT,name='古い画面'),create=False,revision=state['revision'])
            self.assertEqual(self.service.product_master()['products'][0]['name'],'別画面の確認値')
        finally:other.close()

    def test_registered_mode_accepts_four_csv_without_products_and_preserves_physical_files(self):
        self.save();(self.folder/'products.csv').unlink();before={p.name:p.read_bytes() for p in self.folder.iterdir()}
        data=self.imported();self.assertEqual(data.tables['products'][0]['name'],PRODUCT['name'])
        self.assertEqual(data.product_master_revision,self.service.product_master()['revision'])
        lines=self.service.calculate(data,DAY);payload=self.service.issue(data,lines,DAY,'担当',True,True)
        self.assertEqual(payload['product_master_revision'],data.product_master_revision)
        self.assertIn(PRODUCT['name'],payload['raw']['products.csv'])
        self.assertEqual({p.name:p.read_bytes() for p in self.folder.iterdir()},before)

    def test_registered_mode_ignores_bad_physical_product_csv_but_csv_mode_does_not(self):
        self.save();(self.folder/'products.csv').write_bytes(b'bad\xff')
        self.assertEqual(self.imported().tables['products'][0]['confirmed'],'yes')
        with self.assertRaises(DataError):self.service.import_folder(self.folder,workflow=2)

    def test_empty_registry_is_not_zero_products_or_fallback_csv(self):
        with self.assertRaises(DataError):self.imported()
        self.assertFalse(any(self.service.db.execute("SELECT 1 FROM events WHERE kind='import'")))

    def test_new_product_missing_warehouse_data_blocks_import_with_context(self):
        self.save();self.save(dict(PRODUCT,product='P002',name='架空新商品'))
        with self.assertRaises(DataError) as caught:self.imported()
        self.assertEqual({i.warehouse for i in caught.exception.issues if i.product=='P002'},{'KOBE','SUGITO'})
        self.assertEqual({i.filename for i in caught.exception.issues if i.product=='P002'},{'inventory.csv','replenishment.csv'})
        self.assertFalse(any(self.service.db.execute("SELECT 1 FROM events WHERE kind='import'")))

    def test_registered_mode_still_detects_unknown_csv_and_changing_inventory(self):
        self.save();(self.folder/'unknown.csv').write_text('bad')
        with self.assertRaises(DataError):self.imported()
        (self.folder/'unknown.csv').unlink();original=Path.read_bytes
        def read(path):
            binary=original(path)
            if path.name=='forecast.csv':
                inv=self.folder/'inventory.csv';inv.write_bytes(original(inv).replace(b',120,',b',121,'))
            return binary
        with patch.object(Path,'read_bytes',read),self.assertRaises(DataError) as caught:self.imported()
        self.assertTrue(any(i.filename=='inventory.csv' and 'CHANGED' in i.code for i in caught.exception.issues))

    def test_stale_registered_calculation_and_issue_are_blocked(self):
        self.save();data=self.imported();lines=self.service.calculate(data,DAY)
        self.save(dict(PRODUCT,p_per_ball=24),create=False)
        with self.assertRaises(DataError):self.service.calculate(data,DAY)
        with self.assertRaises(DataError):self.service.issue(data,lines,DAY,'担当',True,True)
        fresh=self.imported();self.assertEqual(self.service.calculate(fresh,DAY)[0]['suggested_p'],144)

    def test_pending_plan_cannot_confirm_after_master_edit_but_old_issue_keeps_original(self):
        self.save();data=self.imported();lines=self.service.calculate(data,DAY)
        final=self.service.issue(data,lines,DAY,'担当',True,True)
        plan=self.service.calculate_delivery_plan(data,DAY)
        self.save(dict(PRODUCT,name='架空の更新品'),create=False)
        with self.assertRaises(DataError):self.service.confirm_delivery_plan(plan['plan_id'],'担当',True)
        old=self.service.report_payload(final['number']);self.assertEqual(old['tables']['products'][0]['name'],PRODUCT['name'])
        self.assertEqual(old['product_master_revision'],data.product_master_revision)

    def test_csv_mode_remains_independent_of_registry(self):
        self.save();data=self.service.import_folder(self.folder,workflow=2)
        self.save(dict(PRODUCT,name='別マスタ'),create=False)
        self.assertIsNone(data.product_master_revision)
        self.assertEqual(self.service.calculate(data,DAY)[0]['suggested_p'],120)
        self.assertEqual(data.tables['products'][0]['name'],'架空そうめん')

    def test_settings_and_existing_expiry_editor_preserve_registered_revision(self):
        self.save();data=self.imported();values={k:data.tables['replenishment'][0][k] for k in ('cycle_days','lead_days','safety_days','coverage_days')}
        fresh=self.service.update_settings(data,('KOBE','P001'),values,'確認','担当')
        self.assertEqual(fresh.product_master_revision,data.product_master_revision)
        fresh=self.service.update_product_expiry(fresh,'P001','use_by',8,'期限確認','担当')
        self.assertGreater(fresh.product_master_revision,data.product_master_revision)
        self.assertEqual(self.service.product_master()['products'][0]['shelf_life_days'],8)
        self.service.calculate(fresh,DAY);validate_database(self.service.db)

    def test_catalog_csv_export_is_new_file_only_and_contains_all_fields(self):
        self.save();path=self.root/'products.csv';self.service.export_product_master(path)
        content=path.read_text(encoding='utf-8-sig');self.assertIn('expiry_type,shelf_life_days,confirmed',content)
        with self.assertRaises(FileExistsError):self.service.export_product_master(path)
        self.assertEqual(path.read_text(encoding='utf-8-sig'),content)

    def test_backup_restore_preserves_master_and_blocks_all_writes(self):
        state=self.save();archive=self.root/'backup.zip'
        manifest=create_backup(self.service.db,self.root/'home',archive,lambda:NOW)
        home=restore_backup(archive,self.root/'restored',manifest['archive_sha256'],lambda:NOW)
        viewer=Service(home/'history.db',lambda:NOW)
        try:
            self.assertEqual(viewer.product_master(),state)
            with self.assertRaises(DataError):viewer.save_product_master(PRODUCT,'確認','担当',True,expected_revision=state['revision'],create=False)
            viewer.export_product_master(self.root/'restored-products.csv')
        finally:viewer.close()

    def test_backup_validator_detects_corrupt_master_chain(self):
        self.save();row=self.service.db.execute("SELECT payload FROM events WHERE kind='product_master_change'").fetchone()
        payload=json.loads(row[0]);payload['base_revision']=999
        with self.service.db:self.service.db.execute("UPDATE events SET payload=? WHERE kind='product_master_change'",(json.dumps(payload),))
        with self.assertRaises(DataError):validate_database(self.service.db)

    def test_ui_register_without_csv_and_edit_invalidates_working_confirmation(self):
        root=tk.Tk();root.withdraw();app=App(root,self.root/'ui')
        try:
            app.service.clock=lambda:NOW
            for key,var in app.master_values.items():var.set({'ship_unit':'PL','expiry_type':'賞味期限'}.get(key,str(PRODUCT[key])))
            app.master_operator.set('架空担当');app.master_reason.set('確認');app.master_ack.set(True);app.save_master()
            self.assertEqual(len(app.master_tree.get_children()),1);self.assertFalse(app.master_ack.get());self.assertIsNone(app.data)
            app.use_registered_master.set(True)
            with patch('bunsen.ui.filedialog.askdirectory',return_value=str(self.folder)):app.import_data()
            app.day.set(str(DAY));app.compute();app.ack.set(True)
            app.master_tree.selection_set('0');app.select_master();app.master_values['name'].set('架空改訂商品')
            app.master_reason.set('改訂確認');app.master_ack.set(True);app.save_master()
            self.assertIsNone(app.data);self.assertIsNone(app.lines);self.assertFalse(app.ack.get())
            self.assertEqual(app.service.product_master()['products'][0]['name'],'架空改訂商品')
        finally:app.close()

    def test_ui_source_copy_is_unconfirmed_and_code_cannot_be_renamed(self):
        root=tk.Tk();root.withdraw();app=App(root,self.root/'ui')
        try:
            app.service.clock=lambda:NOW;app.data=load(self.folder,NOW);app.display_settings();app.master_source.set('P001');app.copy_imported_master()
            self.assertFalse(app.master_ack.get());self.assertEqual(app.service.product_master()['products'],[])
            app.master_operator.set('架空担当');app.master_reason.set('確認');app.master_ack.set(True);app.save_master()
            app.master_tree.selection_set('0');app.select_master();self.assertEqual(str(app.master_code_entry.cget('state')),'readonly')
            app.master_values['product'].set('RENAMED');app.master_reason.set('変更');app.master_ack.set(True)
            with self.assertRaises(DataError):app.save_master()
            self.assertEqual(app.service.product_master()['products'][0]['product'],'P001')
        finally:app.close()

    def test_master_change_during_issue_is_rechecked_inside_write_transaction(self):
        self.save();data=self.imported();lines=self.service.calculate(data,DAY)
        original=self.service.snapshot_data
        other=Service(self.root/'home/history.db',lambda:NOW)
        def changed(*args,**kwargs):
            fresh=original(*args,**kwargs)
            state=other.product_master()
            other.save_product_master(dict(PRODUCT,name='確定直前の改訂'),'確認','別担当',True,expected_revision=state['revision'],create=False)
            return fresh
        try:
            with patch.object(self.service,'snapshot_data',changed),self.assertRaises(DataError):self.service.issue(data,lines,DAY,'担当',True,True)
            self.assertEqual(self.service.history(),[])
        finally:other.close()

    def test_registered_master_diagnostic_does_not_require_physical_products_csv(self):
        from bunsen.catalog import catalog_csv
        from bunsen.diagnostics import diagnose,report_html
        state=self.save();(self.folder/'products.csv').unlink()
        report=diagnose(self.folder,DAY,NOW,workflow=2,product_csv=catalog_csv(state['products']),product_master_revision=state['revision'])
        self.assertTrue(report['calculation_possible']);self.assertFalse(report['formal_issue_allowed'])
        self.assertIn(f'登録商品マスタ版{state["revision"]}',report_html(report))

    def test_registered_draft_resume_preserves_revision_and_blocks_stale_master(self):
        self.save();data=self.imported();lines=self.service.calculate(data,DAY)
        draft=self.service.issue(data,lines,DAY,'担当',False)
        resumed,_,_=self.service.resume_draft(draft['number']);self.assertEqual(resumed.product_master_revision,data.product_master_revision)
        self.save(dict(PRODUCT,name='更新品'),create=False)
        with self.assertRaises(DataError):self.service.resume_draft(draft['number'])


if __name__=='__main__':unittest.main()
