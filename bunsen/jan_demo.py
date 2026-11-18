"""Fictional PDF-backed JAN/physical pallet kit; never reads business files."""
import base64
import hashlib
import io
import json
import tempfile
from datetime import date, datetime, time, timedelta
from pathlib import Path
from .core import JST
from .requirements_data import SCHEMA, EVIDENCE, csv_text


def create_kit(folder, day):
    folder=Path(folder)
    if folder.exists():raise ValueError('新しいフォルダーを指定してください')
    folder.mkdir(parents=True);source=folder/'csv';source.mkdir();home=folder/'home';home.mkdir()
    (home/'trial-info.json').write_text(json.dumps(dict(format='bunsen-trial-home',version=1,trial_only=True)),encoding='utf-8')
    jan='4900000000001';at=datetime.combine(day,time(7),JST).isoformat()
    tables={name:[] for name in SCHEMA}
    tables['products']=[dict(product=jan,name='架空JANそうめん',expiry_type='best_before',shelf_life_days=10,confirmed='yes')]
    evidence=[]
    from reportlab.pdfgen.canvas import Canvas
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    pdfmetrics.registerFont(UnicodeCIDFont('HeiseiKakuGo-W5'))
    for warehouse,stock,pallet in [('KOBE',120,240),('SUGITO',48,480)]:
        name=warehouse+'-fictional.pdf';buf=io.BytesIO();c=Canvas(buf);c.setFont('HeiseiKakuGo-W5',12)
        for index,line in enumerate(['架空在庫報告・実業務使用不可',warehouse,at+' 始業在庫',jan,f'{stock}P']):c.drawString(40,780-index*25,line)
        c.save();pdf=buf.getvalue();digest=hashlib.sha256(pdf).hexdigest()
        evidence.append(dict(source_pdf=name,sha256=digest,content_base64=base64.b64encode(pdf).decode('ascii')))
        tables['pallets'].append(dict(warehouse=warehouse,product=jan,p_per_pl=pallet,confirmed='yes'))
        tables['inventory'].append(dict(warehouse=warehouse,product=jan,quantity_p=stock,as_of=at,basis='opening',source_pdf=name,source_sha256=digest,checker='架空転記確認者',confirmed='yes'))
        tables['requirements'].append(dict(warehouse=warehouse,product=jan,cycle_days=1,lead_days=1,safety_days=1,coverage_days=2,confirmed='yes'))
        for i in range(32):
            tables['forecast'].append(dict(warehouse=warehouse,product=jan,date=str(day+timedelta(days=i)),quantity_p=60 if warehouse=='KOBE' else 30,as_of=at,order_basis='included',status='confirmed'))
    for name,fields in SCHEMA.items():(source/(name+'.csv')).write_text(csv_text(fields,tables[name]),encoding='utf-8-sig')
    (source/'source_evidence.csv').write_text(csv_text(EVIDENCE,evidence),encoding='utf-8-sig')
    (folder/'GUIDE.md').write_text('架空JAN・PL方式。業務条件ではありません。\nBunsenReplenishment.exe --data-dir "'+str(home.resolve())+'"\n出荷予定日を'+str(day)+'にしてcsvフォルダーを取り込みます。神戸120P→240P（1物理PL）、杉戸72P→480P（1物理PL）。\n入庫・輸送中は対象外。PDFは保存証跡内に保持。実PDFの自動抽出はありません。',encoding='utf-8')
    return folder


def main(output=None):
    from .service import Service
    from .shipping_output import write_documents
    from .delivery_output import export_plan
    day=date(2026,11,9)
    clock=lambda:datetime.combine(day,time(9),JST)
    if output is None:
        with tempfile.TemporaryDirectory() as temp:return main(Path(temp)/'demo')
    root=create_kit(output,day);service=Service(root/'home/history.db',clock)
    try:
        data=service.import_folder(root/'csv',workflow=2);rows=service.calculate(data,day)
        assert [r['suggested_p'] for r in rows]==[240,480]
        service.edit_quantity(data,rows,('KOBE','4900000000001'),480,'架空PL修正','架空担当')
        service.issue(data,rows,day,'架空担当',False)
        saved=service.issue(data,rows,day,'架空担当',True,True)
        write_documents(service.report_payload(saved['number']),root/'reports')
        plan=service.calculate_delivery_plan(data,day)
        confirmed=service.confirm_delivery_plan(plan['plan_id'],'架空担当',True)
        export_plan(confirmed,root/'plans')
        assert all(t['pallets']<=16 for t in confirmed['trips'])
        print('PASS: JAN/physical PL, excluded inbound, PDF evidence, edit/issue, Excel/PDF, 14-day plan.')
    finally:service.close()
