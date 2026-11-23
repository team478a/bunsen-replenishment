"""Fictional fixed-date warehouse lead-time regression through UI and reports."""
from pathlib import Path
from datetime import date,datetime,timedelta
import tkinter as tk
from .core import JST
from .jan_demo import create_kit
from .ui import App
from .common_settings import parse
from .shipping_output import write_documents,rows
from .backup import validate_database,create_backup,restore_backup

VALUES={w:dict(cycle_days=1,lead_days=lead,safety_days=1,coverage_days=2) for w,lead in [('KOBE',0),('SUGITO',2)]}

def main(output):
    day=date(2026,11,9);kit=create_kit(Path(output),day)
    root=tk.Tk();root.withdraw();app=App(root,kit/'home')
    try:
        app.service.clock=lambda:datetime(2026,11,9,9,tzinfo=JST)
        for w,values in VALUES.items():
            for key,value in values.items():app.setting_values[(w,key)].set(str(value))
        app.operator.set('架空担当');app.setting_reason.set('架空の倉庫別条件・業務値ではありません')
        app.confirmed_action(app.setting_checked,app.apply_settings)
        app.data=app.service.import_folder(kit/'csv',workflow=2,use_common_settings=True,require_warehouse_settings=True)
        app.day.set(str(day));app.compute();by_warehouse={r['warehouse']:r for r in app.lines}
        for w,values in VALUES.items():
            assert by_warehouse[w]['arrival']==str(day+timedelta(days=values['lead_days']))
        assert '入荷前欠品リスク' in by_warehouse['SUGITO']['warning']
        assert '入荷前欠品リスク' not in by_warehouse['KOBE']['warning']
        assert parse(app.data.raw['common-settings.csv'])['values']==VALUES
        app.ack.set(True);app.issue(True);payload=app.service.report_payload(app.last_number)
        assert rows(payload,'KOBE')[0][-1]=='2026-11-09'
        assert rows(payload,'SUGITO')[0][-1]=='2026-11-11'
        assert len(write_documents(payload,kit/'reports'))==6
        plan=app.service.calculate_delivery_plan(app.data,day)
        for trip in plan['trips']:
            for item in trip['items']:
                assert (date.fromisoformat(item['arrival'])-date.fromisoformat(item['dispatch'])).days==VALUES[item['warehouse']]['lead_days']
        validate_database(app.service.db)
        manifest=create_backup(app.service.db,app.home,kit/'backup.zip',app.service.clock)
        restore_backup(kit/'backup.zip',kit/'restore',manifest['archive_sha256'],app.service.clock)
        print('PASS: warehouse-specific lead days, arrival, pre-arrival shortage, reports, 14-day plan, backup.')
    finally:app.close()
    return kit
