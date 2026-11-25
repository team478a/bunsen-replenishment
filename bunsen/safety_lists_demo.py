"""Fixed-date fictional product safety and two-list acceptance path."""
import shutil,tkinter as tk
from pathlib import Path
from datetime import date,datetime
from unittest.mock import patch
from .core import JST,DataError
from .jan_demo import create_kit
from .ui import App
from .pdf_demo import fictional_report
from .pdf_reader_ui import PDFInventoryDialog
from .shipping_output import write_documents,list_payload,rows
from .backup import validate_database,create_backup,restore_backup

JAN='4900000000001';OTHER='4900000000002';DAY=date(2026,11,9)
NOW=datetime(2026,11,9,20,tzinfo=JST)
SETTINGS={w:dict(cycle_days=1,lead_days=lead,safety_days=1,coverage_days=2) for w,lead in [('KOBE',1),('SUGITO',2)]}

def prepare(output):
    kit=create_kit(Path(output),DAY);root=tk.Tk();root.withdraw();app=App(root,kit/'home')
    try:
        app.service.clock=lambda:NOW;app.operator.set('架空担当');app.day.set(str(DAY))
        for jan,name in [(JAN,'架空対象商品'),(OTHER,'架空PDF未掲載商品')]:
            master=dict(product=jan,name=name,expiry_type='best_before',shelf_life_days=10,kobe_p_per_pl=240,sugito_p_per_pl=480)
            app.service.save_jan_master(master,'架空担当','架空マスタ登録',True,app.service.jan_master()['revision'],True)
        dialog=PDFInventoryDialog(app)
        for w in ('KOBE','SUGITO'):
            path=kit/(w+'.pdf');path.write_bytes(fictional_report(w,cases='99999' if w=='KOBE' else '0',loose='0',date='2026/11/08'))
            dialog.load(w,path)
        target=kit/'input';target.mkdir()
        with patch('bunsen.pdf_reader_ui.filedialog.askdirectory',return_value=str(target)):dialog.save()
        shutil.copyfile(kit/'csv/forecast.csv',target/'forecast.csv')
        app.service.save_common_settings(SETTINGS,'架空担当','架空の倉庫別条件',True,0)
        return app,kit,target
    except Exception:
        app.close();raise

def main(output):
    app,kit,target=prepare(output)
    try:
        app.refresh_product_safety();app.safety_product.set(JAN+' 架空対象商品');app.select_product_safety()
        app.safety_days['KOBE'].set('2');app.safety_days['SUGITO'].set('0');app.safety_reason.set('架空商品別の確認');app.save_product_safety()
        app.data=app.service.import_folder(target,workflow=2,use_common_settings=True,use_registered_jan=True);app.compute()
        assert [r['settings']['safety_days'] for r in app.lines]==[2,0]
        assert [r['quantity_p'] for r in app.lines]==[0,480]
        with patch('bunsen.simple_ui.filedialog.askdirectory',return_value=str(kit/'lists')):app.confirm_and_export()
        payload=app.service.report_payload(app.last_number)
        assert len(list((kit/'lists').iterdir()))==12
        all_rows=rows(list_payload(payload,'all'),'KOBE');assert len(all_rows)==2
        assert all_rows[0][9]==0 and all_rows[1][9]=='未確認' and 'PDF未掲載' in all_rows[1][-1]
        assert not rows(list_payload(payload,'shipping'),'KOBE')
        assert len(rows(list_payload(payload,'shipping'),'SUGITO'))==1
        app.service.save_product_safety(JAN,dict(KOBE=None,SUGITO=None),'架空担当','共通条件へ復帰',True,app.service.product_safety()['revision'])
        assert app.service.report_payload(app.last_number)['lines']==payload['lines']
        try:app.service.calculate(app.data,DAY)
        except DataError:pass
        else:raise AssertionError('Stale product safety calculation was reused')
        validate_database(app.service.db)
        manifest=create_backup(app.service.db,app.home,kit/'backup.zip',app.service.clock)
        restore_backup(kit/'backup.zip',kit/'restored',manifest['archive_sha256'],app.service.clock)
        print('PASS: warehouse/JAN safety overrides, zero/inherit, full-master and shipping lists, unknown PDF rows, stale gate and archival restore.')
        return kit
    finally:app.close()
