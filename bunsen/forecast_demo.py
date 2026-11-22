"""Fictional no-bulk forecast fixtures and fixed-date integration demo."""
from datetime import date,datetime,time,timedelta
from pathlib import Path
from .core import JST
from .requirements_data import csv_text
from .forecast_csv import REQUIRED

JAN='4900000000001'
MAPPING={'TEST_A':'KOBE','TEST_B':'SUGITO'}
MASTER=dict(product=JAN,name='架空予測取込商品',expiry_type='best_before',shelf_life_days=10,kobe_p_per_pl=240,sugito_p_per_pl=480)


def fictional_source(day,days=32,quantities=None):
    fields=list(REQUIRED)+['大口込み総出荷数量（パック）','大口特売追加減算数量（パック）','製造計画納品総量（パック）','予測在庫数量（パック）','予測Run ID']
    rows=[]
    for source,quantity in [('TEST_A','60.25'),('TEST_B','30.125')]:
        for index in range(days):
            rows.append(dict(zip(fields,[JAN,source,str(day+timedelta(days=index)),(quantities or {}).get((source,index),quantity),datetime.combine(day,time(7),JST).isoformat(),'通常予測','正式Production予測','999999','888888','777777','666666','fictional-run'])))
    return csv_text(','.join(fields),rows).encode('utf-8-sig')


def main(output):
    import tkinter as tk
    from .jan_demo import create_kit
    from .ui import App
    from .forecast_ui import ForecastDialog
    from .forecast_csv import read_forecast
    from .shipping_output import write_documents
    from .delivery_output import export_plan
    from .backup import create_backup,validate_database
    day=date(2026,11,9);kit=create_kit(output,day);source=kit/'架空予測結果.csv';source.write_bytes(fictional_source(day))
    root=tk.Tk();root.withdraw();app=None
    try:
        app=App(root,kit/'home');app.service.clock=lambda:datetime.combine(day,time(9),JST)
        app.service.save_jan_master(MASTER,'架空担当','架空マスタ確認',True,0,True)
        dialog=ForecastDialog(app);dialog.load(source)
        assert str(dialog.save_button['state'])=='disabled'
        from .core import LABELS
        for code,warehouse in MAPPING.items():dialog.mapping_vars[code].set(LABELS[warehouse])
        assert str(dialog.save_button['state'])=='normal'
        (kit/'csv/forecast.csv').unlink()  # Only this freshly-created fictional kit.
        app.service.save_forecast_csv(read_forecast(source),MAPPING,'架空担当',True,dialog.master_revision,dialog.mapping_revision,kit/'csv');dialog.destroy()
        app.data=app.service.import_folder(kit/'csv',workflow=2);app.day.set(str(day));app.compute()
        assert [r['required_p'] for r in app.lines]==[121,'72.5']
        assert [r['quantity_p'] for r in app.lines]==[240,480]
        app.operator.set('架空担当');app.ack.set(True);app.issue(True)
        payload=app.service.report_payload(app.last_number)
        assert payload['forecast_source']['scope']=='base_forecast_only_bulk_orders_and_receipts_excluded'
        assert len(write_documents(payload,kit/'reports'))==6
        plan=app.service.calculate_delivery_plan(app.data,day);confirmed=app.service.confirm_delivery_plan(plan['plan_id'],'架空担当',True)
        assert len(export_plan(confirmed,kit/'plans'))==7
        create_backup(app.service.db,kit/'home',kit/'backup.zip',app.service.clock)
        validate_database(app.service.db)
        print('PASS: no-bulk forecast import, explicit mapping, exact decimals, formal reports, 14-day plan, backup.')
    finally:
        if app is not None:app.close()
        else:root.destroy()
    return kit
