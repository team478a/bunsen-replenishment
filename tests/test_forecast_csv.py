import base64
import csv
import io
import json
import tempfile
import tkinter as tk
import unittest
from datetime import date,datetime,time
from fractions import Fraction
from pathlib import Path
from unittest.mock import patch

from bunsen.core import DataError,JST,load,calculate
from bunsen.jan_demo import create_kit
from bunsen.forecast_demo import fictional_source,JAN,MAPPING,MASTER
from bunsen.forecast_csv import analyse_bytes,read_forecast,make_outputs,verify_review,REVIEW_FILE,QUANTITY
from bunsen.exact_quantity import canonical,number,ceil_multiple
from bunsen.service import Service
from bunsen.backup import validate_database,create_backup,restore_backup

DAY=date(2026,11,9);NOW=datetime.combine(DAY,time(9),JST)

class ForecastCSVTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name);self.kit=create_kit(self.path/'kit',DAY);self.csv=self.kit/'csv'
        self.service=Service(self.kit/'home/history.db',lambda:NOW)
        self.service.save_jan_master(MASTER,'架空担当','確認',True,0,True)
    def tearDown(self):self.service.close();self.temp.cleanup()
    def report(self,content=None):return analyse_bytes(content if content is not None else fictional_source(DAY))
    def change(self,content,update):
        records=list(csv.DictReader(io.StringIO(content.decode('utf-8-sig'))));fields=list(records[0]);records=update(records)
        stream=io.StringIO();w=csv.DictWriter(stream,fieldnames=fields,lineterminator='\n');w.writeheader();w.writerows(records);return stream.getvalue().encode('utf-8-sig')
    def converted(self,content=None):
        outputs,_=make_outputs(self.report(content),MAPPING,'架空担当')
        for name,text in outputs.items():(self.csv/name).write_text(text,encoding='utf-8-sig')
        return load(self.csv,NOW)
    def save(self,report=None,mapping=None,ack=True,master=None,revision=0,folder=None):
        return self.service.save_forecast_csv(report or self.report(),MAPPING if mapping is None else mapping,'担当',ack,self.service.jan_master()['revision'] if master is None else master,revision,folder or self.path)
    def test_only_base_packs_are_used_not_bulk_inventory_or_manufacturing(self):
        report=self.report();self.assertEqual(report['rows'][0]['quantity_p'],'60.25')
        content=self.change(fictional_source(DAY),lambda rows:[dict(r,**{'大口込み総出荷数量（パック）':'invalid','大口特売追加減算数量（パック）':'123456789','製造計画納品総量（パック）':'invalid','予測在庫数量（パック）':'invalid'}) for r in rows])
        self.assertEqual(report['rows'],self.report(content)['rows'])
    def test_no_mapping_and_duplicate_destinations_are_blocked(self):
        for mapping in [{},{'TEST_A':'KOBE','TEST_B':None},{'TEST_A':'KOBE','TEST_B':'KOBE'}]:
            with self.assertRaises(DataError):self.save(mapping=mapping)
        self.assertFalse((self.path/'forecast.csv').exists())
    def test_missing_and_invalid_values_are_not_zero_filled(self):
        for value in ['', '-1','NaN','Infinity','1e3','１','1,234']:
            with self.subTest(value=value):
                report=self.report(self.change(fictional_source(DAY),lambda rows:[dict(r,**{QUANTITY:value}) for r in rows]))
                self.assertTrue(report['errors'])
                with self.assertRaises(DataError):make_outputs(report,MAPPING,'担当')
    def test_zero_is_preserved(self):
        r=self.report(fictional_source(DAY,quantities={('TEST_A',0):'0.000'}));self.assertEqual(r['rows'][0]['quantity_p'],0)
    def test_duplicate_key_and_mixed_run_block(self):
        content=fictional_source(DAY)
        self.assertTrue(self.report(self.change(content,lambda rows:rows+[rows[0]]))['errors'])
        self.assertTrue(self.report(self.change(content,lambda rows:[dict(r,**{'予測Run ID':'other'}) if i==0 else r for i,r in enumerate(rows)]))['errors'])
    def test_unknown_forecast_kind_does_not_become_confirmed(self):
        report=self.report(self.change(fictional_source(DAY),lambda rows:[dict(r,**{'予測区分':'不明'}) for r in rows]));self.assertTrue(report['errors'])
    def test_missing_required_column_is_not_replaced_by_bulk_column(self):
        content=fictional_source(DAY).decode('utf-8-sig').replace(QUANTITY,'wrong',1)
        with self.assertRaises(DataError):self.report(content.encode())
    def test_fractional_boundary_is_rounded_only_at_final_pl(self):
        content=fictional_source(DAY,quantities={('TEST_A',0):'120.2',('TEST_A',1):'119.8'})
        data=self.converted(content)
        for row in data.tables['inventory']:row['quantity_p']=0
        for row in data.tables['replenishment']:row.update(cycle_days=1,lead_days=0,coverage_days=2,safety_days=0)
        row=next(r for r in calculate(data,DAY) if r['warehouse']=='KOBE')
        self.assertEqual(row['required_p'],240);self.assertEqual(row['quantity_p'],240)
        data.tables['forecast'][0]['quantity_p']='120.200000000000000000000001'
        row=next(r for r in calculate(data,DAY) if r['warehouse']=='KOBE')
        self.assertEqual(row['required_p'],'240.000000000000000000000001');self.assertEqual(row['quantity_p'],480)
    def test_sub_pack_positive_need_is_not_rounded_down(self):
        self.assertEqual(ceil_multiple('0.000000000000000000000001',240),240)
        self.assertEqual(canonical(number('120.2')+number('119.8')),240)
        self.assertEqual(canonical(Fraction(-1,100)),'-0.01')
    def test_original_integer_contract_still_rejects_decimals(self):
        text=(self.csv/'forecast.csv').read_text(encoding='utf-8-sig').replace(',60,',',60.25,')
        (self.csv/'forecast.csv').write_text(text,encoding='utf-8-sig')
        with self.assertRaises(DataError):load(self.csv,NOW)
    def test_source_review_is_required_and_tamper_is_detected(self):
        self.converted();(self.csv/REVIEW_FILE).unlink()
        with self.assertRaises(DataError):load(self.csv,NOW)
        self.converted();text=(self.csv/'forecast.csv').read_text(encoding='utf-8-sig').replace('60.25','61.25',1);(self.csv/'forecast.csv').write_text(text,encoding='utf-8-sig')
        with self.assertRaises(DataError):load(self.csv,NOW)
    def test_bulk_scope_cannot_be_changed_in_review(self):
        outputs,_=make_outputs(self.report(),MAPPING,'担当')
        text=outputs[REVIEW_FILE].replace('base_forecast_only_bulk_orders_and_receipts_excluded','include_bulk')
        with self.assertRaises(ValueError):verify_review(text)
    def test_formal_replay_reports_and_14_day_plan_keep_exact_quantities(self):
        data=self.converted();lines=self.service.calculate(data,DAY)
        self.assertEqual([r['required_p'] for r in lines],[121,'72.5'])
        saved=self.service.issue(data,lines,DAY,'担当',True,True)
        self.assertEqual(self.service.get_issue(saved['number']),saved)
        self.assertEqual(self.service.snapshot_data(saved['raw'],saved['digest']).tables['forecast'],data.tables['forecast'])
        plan=self.service.calculate_delivery_plan(data,DAY);confirmed=self.service.confirm_delivery_plan(plan['plan_id'],'担当',True)
        self.assertTrue(confirmed['formal']);self.assertTrue(all(type(r['quantity_p']) is int for r in confirmed['shipments']))
        self.assertTrue(all(t['pallets']<=16 for t in confirmed['trips']))
        self.assertNotRegex('\n'.join(confirmed['warnings']),r'-?\d+/\d+P')
        validate_database(self.service.db)
        archive=self.path/'backup.zip';manifest=create_backup(self.service.db,self.kit/'home',archive,self.service.clock)
        restore_backup(archive,self.path/'restored',manifest['archive_sha256'],self.service.clock)
    def test_provisional_source_cannot_be_formally_issued(self):
        data=self.converted(self.change(fictional_source(DAY),lambda rows:[dict(r,**{'予測区分':'暫定予測'}) for r in rows]))
        lines=self.service.calculate(data,DAY,allow_provisional=True)
        with self.assertRaises(DataError):self.service.issue(data,lines,DAY,'担当',True,True)
    def test_save_records_mapping_and_original_and_never_overwrites(self):
        self.save();state=self.service.forecast_mapping();self.assertEqual(state['mapping'],MAPPING)
        payload=json.loads(self.service.db.execute("SELECT payload FROM events WHERE kind='forecast_csv_review'").fetchone()[0])
        self.assertEqual(base64.b64decode(payload['report']['source_base64']),fictional_source(DAY))
        with self.assertRaises(FileExistsError):self.save(revision=state['revision'])
    def test_ack_and_stale_mapping_and_stale_master_block(self):
        for kw in [dict(ack=False),dict(master=0),dict(revision=999)]:
            with self.assertRaises(DataError):self.save(**kw)
        self.assertFalse((self.path/'forecast.csv').exists())
    def test_changed_source_file_is_rejected(self):
        path=self.path/'source.csv';path.write_bytes(fictional_source(DAY));report=read_forecast(path);path.write_bytes(fictional_source(DAY,days=31))
        with self.assertRaises(DataError):self.save(report=report)
    def test_unknown_jan_blocks(self):
        content=fictional_source(DAY).replace(JAN.encode(),b'4900000000002')
        with self.assertRaises(DataError):self.save(report=self.report(content))
    def test_read_only_blocks_new_review(self):
        self.service.read_only=True
        with self.assertRaises(DataError):self.save()
    def test_large_original_evidence_survives_import(self):
        content=fictional_source(DAY).replace(b'fictional-run',b'f'*3000)
        data=self.converted(content)
        self.assertGreater(len(data.raw[REVIEW_FILE]),128*1024)
        self.assertEqual(calculate(data,DAY)[0]['quantity_p'],240)
    def test_second_file_conflict_rolls_back_new_forecast(self):
        (self.path/REVIEW_FILE).write_text('existing',encoding='utf-8')
        with self.assertRaises(FileExistsError):self.save()
        self.assertFalse((self.path/'forecast.csv').exists())
        self.assertEqual((self.path/REVIEW_FILE).read_text(encoding='utf-8'),'existing')
        self.assertEqual(self.service.forecast_mapping()['revision'],0)
    def test_future_generation_blocks_save(self):
        self.service.clock=lambda:datetime.combine(DAY,time(6),JST)
        with self.assertRaises(DataError):self.save()
        self.assertFalse((self.path/'forecast.csv').exists())
    def test_bad_original_hash_blocks_import(self):
        self.converted()
        path=self.csv/REVIEW_FILE
        text=path.read_text(encoding='utf-8-sig')
        report=self.report();text=text.replace(report['source_sha256'],'0'*64)
        path.write_text(text,encoding='utf-8-sig')
        with self.assertRaises(DataError):load(self.csv,NOW)
    def test_ui_mapping_gate_cancel_and_success_invalidate_old_calculation(self):
        from bunsen.ui import App
        from bunsen.forecast_ui import ForecastDialog
        from bunsen.core import LABELS
        root=tk.Tk();root.withdraw();app=App(root,self.kit/'home')
        try:
            app.service.clock=lambda:NOW;app.day.set(str(DAY));app.data=app.service.import_folder(self.csv,workflow=2);app.compute();app.operator.set('架空担当')
            path=self.path/'source.csv';path.write_bytes(fictional_source(DAY));dialog=ForecastDialog(app);dialog.load(path)
            self.assertEqual(str(dialog.save_button['state']),'disabled')
            with self.assertRaises(DataError):dialog.save()
            dialog.mapping_vars['TEST_A'].set(LABELS['KOBE']);self.assertEqual(str(dialog.save_button['state']),'disabled')
            dialog.mapping_vars['TEST_B'].set(LABELS['SUGITO']);self.assertEqual(str(dialog.save_button['state']),'normal')
            with patch('bunsen.forecast_ui.filedialog.askdirectory',return_value=''):dialog.save()
            self.assertTrue(app.lines)
            target=self.path/'prepared';target.mkdir()
            with patch('bunsen.forecast_ui.filedialog.askdirectory',return_value=str(target)):dialog.save()
            self.assertIsNone(app.data);self.assertFalse(app.lines)
            second=ForecastDialog(app);second.load(path);self.assertEqual(second.mapping(),MAPPING);second.destroy()
        finally:app.service.close();root.destroy()
