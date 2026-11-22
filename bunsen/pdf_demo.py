"""Fictional fixed-date PDF reading demo; no business data or cloud service."""
import io
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
JAN='4900000000001'

def fictional_report(warehouse='KOBE',cases='7',loose='3',factor='10',jan=JAN,total=True,page_count=1,date='2026/11/20',printed_day=None):
    stream=io.BytesIO();c=canvas.Canvas(stream,pagesize=(595.32,841.92),invariant=1);pdfmetrics.registerFont(UnicodeCIDFont('HeiseiKakuGo-W5'))
    for index in range(1,page_count+1):
        c.setFont('HeiseiKakuGo-W5',10)
        c.drawString(15,821,'架空サンプル・実出荷不可')
        for x,y,text in [(220,45,'在 庫 一 覧 受 払'),(15,65,'在庫集計日：'+date),(220,115,'C入'),(536,105,'現在'),(520,121,'ケース'),(554,121,'バラ'),(15,790,'ムロオ '+('神戸支店' if warehouse=='KOBE' else '杉戸営業所')),(15,807,(printed_day or date)+f' 18:00:{index:02}'),(268,820,f'{index}/{page_count} ページ')]:c.drawString(x,841.92-y,text)
        c.setFont('Helvetica',10);c.drawString(-20,841.92-150,jan)
        if factor is not None:c.drawString(223,841.92-150,factor)
        c.setFont('HeiseiKakuGo-W5',10);c.drawString(60,841.92-150,'架空商品')
        if total and index==page_count:
            c.drawString(464,841.92-180,'商品合計');c.setFont('Helvetica',10)
            if cases is not None:c.drawString(525,841.92-180,cases)
            if loose is not None:c.drawString(558,841.92-180,loose)
        c.showPage()
    c.save();return stream.getvalue()

def main(output):
    import shutil
    import json
    import tkinter as tk
    from datetime import date,datetime
    from pathlib import Path
    from .core import JST
    from .jan_demo import create_kit
    from .ui import App
    from .pdf_reader_ui import PDFInventoryDialog,PDFPreview
    from .shipping_output import write_documents
    root_path=Path(output)
    if root_path.exists():raise ValueError('新規フォルダーを指定してください')
    kit=create_kit(root_path,date(2026,11,9));root=tk.Tk();root.withdraw();app=None
    try:
        app=App(root,kit/'home');app.service.clock=lambda:datetime(2026,11,9,9,tzinfo=JST)
        app.service.save_jan_master(dict(product=JAN,name='架空PDF商品',expiry_type='best_before',shelf_life_days=10,kobe_p_per_pl=240,sugito_p_per_pl=480),'架空担当','架空登録',True,0,True)
        dialog=PDFInventoryDialog(app);reports={};decisions={}
        for warehouse in ('KOBE','SUGITO'):
            content=fictional_report(warehouse,date='2026/11/09');path=kit/(warehouse+'.pdf');path.write_bytes(content)
            dialog.load(warehouse,path)
            assert dialog.items[(warehouse,JAN)]['quantity']==73
            preview=PDFPreview(dialog,content);root.update_idletasks();preview.close()
            reports[warehouse]=dict(report=dialog.reports[warehouse],as_of='2026-11-09T08:00:00+09:00',basis='opening',blank_policy='unknown')
            decisions[(warehouse,JAN)]=dict(quantity_p=73,reason='')
        decisions[('KOBE',JAN)]=dict(quantity_p=80,reason='架空原本再確認')
        target=kit/'pdf-import';target.mkdir()
        app.service.save_pdf_inventory(reports,decisions,'架空担当',True,dialog.revision,target);dialog.destroy()
        for name in ('forecast.csv','requirements.csv'):shutil.copyfile(kit/'csv'/name,target/name)
        app.data=app.service.import_folder(target,workflow=2);app.day.set('2026-11-09');app.compute()
        assert [row['inventory_p'] for row in app.lines]==[80,73]
        app.operator.set('架空担当');app.ack.set(True);app.issue(True)
        assert len(write_documents(app.service.report_payload(app.last_number),kit/'reports'))==6
        assert app.service.db.execute("SELECT count(*) FROM events WHERE kind='pdf_inventory_review'").fetchone()[0]==1
        print('PASS: PDF read, native preview, review/edit audit, CSV import, replenishment and formal Excel/PDF.')
    finally:
        if app is not None:app.close()
        else:root.destroy()
    return root_path
