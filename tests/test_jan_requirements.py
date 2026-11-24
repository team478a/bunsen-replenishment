import base64
import copy
import csv
from datetime import date, datetime, time, timedelta
import hashlib
import io
import json
from pathlib import Path
import tempfile
import tkinter as tk
import unittest
from unittest.mock import patch

from bunsen.core import DataError, JST, load, calculate
from bunsen.service import Service
from bunsen.jan_demo import create_kit
from bunsen.requirements_data import PROFILE, SCHEMA, csv_text
from bunsen.shipping_output import rows, write_documents
from bunsen.delivery import build_plan
from bunsen.delivery_output import grid_rows, plan_grid
from bunsen.backup import create_backup, restore_backup, validate_database
from bunsen.diagnostics import diagnose

DAY=date(2026,11,9)
NOW=datetime.combine(DAY,time(9),JST)
JAN='4900000000001'
MASTER=dict(product=JAN,name='架空JAN',expiry_type='best_before',shelf_life_days=10,kobe_p_per_pl=240,sugito_p_per_pl=480)


class JanRequirementsTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        create_kit(self.root/'kit',DAY);self.csv=self.root/'kit/csv'
        self.service=Service(self.root/'kit/home/history.db',lambda:NOW)

    def tearDown(self):
        self.service.close();self.temp.cleanup()

    def data(self):return self.service.import_folder(self.csv,workflow=2)

    def change(self,name,fn):
        path=self.csv/(name+'.csv')
        with path.open(encoding='utf-8-sig',newline='') as stream:
            reader=csv.DictReader(stream);fields=reader.fieldnames;records=list(reader)
        records=fn(records)
        with path.open('w',encoding='utf-8-sig',newline='') as stream:
            writer=csv.DictWriter(stream,fieldnames=fields,lineterminator='\n');writer.writeheader();writer.writerows(records)

    def set_field(self,name,key,value):
        self.change(name,lambda records:[dict(r,**{key:value}) for r in records])

    def test_independent_warehouse_pl_rounding_without_transit(self):
        data=self.data();lines=self.service.calculate(data,DAY)
        self.assertEqual([r['required_p'] for r in lines],[120,72])
        self.assertEqual([r['quantity_p'] for r in lines],[240,480])
        self.assertTrue(all(r['transit_p'] is None and r['transit_checked_at'] is None for r in lines))
        self.assertNotIn('transit.csv',data.raw)
        self.assertTrue(all(d['transit_p'] is None for r in lines for d in r['timeline']))

    def test_existing_transit_even_invalid_is_ignored_not_confirmed_zero(self):
        before=self.data()
        (self.csv/'transit.csv').write_bytes(b'bad\xff')
        after=self.data()
        self.assertEqual(before.digest,after.digest)
        self.assertEqual(calculate(before,DAY),calculate(after,DAY))

    def test_zero_inventory_distinct_from_missing(self):
        self.set_field('inventory','quantity_p','0');self.assertTrue(self.data())
        self.set_field('inventory','quantity_p','')
        with self.assertRaisesRegex(DataError,'欠損'):self.data()

    def test_decimal_prediction_never_automatically_rounded(self):
        self.set_field('forecast','quantity_p','1.5')
        with self.assertRaisesRegex(DataError,'丸め方'):self.data()

    def test_unknown_warehouse_no_70301_guess(self):
        self.set_field('inventory','warehouse','70301')
        with self.assertRaisesRegex(DataError,'warehouse'):self.data()

    def test_duplicate_jan_and_missing_warehouse_pl_rejected(self):
        self.change('products',lambda rows:rows+rows)
        with self.assertRaisesRegex(DataError,'重複'):self.data()
        self.change('products',lambda rows:rows[:1]);self.change('pallets',lambda rows:rows[:1])
        with self.assertRaisesRegex(DataError,'SUGITO'):self.data()

    def test_pdf_hash_missing_checker_and_unconfirmed_stop(self):
        self.set_field('inventory','source_sha256','0'*64)
        with self.assertRaisesRegex(DataError,'SHA-256'):self.data()
        self.set_field('inventory','checker','')
        with self.assertRaisesRegex(DataError,'checker'):self.data()

    def test_pdf_evidence_tampering_is_not_accepted(self):
        self.set_field('source_evidence','content_base64',base64.b64encode(b'%PDF-fake').decode())
        with self.assertRaisesRegex(DataError,'ハッシュ'):self.data()

    def test_pdf_source_change_observed_and_blocks_import(self):
        evidence=list(csv.DictReader((self.csv/'source_evidence.csv').open(encoding='utf-8-sig')))
        for r in evidence:(self.csv/r['source_pdf']).write_bytes(base64.b64decode(r['content_base64']))
        from bunsen.requirements_data import SourceGuard
        original=SourceGuard.finish
        def changed(guard):
            path=self.csv/evidence[0]['source_pdf'];path.write_bytes(path.read_bytes()+b'\nchanged')
            return original(guard)
        with patch.object(SourceGuard,'finish',changed):
            with self.assertRaisesRegex(DataError,'変わ|変更'):self.data()

    def test_unrelated_output_pdfs_do_not_block_or_change_import(self):
        before=self.data();name='F-20261124-R001-KOBE.pdf';path=self.csv/name
        path.write_bytes(b'%PDF-fictional-output')
        (self.csv/'F-20261124-R001-SUGITO.pdf').write_bytes(b'%PDF-fictional-output')
        after=self.data();self.assertEqual(after.digest,before.digest)
        self.assertEqual(calculate(after,DAY),calculate(before,DAY))
        from bunsen.requirements_data import SourceGuard
        original=SourceGuard.finish
        def change_output(guard):
            path.write_bytes(b'%PDF-output-revised')
            (self.csv/'other-output.pdf').write_bytes(b'%PDF-new-output')
            return original(guard)
        with patch.object(SourceGuard,'finish',change_output):self.assertEqual(self.data().digest,before.digest)

    def test_windows_source_pdf_case_difference_is_still_monitored(self):
        import os
        if os.name!='nt':self.skipTest('Windows file-name case semantics')
        evidence=list(csv.DictReader((self.csv/'source_evidence.csv').open(encoding='utf-8-sig')))
        for r in evidence:(self.csv/r['source_pdf'].upper()).write_bytes(base64.b64decode(r['content_base64']))
        self.assertTrue(self.data())
        from bunsen.requirements_data import SourceGuard
        original=SourceGuard.finish
        def changed(guard):
            path=self.csv/evidence[0]['source_pdf'].upper();path.write_bytes(path.read_bytes()+b'changed')
            return original(guard)
        with patch.object(SourceGuard,'finish',changed),self.assertRaises(DataError):self.data()

    def test_source_discovery_does_not_forget_first_inventory_bytes(self):
        original=Path.read_bytes;seen=0
        def read(path):
            nonlocal seen
            result=original(path)
            if path==self.csv/'inventory.csv':
                seen+=1
                if seen==1:path.write_bytes(result.replace(b',120,',b',121,'))
            return result
        with patch.object(Path,'read_bytes',read):
            with self.assertRaises(DataError) as exc:self.data()
        self.assertIn('INPUT_CHANGED',{i.code for i in exc.exception.issues})

    def test_snapshot_preserves_pdf_without_original_file(self):
        data=self.data();fresh=self.service.snapshot_data(data.raw,data.digest)
        self.assertEqual(fresh.digest,data.digest)
        self.assertIn('content_base64',fresh.raw['source_evidence.csv'])

    def test_inventory_excess_no_need_and_prearrival_risk(self):
        self.set_field('inventory','quantity_p','10000')
        self.assertTrue(all(r['quantity_p']==0 for r in calculate(self.data(),DAY)))
        self.set_field('inventory','quantity_p','0')
        self.assertTrue(all('入荷前欠品' in r['warning'] for r in calculate(self.data(),DAY)))

    def test_forecast_gap_and_provisional_formal_gate(self):
        self.change('forecast',lambda rows:[r for r in rows if r['date']!=str(DAY)])
        with self.assertRaisesRegex(DataError,'予測欠損'):calculate(self.data(),DAY)

    def test_provisional_and_acknowledgement_formal_gate(self):
        self.set_field('forecast','status','provisional');data=self.data()
        lines=self.service.calculate(data,DAY,allow_provisional=True)
        with self.assertRaisesRegex(DataError,'未確定'):self.service.issue(data,lines,DAY,'担当',True,True)
        self.set_field('forecast','status','confirmed');data=self.data();lines=self.service.calculate(data,DAY)
        with self.assertRaisesRegex(DataError,'確認操作'):self.service.issue(data,lines,DAY,'担当',True,False)

    def test_manual_quantity_only_whole_physical_pl_and_history(self):
        data=self.data();lines=self.service.calculate(data,DAY)
        with self.assertRaises(DataError):self.service.edit_quantity(data,lines,('KOBE',JAN),241,'理由','担当')
        self.service.edit_quantity(data,lines,('KOBE',JAN),480,'架空修正','担当')
        payload=self.service.issue(data,lines,DAY,'担当',True,True)
        saved=self.service.get_issue(payload['number'])
        self.assertEqual(saved['profile'],PROFILE);self.assertEqual(saved['inbound_scope'],'excluded')
        self.assertEqual(saved['lines'][0]['suggested_p'],240)
        self.assertEqual(saved['lines'][0]['quantity_p'],480)
        self.assertEqual(saved['lines'][0]['edit_reason'],'架空修正')
        self.assertTrue(self.service.db.execute("SELECT 1 FROM events WHERE kind='edit'").fetchone())

    def test_formal_revalidation_and_revision_race(self):
        data=self.data();lines=self.service.calculate(data,DAY)
        forged=copy.deepcopy(lines);forged[0]['unit_p']=1;forged[0]['quantity_p']=241;forged[0]['edit_reason']='改ざん'
        with self.assertRaises(DataError):self.service.issue(data,forged,DAY,'担当',True,True)
        self.service.issue(data,lines,DAY,'担当',True,True)
        with self.assertRaisesRegex(DataError,'改訂'):self.service.issue(data,lines,DAY,'担当',True,True)

    def test_14day_warehouse_pallets_capacity_and_reference_expiry(self):
        report=build_plan(self.data(),DAY,NOW)
        self.assertEqual(report['confirmed_inbound'],None)
        self.assertEqual(report['profile'],PROFILE)
        for t in report['trips']:
            self.assertLessEqual(t['pallets'],16)
            for item in t['items']:
                self.assertEqual(item['quantity_p']%item['p_per_pl'],0)
                self.assertEqual(item['pallets'],item['quantity_p']//item['p_per_pl'])
                self.assertEqual(item['p_per_pl'],240 if item['warehouse']=='KOBE' else 480)
                self.assertEqual(item['estimated_expiry'],'2026-11-19')
        self.assertTrue(all('PL（物理）' in r[-1] for r in grid_rows(report,'KOBE')))

    def test_expiry_missing_blocks_plan_but_daily_still_works(self):
        self.set_field('products','expiry_type','');self.set_field('products','shelf_life_days','')
        data=self.data();self.assertTrue(calculate(data,DAY))
        with self.assertRaisesRegex(DataError,'概算期限'):build_plan(data,DAY,NOW)

    def test_csv_master_and_registered_master_versions_and_diagnostics(self):
        state=self.service.save_jan_master(MASTER,'担当','根拠',True,0,True)
        (self.csv/'products.csv').unlink();(self.csv/'pallets.csv').unlink()
        data=self.service.import_folder(self.csv,workflow=2,use_registered_jan=True)
        self.assertEqual(data.jan_master_revision,state['revision'])
        from bunsen.jan_catalog import master_csvs
        self.assertTrue(diagnose(self.csv,DAY,NOW,workflow=2,master_csvs=master_csvs(state['products']))['calculation_possible'])
        lines=self.service.calculate(data,DAY)
        changed=dict(MASTER,kobe_p_per_pl=480)
        self.service.save_jan_master(changed,'担当','更新',True,state['revision'],False)
        with self.assertRaisesRegex(DataError,'更新'):self.service.issue(data,lines,DAY,'担当',True,True)

    def test_catalog_requires_explicit_check_and_optimistic_revision(self):
        with self.assertRaises(DataError):self.service.save_jan_master(MASTER,'担当','根拠',False,0,True)
        self.service.save_jan_master(MASTER,'担当','根拠',True,0,True)
        with self.assertRaises(DataError):self.service.save_jan_master(MASTER,'担当','根拠',True,0,False)

    def test_plan_formal_stops_after_catalog_update(self):
        state=self.service.save_jan_master(MASTER,'担当','根拠',True,0,True)
        data=self.service.import_folder(self.csv,workflow=2,use_registered_jan=True)
        plan=self.service.calculate_delivery_plan(data,DAY)
        self.service.save_jan_master(dict(MASTER,sugito_p_per_pl=960),'担当','更新',True,state['revision'],False)
        with self.assertRaisesRegex(DataError,'更新'):self.service.confirm_delivery_plan(plan['plan_id'],'担当',True)

    def test_settings_edit_and_backup_restore_preserve_new_scope(self):
        state=self.service.save_jan_master(MASTER,'担当','根拠',True,0,True)
        data=self.service.import_folder(self.csv,workflow=2,use_registered_jan=True)
        values=dict(cycle_days=1,lead_days=1,safety_days=1,coverage_days=3)
        data=self.service.update_settings(data,('KOBE',JAN),values,'根拠','担当')
        self.assertEqual(data.jan_master_revision,state['revision'])
        self.assertNotIn('replenishment.csv',data.raw)
        saved=self.service.issue(data,calculate(data,DAY),DAY,'担当',True,True)
        self.assertEqual(validate_database(self.service.db)['issues'],1)
        archive=self.root/'backup.zip';create_backup(self.service.db,self.service.db_path.parent,archive,clock=lambda:NOW)
        restored=restore_backup(archive,self.root/'restored',clock=lambda:NOW)
        # The restore API returns the new data-home path.
        target=Path(restored) if not isinstance(restored,dict) else Path(restored['home'])
        reader=Service(target/'history.db',lambda:NOW)
        try:
            self.assertTrue(reader.read_only)
            self.assertEqual(reader.report_payload(saved['number'])['profile'],PROFILE)
        finally:reader.close()

    def test_warehouse_excel_pdf_scope_and_physical_quantity(self):
        data=self.data();saved=self.service.issue(data,calculate(data,DAY),DAY,'担当',True,True)
        payload=self.service.report_payload(saved['number'])
        self.assertEqual(rows(payload,'KOBE')[0][9:11],[1,None])
        paths=write_documents(payload,self.root/'reports');self.assertEqual(len(paths),6)
        from pypdf import PdfReader
        text='\n'.join(p.extract_text() for p in PdfReader(next(p for p in paths if p.suffix=='.pdf')).pages)
        self.assertIn('対象外',text);self.assertIn('在庫PDF',text)

    def test_new_ui_master_without_import_and_check_reset(self):
        from bunsen.ui import App
        root=tk.Tk();root.withdraw()
        app=App(root,self.root/'kit/home')
        try:
            for k,v in MASTER.items():app.jan_values[k].set('賞味期限' if k=='expiry_type' else str(v))
            app.jan_operator.set('担当');app.jan_reason.set('確認');app.jan_ack.set(True)
            app.save_jan()
            self.assertEqual(len(app.service.jan_master()['products']),1)
            app.jan_ack.set(True);app.jan_values['kobe_p_per_pl'].set('480');self.assertFalse(app.jan_ack.get())
        finally:app.service.close();root.destroy()

    def test_pdf_template_requires_manual_quantities_and_preserves_original(self):
        from bunsen.pdf_inventory import prepare_inventory
        ev=list(csv.DictReader((self.csv/'source_evidence.csv').open(encoding='utf-8-sig')))
        pdfs={}
        for w,r in zip(('KOBE','SUGITO'),ev):
            path=self.root/(w+'.pdf');path.write_bytes(base64.b64decode(r['content_base64']));pdfs[w]=path
        before={k:v.read_bytes() for k,v in pdfs.items()}
        folder=self.root/'prepared';folder.mkdir()
        prepare_inventory([MASTER],pdfs,NOW.isoformat(),'opening','担当',folder)
        records=list(csv.DictReader((folder/'inventory.csv').open(encoding='utf-8-sig')))
        self.assertTrue(all(r['quantity_p']=='' and r['confirmed']=='' for r in records))
        self.assertEqual(before,{k:v.read_bytes() for k,v in pdfs.items()})
        with self.assertRaises(FileExistsError):prepare_inventory([MASTER],pdfs,NOW.isoformat(),'opening','担当',folder)

    def test_unconfirmed_source_inventory_stops_even_for_reference(self):
        self.set_field('inventory','confirmed','no')
        with self.assertRaises(DataError):self.data()

    def test_oversize_pallet_trip_splits_using_physical_slots(self):
        from bunsen.delivery import pack_trips
        items=[dict(warehouse='KOBE',product=JAN,dispatch=str(DAY),p_per_pl=240,quantity_p=240*17)]
        trips=pack_trips(items)
        self.assertEqual([r['pallets'] for r in trips],[16,1])
        self.assertEqual(sum(i['quantity_p'] for t in trips for i in t['items']),240*17)

    def test_tampered_saved_plan_is_recomputed_before_confirm(self):
        plan=self.service.calculate_delivery_plan(self.data(),DAY)
        record=self.service.db.execute("SELECT id,payload FROM events WHERE kind='delivery_plan'").fetchone()
        payload=json.loads(record[1]);payload['report']['shipments'][0]['p_per_pl']=1
        self.service.db.execute('UPDATE events SET payload=? WHERE id=?',(json.dumps(payload),record[0]));self.service.db.commit()
        with self.assertRaisesRegex(DataError,'再計算'):self.service.confirm_delivery_plan(plan['plan_id'],'担当',True)

    def test_jan_reference_is_not_reinterpreted_as_legacy_resume(self):
        data=self.data();saved=self.service.issue(data,calculate(data,DAY),DAY,'担当',False)
        with self.assertRaisesRegex(DataError,'再取込'):self.service.resume_draft(saved['number'])

    def test_invalid_catalog_backup_is_rejected_with_data_error(self):
        self.service.save_jan_master(MASTER,'担当','根拠',True,0,True)
        row=self.service.db.execute("SELECT id,payload FROM events WHERE kind='jan_master_change'").fetchone()
        payload=json.loads(row[1]);payload['products']=None
        self.service.db.execute('UPDATE events SET payload=? WHERE id=?',(json.dumps(payload),row[0]));self.service.db.commit()
        with self.assertRaises(DataError):validate_database(self.service.db)
