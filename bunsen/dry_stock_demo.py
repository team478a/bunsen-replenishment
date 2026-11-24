"""Fixed-date fictional intake and combined physical-PL comparison."""
from pathlib import Path
from datetime import date,datetime
import copy,tkinter as tk
from .core import JST
from .jan_demo import create_kit
from .ui import App
from .dry_stock_ui import DryStockDialog,DryStockComparison
from .dry_stock_intake import template
from .backup import validate_database,create_backup,restore_backup

def main(output):
    kit=create_kit(Path(output),date(2026,11,9));root=tk.Tk();root.withdraw();app=App(root,kit/'home')
    try:
        app.service.clock=lambda:datetime(2026,11,9,9,tzinfo=JST);app.operator.set('架空担当');app.day.set('2026-11-09')
        app.data=app.service.import_folder(kit/'csv',workflow=2);app.compute();before=copy.deepcopy(app.lines)
        path=kit/'fictional-dry-stock.csv';path.write_text(template()+'4900000000001,500,20\n',encoding='utf-8-sig')
        dialog=DryStockDialog(app);dialog.load(path);dialog.as_of.set('2026-11-09 08:00');dialog.save()
        check=DryStockComparison(app);row=check.report['rows'][0]
        assert (row['quantity_p'],row['usable_p'],row['shortage_p'])==(720,480,240);check.save()
        assert app.service.calculate(app.data,date(2026,11,9))==before
        app.service.edit_quantity(app.data,app.lines,('KOBE','4900000000001'),480,'架空増量','架空担当')
        check=DryStockComparison(app);assert check.report['rows'][0]['shortage_p']==480;check.save()
        validate_database(app.service.db)
        manifest=create_backup(app.service.db,app.home,kit/'backup.zip',app.service.clock)
        restore_backup(kit/'backup.zip',kit/'restore',manifest['archive_sha256'],app.service.clock)
        print('PASS: staging stock CSV, joint demand comparison, rounded PL, edit recheck and archival backup; demand unchanged.')
    finally:app.close()
    return kit
