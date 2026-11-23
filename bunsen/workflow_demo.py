"""Acceptance regression: import forecast before master, then issue from PDF stock."""
from pathlib import Path
from datetime import date,datetime
from unittest.mock import patch
import shutil,tkinter as tk
from .core import JST,LABELS
from .forecast_demo import fictional_source,MASTER,MAPPING
from .forecast_csv import read_forecast
from .jan_csv import export_csv
from .jan_csv_ui import JanCSVDialog
from .jan_demo import create_kit
from .pdf_demo import fictional_report
from .pdf_reader_ui import PDFInventoryDialog
from .ui import App
from .shipping_output import write_documents
from .backup import validate_database,create_backup,restore_backup

def main(output):
    kit=create_kit(output,date(2026,11,9));target=kit/'new-flow';target.mkdir()
    root=tk.Tk();root.withdraw();app=App(root,kit/'home')
    try:
        app.service.clock=lambda:datetime(2026,11,9,20,tzinfo=JST);app.operator.set('架空担当')
        source=kit/'架空予測.csv';source.write_bytes(fictional_source(date(2026,11,9)))
        app.service.save_forecast_csv(read_forecast(source),MAPPING,'架空担当',True,0,0,target)
        assert not app.service.jan_master()['products']
        csv=kit/'架空JANマスタ.csv';csv.write_text(export_csv([dict(MASTER,name='架空ＬＬ商品ＡＢＣ１２３')]),encoding='utf-8-sig')
        dialog=JanCSVDialog(app);dialog.load(csv);dialog.reason.set('架空CSV一括登録');dialog.save()
        stock=PDFInventoryDialog(app)
        for w in ('KOBE','SUGITO'):
            pdf=kit/(w+'.pdf');pdf.write_bytes(fictional_report(w,date='2026/11/08',printed_day='2026/11/09'))
            stock.load(w,pdf);assert stock.meta[w]['as_of'].get()=='18:00:01';assert 'basis' not in stock.meta[w]
        with patch('bunsen.pdf_reader_ui.filedialog.askdirectory',return_value=str(target)):stock.save()
        for key,value in dict(cycle_days=1,lead_days=0,safety_days=1,coverage_days=2).items():app.setting_values[key].set(str(value))
        app.setting_reason.set('架空の全商品共通条件');app.confirmed_action(app.setting_checked,app.apply_settings)
        assert not (target/'requirements.csv').exists()
        app.data=app.service.import_folder(target,workflow=2,use_common_settings=True);app.day.set('2026-11-09');app.compute()
        assert 'common-settings.csv' in app.data.raw
        assert [r['quantity_p'] for r in app.lines]==[240,480]
        assert all(r['timeline'][0]['date']=='2026-11-09' for r in app.lines)
        assert all(r['inventory_basis']=='closing' for r in app.lines)
        app.ack.set(True);app.issue(True);assert len(write_documents(app.service.report_payload(app.last_number),kit/'reports'))==6
        validate_database(app.service.db)
        manifest=create_backup(app.service.db,app.home,kit/'backup.zip',app.service.clock)
        restore_backup(kit/'backup.zip',kit/'restored',manifest['archive_sha256'],app.service.clock)
        print('PASS: forecast before master, CSV master, PDF footer time, common conditions without CSV, formal Excel/PDF, backup restore.')
    finally:app.close()
    return kit
