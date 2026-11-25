import copy,csv,io,json,unittest,tempfile
from pathlib import Path
from zipfile import ZipFile
from xml.etree import ElementTree as ET
from unittest.mock import patch
from bunsen.core import DataError
from bunsen.product_safety import FILE,parse,proof
from bunsen.safety_lists_demo import prepare,JAN,OTHER,DAY,SETTINGS
from bunsen.shipping_output import write_documents,rows,list_payload,report_html
from bunsen.backup import validate_database,create_backup,restore_backup
from bunsen.service import Service

class ProductSafetyListTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name);self.app,self.kit,self.target=prepare(self.path/'kit');self.s=self.app.service
    def tearDown(self):self.app.close();self.temp.cleanup()
    def save(self,product=JAN,days=None,reason='架空の商品別条件'):
        return self.s.save_product_safety(product,days if days is not None else dict(KOBE=3,SUGITO=None),'架空担当',reason,True,self.s.product_safety()['revision'])
    def data(self):return self.s.import_folder(self.target,workflow=2,use_common_settings=True,use_registered_jan=True)
    def issue(self):
        data=self.data();rr=self.s.calculate(data,DAY);return data,rr,self.s.issue(data,rr,DAY,'架空担当',True,True)
    def test_warehouse_specific_override_inherit_zero_and_unused_product(self):
        self.save(days=dict(KOBE=3,SUGITO=0));self.save(OTHER,dict(KOBE=5,SUGITO=6))
        data=self.data();rr=self.s.calculate(data,DAY)
        self.assertEqual([r['settings']['safety_days'] for r in rr],[3,0]);self.assertEqual([r['safety_p'] for r in rr],[180,0])
        self.assertEqual(parse(data.raw[FILE]),self.s.product_safety());self.assertEqual(len(rr),2)
        self.save(days=dict(KOBE=None,SUGITO=None));self.assertEqual([r['settings']['safety_days'] for r in self.s.calculate(self.data(),DAY)],[1,1])
    def test_unset_product_tracks_common_updates_and_zero_stays_override(self):
        self.save(days=dict(KOBE=None,SUGITO=0))
        updated=copy.deepcopy(SETTINGS);updated['KOBE']['safety_days']=4;updated['SUGITO']['safety_days']=5
        self.s.save_common_settings(updated,'担当','架空共通変更',True,self.s.common_settings()['revision'])
        self.assertEqual([r['settings']['safety_days'] for r in self.s.calculate(self.data(),DAY)],[4,0])
    def test_more_safety_days_requires_more_forecast_not_zero_fill(self):
        self.save(days=dict(KOBE=40,SUGITO=None));data=self.data()
        with self.assertRaises(DataError) as exc:self.s.calculate(data,DAY)
        self.assertIn('FORECAST_MISSING',{i.code for i in exc.exception.issues});self.assertFalse(self.s.history())
    def test_bad_values_unknown_jan_stale_revision_and_readonly_stop(self):
        for value in (-1,True,'2'):
            with self.assertRaises(DataError):self.save(days=dict(KOBE=value,SUGITO=None))
        with self.assertRaises(DataError):self.save(product='4900000000099')
        with self.assertRaises(DataError):self.save(reason='')
        self.save()
        with self.assertRaises(DataError):self.s.save_product_safety(JAN,dict(KOBE=1,SUGITO=1),'担当','架空',True,0)
        self.s.read_only=True
        with self.assertRaises(DataError):self.save()
    def test_stale_calculation_formal_issue_and_delivery_confirmation_block(self):
        data=self.data();rr=self.s.calculate(data,DAY);plan=self.s.calculate_delivery_plan(data,DAY)
        self.save()
        with self.assertRaises(DataError):self.s.calculate(data,DAY)
        with self.assertRaises(DataError):self.s.issue(data,rr,DAY,'担当',True,True)
        with self.assertRaises(DataError):self.s.confirm_delivery_plan(plan['plan_id'],'担当',True)
    def test_proof_and_effective_rows_must_agree(self):
        self.save();data=self.data();raw=dict(data.raw)
        rr=list(csv.DictReader(io.StringIO(raw['requirements.csv'])));rr[0]['safety_days']='9'
        from bunsen.requirements_data import SCHEMA,csv_text
        raw['requirements.csv']=csv_text(SCHEMA['requirements'],rr)
        with self.assertRaises(DataError):self.s.snapshot_data(raw)
        initial=self.s.product_safety();initial=dict(initial,revision=0)
        with self.assertRaises(DataError):parse(proof(initial))
    def test_archived_settings_and_lists_survive_change_and_restore(self):
        self.save();data,rr,issued=self.issue();original=copy.deepcopy(issued['lines'])
        self.save(days=dict(KOBE=None,SUGITO=None));self.assertEqual(self.s.report_payload(issued['number'])['lines'],original)
        validate_database(self.s.db);manifest=create_backup(self.s.db,self.app.home,self.kit/'backup.zip',self.s.clock)
        home=restore_backup(self.kit/'backup.zip',self.kit/'restored',manifest['archive_sha256'],self.s.clock)
        view=Service(home/'history.db',self.s.clock)
        try:
            payload=view.report_payload(issued['number']);self.assertEqual(payload['lines'],original);self.assertTrue(payload['restored_view'])
            self.assertEqual(len(write_documents(payload,self.kit/'restored-lists',scope='both')),12)
        finally:view.close()
    def test_all_master_list_includes_absent_pdf_as_unknown_and_real_zero(self):
        _,_,payload=self.issue();all_view=list_payload(payload,'all');rr=rows(all_view,'KOBE')
        self.assertEqual(len(rr),2);self.assertEqual(rr[0][9],0);self.assertEqual(rr[0][-1],'出荷なし（指示0）')
        self.assertEqual(rr[1][0],OTHER);self.assertTrue(all(v=='未確認' for v in rr[1][2:11]));self.assertIn('PDF未掲載',rr[1][-1])
        self.s.save_jan_master(dict(product=OTHER,name='現在の名称へ変更',expiry_type='best_before',shelf_life_days=10,kobe_p_per_pl=240,sugito_p_per_pl=480),'担当','架空改名',True,self.s.jan_master()['revision'],False)
        self.assertEqual(rows(list_payload(self.s.report_payload(payload['number']),'all'),'KOBE')[1][1],'架空PDF未掲載商品')
    def test_shipping_uses_final_manual_quantity_and_empty_warehouse(self):
        data=self.data();rr=self.s.calculate(data,DAY)
        self.s.edit_quantity(data,rr,('KOBE',JAN),240,'架空増量','担当');self.s.edit_quantity(data,rr,('SUGITO',JAN),0,'架空停止','担当')
        payload=self.s.issue(data,rr,DAY,'担当',True,True);shipping=list_payload(payload,'shipping')
        self.assertEqual(rows(shipping,'KOBE')[0][9],1);self.assertFalse(rows(shipping,'SUGITO'))
        self.assertNotIn(OTHER,report_html(shipping,'KOBE'));self.assertIn('出荷対象商品はありません',report_html(shipping,'SUGITO'))
    def test_both_outputs_unique_complete_and_legacy_default_preserved(self):
        _,_,payload=self.issue();before=copy.deepcopy(payload);paths=write_documents(payload,self.kit/'lists',scope='both')
        self.assertEqual(len(paths),12);self.assertEqual(payload,before);self.assertEqual(sum(p.suffix=='.pdf' for p in paths),4)
        path=next(p for p in paths if p.suffix=='.xlsx' and 'KOBE-全商品一覧' in p.name)
        with ZipFile(path) as z:sheet=ET.fromstring(z.read('xl/worksheets/sheet1.xml'))
        ns={'m':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
        self.assertEqual(''.join(sheet.find(".//m:c[@r='N6']",ns).itertext()),'状態')
        self.assertIn('PDF未掲載',''.join(sheet.find(".//m:c[@r='N8']",ns).itertext()))
        self.assertEqual(len(write_documents(payload,self.kit/'legacy')),6)
        existing=self.kit/'lists-existing';existing.mkdir();keep=existing/(payload['number']+'-KOBE-全商品一覧.pdf');keep.write_bytes(b'keep')
        with self.assertRaises(DataError):write_documents(payload,existing,scope='both')
        self.assertEqual(list(existing.iterdir()),[keep]);self.assertEqual(keep.read_bytes(),b'keep')
    def test_ui_saves_zero_and_reset_and_invalidates_old_results(self):
        self.app.data=self.data();self.app.compute();self.app.refresh_product_safety();self.app.safety_product.set(JAN+' 架空対象商品');self.app.select_product_safety()
        self.app.safety_days['KOBE'].set('0');self.app.safety_days['SUGITO'].set('');self.app.safety_reason.set('架空日数変更');self.app.save_product_safety()
        self.assertIsNone(self.app.data);self.assertIsNone(self.app.lines);self.assertEqual(self.s.product_safety()['values']['KOBE'][JAN],0)
        self.app.safety_reason.set('架空共通へ戻す');self.app.save_product_safety(reset=True);self.assertNotIn(JAN,self.s.product_safety()['values']['KOBE'])
    def test_history_chain_detects_changed_override_values(self):
        self.save();event=self.s.db.execute("SELECT id,payload FROM events WHERE kind='product_safety_change'").fetchone();payload=json.loads(event[1]);payload['after']['KOBE'][JAN]=9
        self.s.db.execute('UPDATE events SET payload=? WHERE id=?',(json.dumps(payload),event[0]));self.s.db.commit()
        with self.assertRaises(DataError):validate_database(self.s.db)
    def test_legacy_snapshot_without_product_proof_keeps_original_conditions(self):
        data=self.data();raw=dict(data.raw);raw.pop(FILE)
        old=self.s.snapshot_data(raw);rr=self.s.calculate(old,DAY)
        issued=self.s.issue(old,rr,DAY,'架空担当',True,True)
        self.save()
        with self.assertRaises(DataError):self.s.calculate(old,DAY)
        frozen=self.s.report_payload(issued['number'])
        self.assertNotIn(FILE,frozen['raw']);self.assertEqual([r['settings']['safety_days'] for r in frozen['lines']],[1,1])
        self.assertEqual(len(write_documents(frozen,self.kit/'old-original')),6)
