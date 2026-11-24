import copy,json,tempfile,tkinter as tk,unittest
from pathlib import Path
from datetime import date,datetime,timedelta
from unittest.mock import patch
from bunsen.core import DataError,JST
from bunsen.dry_stock_intake import analyse,read_csv,template,compare
from bunsen.dry_stock_ui import DryStockDialog,DryStockComparison
from bunsen.service import Service
from bunsen.ui import App
from bunsen.jan_demo import create_kit
from bunsen.backup import validate_database,create_backup,restore_backup

DAY=date(2026,11,9);NOW=datetime(2026,11,9,9,tzinfo=JST);JAN='4900000000001'
def source(stock=500,reserved=20):return (template()+f'{JAN},{stock},{reserved}\n').encode('utf-8-sig')

class DryStockIntakeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name);self.s=Service(self.path/'home/history.db',lambda:NOW)
    def tearDown(self):self.s.close();self.temp.cleanup()
    def save(self,content=None,as_of=None):
        state=self.s.dry_stock()
        return self.s.save_dry_stock(analyse(content or source()),as_of or '2026-11-09T08:00:00+09:00','架空担当',True,state['revision'] if state else 0)
    def data(self):
        kit=create_kit(self.path/'kit',DAY);data=self.s.import_folder(kit/'csv',workflow=2);lines=self.s.calculate(data,DAY);return data,lines
    def test_source_original_and_available_stock_are_saved(self):
        state=self.save();self.assertEqual(state['report']['rows'][0],dict(product=JAN,stock_p=500,reserved_p=20));validate_database(self.s.db)
        cp=analyse(source().decode('utf-8-sig').encode('cp932'));self.assertFalse(cp['errors'])
    def test_missing_and_explicit_zero_are_different(self):
        stock=self.save(source(0,0));planned=[dict(warehouse='KOBE',product=JAN,required_p=1,quantity_p=240)]
        self.assertEqual(compare(stock,planned,str(DAY),NOW.isoformat())[0]['shortage_p'],240)
        planned[0]['product']='4900000000002';row=compare(stock,planned,str(DAY),NOW.isoformat())[0]
        self.assertIsNone(row['shortage_p']);self.assertIsNone(row['stock_p']);self.assertIn('未掲載',row['status'])
    def test_malformed_duplicate_negative_and_reserved_over_stock_stop_all(self):
        for content in [source('',0),source(-1,0),source(1,2),source(3,''),source(3,1)+f'{JAN},4,0\n'.encode(),b'JAN,wrong\n',template().encode('utf-8-sig')]:
            with self.assertRaises((DataError,ValueError)):self.save(content)
        self.assertIsNone(self.s.dry_stock())
    def test_joint_allocation_uses_rounded_quantities_and_does_not_change_demand(self):
        data,lines=self.data();before=copy.deepcopy(lines);self.save()
        report=self.s.dry_stock_comparison(data,lines,DAY);row=report['rows'][0]
        self.assertEqual((row['KOBE'],row['SUGITO'],row['quantity_p'],row['usable_p'],row['shortage_p']),(240,480,720,480,240))
        self.assertEqual(lines,before);self.assertEqual(self.s.calculate(data,DAY),before)
        self.s.edit_quantity(data,lines,('KOBE',JAN),480,'架空増量','架空担当')
        self.assertEqual(self.s.dry_stock_comparison(data,lines,DAY)['rows'][0]['shortage_p'],480)
    def test_dates_missing_inventory_and_future_import(self):
        data,lines=self.data();row=self.s.dry_stock_comparison(data,lines,DAY)['rows'][0];self.assertIsNone(row['shortage_p'])
        self.save(as_of='2026-11-08T08:00:00+09:00');row=self.s.dry_stock_comparison(data,lines,DAY)['rows'][0];self.assertIn('基準日違い',row['status'])
        with self.assertRaises(DataError):self.save(as_of='2026-11-09T10:00:00+09:00')
    def test_revision_source_change_and_read_only_are_blocked(self):
        state=self.save();path=self.path/'source.csv';path.write_bytes(source());report=read_csv(path);path.write_bytes(source(600,0))
        with self.assertRaises(DataError):self.s.save_dry_stock(report,state['as_of'],'担当',True,state['revision'])
        with self.assertRaises(DataError):self.s.save_dry_stock(analyse(source()),state['as_of'],'担当',True,0)
        self.s.read_only=True
        with self.assertRaises(DataError):self.save()
    def test_report_tampering_and_stale_comparison_are_blocked(self):
        bad=analyse(source());bad['rows'][0]['stock_p']=999
        with self.assertRaises(DataError):self.s.save_dry_stock(bad,NOW.isoformat(),'担当',True,0)
        data,lines=self.data();self.save();report=self.s.dry_stock_comparison(data,lines,DAY);self.save(source(600,0))
        with self.assertRaises(DataError):self.s.save_dry_stock_comparison(report,'担当')
        report=self.s.dry_stock_comparison(data,lines,DAY);report['rows'][0]['shortage_p']=999
        with self.assertRaises(DataError):self.s.save_dry_stock_comparison(report,'担当')
    def test_backup_restores_intake_and_comparison_and_detects_tampering(self):
        data,lines=self.data();self.save();report=self.s.dry_stock_comparison(data,lines,DAY);self.s.save_dry_stock_comparison(report,'担当');validate_database(self.s.db)
        backup=self.path/'backup.zip';manifest=create_backup(self.s.db,self.path/'home',backup,self.s.clock);restored=restore_backup(backup,self.path/'restore',manifest['archive_sha256'],self.s.clock)
        view=Service(restored/'history.db',lambda:NOW)
        try:self.assertEqual(view.dry_stock()['report']['rows'][0]['stock_p'],500)
        finally:view.close()
        event=self.s.db.execute("SELECT id,payload FROM events WHERE kind='dry_stock_check'").fetchone();payload=json.loads(event[1]);payload['rows'][0]['stock_p']=999
        self.s.db.execute('UPDATE events SET payload=? WHERE id=?',(json.dumps(payload),event[0]));self.s.db.commit()
        with self.assertRaises(DataError):validate_database(self.s.db)
    def test_ui_import_and_comparison_refresh_after_quantity_edit(self):
        kit=create_kit(self.path/'ui-kit',DAY);root=tk.Tk();root.withdraw();app=App(root,kit/'home')
        try:
            app.service.clock=lambda:NOW;app.operator.set('架空担当');app.day.set(str(DAY));app.data=app.service.import_folder(kit/'csv',workflow=2);app.compute()
            path=self.path/'ui.csv';path.write_bytes(source());dialog=DryStockDialog(app);dialog.load(path);dialog.as_of.set('2026-11-09 08:00');dialog.save()
            check=DryStockComparison(app);self.assertEqual(check.report['rows'][0]['shortage_p'],240);check.save()
            app.service.edit_quantity(app.data,app.lines,('KOBE',JAN),480,'架空増量','担当')
            check=DryStockComparison(app);self.assertEqual(check.report['rows'][0]['shortage_p'],480);check.destroy()
            validate_database(app.service.db)
        finally:app.close()
