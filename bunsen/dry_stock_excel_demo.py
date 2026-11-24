"""Fictional XLSX intake regression. Never reads business files."""
import io,zipfile,tkinter as tk
from pathlib import Path
from datetime import date,datetime
from xml.etree.ElementTree import Element,SubElement,tostring
from .dry_stock_excel import HEADERS

def fictional_excel(rows=None):
    rows=rows if rows is not None else [
        ['DEMO-A','架空商品・通常','75g','4900000000001',80,20,80,240,'参考表記','入力済み','08:00'],
        ['DEMO-B','架空商品・増量','80g','4900000000001',80,20,80,480,'参考表記','入力済み','08:01'],
        ['DEMO-C','架空未入力商品','100g','4900000000002',20,10,60,None,None,'未入力',None]]
    ns='http://schemas.openxmlformats.org/spreadsheetml/2006/main'
    sheet=Element('worksheet',xmlns=ns);data=SubElement(sheet,'sheetData')
    for i,values in enumerate([HEADERS]+rows,1):
        row=SubElement(data,'row',r=str(i))
        for j,value in enumerate(values):
            if value is None:continue
            c=SubElement(row,'c',r=chr(65+j)+str(i),t='n' if type(value) in (int,float) else 'inlineStr')
            if type(value) in (int,float):SubElement(c,'v').text=str(value)
            else:SubElement(SubElement(c,'is'),'t').text=str(value)
    stream=io.BytesIO()
    with zipfile.ZipFile(stream,'w',zipfile.ZIP_DEFLATED) as z:
        z.writestr('[Content_Types].xml','<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>')
        z.writestr('_rels/.rels','<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        z.writestr('xl/workbook.xml',f'<workbook xmlns="{ns}" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="実在庫" sheetId="1" r:id="rId1"/></sheets></workbook>')
        z.writestr('xl/_rels/workbook.xml.rels','<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>')
        z.writestr('xl/worksheets/sheet1.xml',tostring(sheet,encoding='utf-8'))
    return stream.getvalue()

def main(output):
    from .core import JST
    from .jan_demo import create_kit
    from .ui import App
    from .dry_stock_ui import DryStockDialog,DryStockComparison
    from .backup import validate_database,create_backup,restore_backup
    kit=create_kit(Path(output),date(2026,11,9));root=tk.Tk();root.withdraw();app=App(root,kit/'home')
    try:
        app.service.clock=lambda:datetime(2026,11,9,9,tzinfo=JST);app.operator.set('架空担当');app.day.set('2026-11-09')
        app.data=app.service.import_folder(kit/'csv',workflow=2);app.compute();before=[dict(r) for r in app.lines]
        path=kit/'fictional-available-stock.xlsx';path.write_bytes(fictional_excel())
        dialog=DryStockDialog(app);dialog.load(path);assert dialog.report['rows'][0]['available_p']==720
        dialog.as_of.set('2026-11-09 08:10');dialog.save()
        check=DryStockComparison(app);row=check.report['rows'][0]
        assert (row['quantity_p'],row['usable_p'],row['shortage_p'])==(720,720,0)
        assert row['stock_p'] is None and row['reserved_p'] is None;check.save()
        assert app.service.calculate(app.data,date(2026,11,9))==before
        validate_database(app.service.db)
        manifest=create_backup(app.service.db,app.home,kit/'backup.zip',app.service.clock)
        restore_backup(kit/'backup.zip',kit/'restored',manifest['archive_sha256'],app.service.clock)
        print('PASS: XLSX available packs, JAN sum, pending rows, preserved product codes, joint comparison and backup; demand unchanged.')
    finally:app.close()
    return kit
