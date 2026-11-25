"""Direct, warehouse-specific OOXML/PDF output; no business workbook dependency."""
import csv,json
import html
import io
import math
import unicodedata
from pathlib import Path
from xml.etree import ElementTree as ET
from zipfile import ZipFile, ZIP_DEFLATED

from .core import DataError, LABELS, case_p
from .requirements_data import PROFILE, NOTICE

HEADERS = ['商品','品名','現在庫P','対象予測P','安全P(最大)','必要P','換算後P','修正P','確定P','PL','甲','修正理由','到着日']


SCOPES={'all':'全商品一覧','shipping':'出荷対象商品一覧','both':'全商品一覧＋出荷対象商品一覧'}

def list_payload(payload,scope):
    if scope not in ('all','shipping'):raise DataError(['一覧の出力範囲が不正です'])
    return dict(payload,_list_scope=scope,lines=[r for r in payload['lines'] if scope!='shipping' or r['quantity_p']>0])

def safety_source(payload,line):
    if 'product-safety.csv' in payload.get('raw',{}):
        from .product_safety import parse
        if line['product'] in parse(payload['raw']['product-safety.csv'])['values'][line['warehouse']]:return '商品別設定'
    return '倉庫別共通' if 'common-settings.csv' in payload.get('raw',{}) else '取込条件'

def imported_products(payload):
    raw=payload.get('raw',{}).get('products.csv')
    products=list(csv.DictReader(io.StringIO(raw))) if raw else payload['tables']['products']
    keys=[p['product'] for p in products]
    if len(keys)!=len(set(keys)):raise DataError(['取込商品マスタのJANが重複しています'])
    return products

def headers(payload):return HEADERS+['状態'] if payload.get('_list_scope') else HEADERS

def scope_note(payload,warehouse):
    if payload.get('_list_scope')=='all':return '取込マスタの全商品。PDF未掲載・計算対象外は数量未確認として表示し、0にしません。出荷数量が未確認の行は出荷指示の対象ではありません。'
    if payload.get('_list_scope')=='shipping':return '手修正後の最終指示数量が0を超える商品のみ。'+('この倉庫の出荷対象商品はありません。' if not any(r['warehouse']==warehouse for r in payload['lines']) else '0数量に修正した商品は全商品一覧で確認してください。')
    return '必要Pと確定Pは区別。未確定指示の確定Pは空欄。実出荷実績は含みません。'

def notice(payload):
    parts = ['必要補充数量の指示です。実出荷実績・ブンセンの供給可能在庫を示しません。']
    if payload.get('_list_scope'):parts.append(SCOPES[payload['_list_scope']])
    if 'product-safety.csv' in payload.get('raw',{}):
        from .product_safety import parse
        parts.append('商品別安全在庫版 '+str(parse(payload['raw']['product-safety.csv'])['revision']))
    if payload.get('product_scope'):parts.append(f"明示的な補充対象外JAN {len(payload['product_scope']['excluded'])}件（在庫0の意味ではありません）")
    if payload.get('profile')==PROFILE:parts.append(NOTICE)
    if payload.get('forecast_source'):
        source=payload['forecast_source']
        parts.append('需要予測列: '+source['quantity_column']+' / 大口・注文・製造・入庫の別加算なし / 倉庫対応 '+', '.join(code+'→'+LABELS[w] for code,w in source['mapping'].items()))
    if payload.get('jan_master_revision') is not None:parts.append(f'JAN・PLマスタ版 {payload["jan_master_revision"]}')
    if payload.get('product_master_revision') is not None:
        parts.append(f'登録商品マスタ版 {payload["product_master_revision"]}（発行時の保存原本）。')
    if payload.get('trial_only'):
        parts.append('架空データの試用帳票 - 実出荷に使用しない。')
    if payload.get('restored_view'):
        parts.append('復元履歴の参考帳票 - 実出荷に使用しない。')
    status = payload.get('report_status')
    if not status:
        parts.append('現行改訂は未照合。出荷前に最新版を確認してください。')
    elif status['superseded']:
        parts.append('旧版・差替え済み - 出荷に使用しない。現行番号: '+status['latest_number'])
    else:
        parts.append('照合時点の最新改訂: '+status['latest_number'])
    if not payload['formal']:
        parts.append('参考・未確定指示。正式出荷に使用しない。')
    return ' / '.join(parts)


def rows(payload, warehouse):
    products = {r['product']:r for r in payload['tables']['products']}
    result = []
    for row in payload['lines']:
        if row['warehouse'] != warehouse:
            continue
        product = products[row['product']]
        if row.get('rounding')=='ceil_physical_pl':
            pl,remainder=row['quantity_p']//row['unit_p'],None
        else:
            cases = row['quantity_p']//case_p(product)
            pl, remainder = divmod(cases,product['cases_per_pl'])
        result.append([row['product'],product['name'],row['inventory_p'],row['demand_p'],row['safety_p'],
                       row['required_p'],row['suggested_p'],row['quantity_p'] if row['edit_reason'].strip() else None,
                       row['quantity_p'] if payload['formal'] else None,pl,remainder,row['edit_reason'],row['arrival']])
    if payload.get('_list_scope'):
        quantities={(r['warehouse'],r['product']):r['quantity_p'] for r in payload['lines']}
        result=[r+['出荷対象' if quantities[(warehouse,r[0])]>0 else '出荷なし（指示0）'] for r in result]
    if payload.get('_list_scope')=='all':
        seen={r[0] for r in result};excluded=payload.get('product_scope',{}).get('excluded',{})
        source=payload.get('raw',{}).get('pdf-presence.csv','');missing=set()
        if source:
            for item in csv.DictReader(io.StringIO(source)):
                if item['warehouse']==warehouse:missing.update(json.loads(item['missing_json']))
        for product in imported_products(payload):
            p=product['product']
            if p not in seen:
                reason='補充対象外（マスタ設定）' if p in excluded else 'PDF未掲載・数量未確認' if p in missing else '計算対象外・数量未確認'
                result.append([p,product['name']]+['未確認']*9+['','未確認',reason])
        order={p['product']:i for i,p in enumerate(imported_products(payload))}
        result.sort(key=lambda r:order.get(r[0],len(order)))
    return result


def title(payload, warehouse):
    if payload.get('_list_scope'):return SCOPES[payload['_list_scope']]+('（正式確定）' if payload['formal'] else '（参考・未確定）')+' - '+LABELS[warehouse]
    return ('出荷指示書（必要補充）' if payload['formal'] else '参考補充指示書')+' - '+LABELS[warehouse]


def report_html(payload, warehouse):
    esc = lambda v: html.escape(str(v) if v is not None else '',quote=True)
    table = ''.join('<tr>'+''.join('<td>'+esc(v)+'</td>' for v in row)+'</tr>' for row in rows(payload,warehouse))
    basis = '\n'.join(f'{r["product"]}: 在庫基準 {r["inventory_as_of"]} ({r["inventory_basis"]}) / 入庫網羅確認 {r["transit_checked_at"] if r["transit_checked_at"] is not None else "対象外"} / 予測基準 {r["forecast_as_of"]} / 設定 {r["settings"]} / {r["warning"]}' for r in payload['lines'] if r['warehouse']==warehouse)
    return f'''<!doctype html><html lang="ja"><meta charset="utf-8"><title>{esc(title(payload,warehouse))}</title>
<style>body{{font-family:Meiryo,sans-serif;margin:24px}}table{{border-collapse:collapse;width:100%;font-size:12px}}td,th{{border:1px solid #888;padding:6px;overflow-wrap:anywhere}}.notice{{border:3px solid #a54000;padding:10px}}pre{{white-space:pre-wrap}}@media print{{button{{display:none}}thead{{display:table-header-group}}}}</style>
<h1>{esc(title(payload,warehouse))}</h1><p class="notice">{esc(notice(payload))}</p>
<p>{esc(payload['number'])} / 出荷予定日 {esc(payload['dispatch'])} / 担当者 {esc(payload['operator'])} / 発行 {esc(payload['at'])}</p>
<p>{esc(scope_note(payload,warehouse))}</p><table><thead><tr>{''.join('<th>'+esc(v)+'</th>' for v in headers(payload))}</tr></thead><tbody>{table}</tbody></table>
<h2>根拠・警告</h2><pre>{esc(basis)}</pre><p>数量・単位は商品別マスタを使用。PL＋甲は数量表記です。実ロット・期限・車両積載の確認や実出荷の登録は別業務です。</p>
<button onclick="window.print()">印刷・PDF保存</button></html>'''


def xlsx_bytes(payload, warehouse):
    grid=[[title(payload,warehouse)],[notice(payload)],[payload['number'],'出荷予定日',payload['dispatch'],'担当者',payload['operator']],
          [scope_note(payload,warehouse)],[],headers(payload)]+rows(payload,warehouse)
    grid += [[],['データ識別',payload['digest']],['発行日時',payload['at']]]
    for line in payload['lines']:
        if line['warehouse']==warehouse:
            if line.get('source_pdf'):grid += [['在庫PDF',line['source_pdf'],'SHA-256',line['source_sha256'],'転記確認者',line['inventory_checker']]]
            grid += [[line['product'],'在庫基準',line['inventory_as_of'],'入庫網羅確認',line['transit_checked_at'] if line['transit_checked_at'] is not None else '対象外'],
                     ['予測基準',', '.join(line['forecast_as_of'])],['安全在庫日数',line['settings']['safety_days'],'適用元',safety_source(payload,line)],['警告',line['warning']]]
    return xlsx_grid_bytes(grid)


def xlsx_grid_bytes(grid):
    # Minimal standard OOXML, independent of Excel installations or proprietary runtimes.
    ns='http://schemas.openxmlformats.org/spreadsheetml/2006/main'
    ET.register_namespace('',ns)
    root=ET.Element('{'+ns+'}worksheet')
    ET.SubElement(ET.SubElement(root,'{'+ns+'}sheetPr'),'{'+ns+'}pageSetUpPr',fitToPage='1')
    views=ET.SubElement(root,'{'+ns+'}sheetViews');view=ET.SubElement(views,'{'+ns+'}sheetView',workbookViewId='0')
    ET.SubElement(view,'{'+ns+'}pane',ySplit='6',topLeftCell='A7',state='frozen')
    cols=ET.SubElement(root,'{'+ns+'}cols')
    widths=[14,26,13,13,14,13,13,13,13,8,8,30,16]
    columns=max(13,max(len(row) for row in grid));widths += [26]*(columns-13)
    for i,width in enumerate(widths,1):
        ET.SubElement(cols,'{'+ns+'}col',min=str(i),max=str(i),width=str(width),customWidth='1')
    sheet=ET.SubElement(root,'{'+ns+'}sheetData')
    merged_rows={1,2,4}|{ri for ri,values in enumerate(grid,1) if len(values)==1 and isinstance(values[0],str)}
    for ri,values in enumerate(grid,1):
        font_size=13 if ri in (1,6) else 11
        height=36 if ri in (1,2,6) else 30
        for ci,value in enumerate(values):
            width=(sum(widths) if ri in merged_rows else widths[ci])-2
            lines=sum(max(1,math.ceil(sum(2 if unicodedata.east_asian_width(c) in ('W','F') else 1 for c in part)/(width*11/font_size)))
                      for part in str(value if value is not None else '').split('\n'))
            height=max(height,lines*(font_size+5)+10)
            if height>409:
                raise DataError([f'Excelの印刷行高上限を超えます: {chr(65+ci)}{ri}。品名・理由などの長文を見直してください（内容は切り捨てません）'])
        row=ET.SubElement(sheet,'{'+ns+'}row',r=str(ri),ht=str(height),customHeight='1')
        for ci,value in enumerate(values,1):
            if value is None:continue
            letter=chr(64+ci)
            cell=ET.SubElement(row,'{'+ns+'}c',r=f'{letter}{ri}',s='1' if ri in (1,6) else '0')
            if type(value) is int and abs(value) < 10**15:
                ET.SubElement(cell,'{'+ns+'}v').text=str(value)
            else:
                cell.set('t','inlineStr');text=ET.SubElement(ET.SubElement(cell,'{'+ns+'}is'),'{'+ns+'}t')
                text.text=''.join(c for c in str(value) if ord(c)>=32 or c in '\n\t\r')
    merges=ET.SubElement(root,'{'+ns+'}mergeCells',count=str(len(merged_rows)))
    for ri in sorted(merged_rows):ET.SubElement(merges,'{'+ns+'}mergeCell',ref=f'A{ri}:{chr(64+columns)}{ri}')
    ET.SubElement(root,'{'+ns+'}pageSetup',orientation='landscape',paperSize='9',fitToWidth='1',fitToHeight='0')
    files={
        '[Content_Types].xml':'<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/></Types>',
        '_rels/.rels':'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>',
        'xl/workbook.xml':'<workbook xmlns="'+ns+'" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="出荷指示" sheetId="1" r:id="rId1"/></sheets><definedNames><definedName name="_xlnm.Print_Titles" localSheetId="0">出荷指示!$1:$6</definedName></definedNames></workbook>',
        'xl/_rels/workbook.xml.rels':'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>',
        'xl/styles.xml':'<styleSheet xmlns="'+ns+'"><fonts count="2"><font><sz val="11"/><name val="Yu Gothic"/></font><font><b/><sz val="13"/><name val="Yu Gothic"/></font></fonts><fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill></fills><borders count="1"><border/></borders><cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs><cellXfs count="2"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0" applyAlignment="1"><alignment vertical="center" wrapText="1"/></xf><xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyAlignment="1"><alignment vertical="center" wrapText="1"/></xf></cellXfs><cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>',
        'xl/worksheets/sheet1.xml':ET.tostring(root,encoding='utf-8',xml_declaration=True)}
    output=io.BytesIO()
    with ZipFile(output,'w',ZIP_DEFLATED) as z:
        for name,content in files.items():z.writestr(name,content)
    return output.getvalue()


def pdf_bytes(payload, warehouse):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
    font='HeiseiKakuGo-W5'
    pdfmetrics.registerFont(UnicodeCIDFont(font))
    style=ParagraphStyle('jp',fontName=font,fontSize=9.5,leading=14,wordWrap='CJK')
    heading=ParagraphStyle('heading',fontName=font,fontSize=16,leading=22,spaceAfter=12)
    para=lambda v:Paragraph(html.escape('' if v is None else str(v)),style)
    output=io.BytesIO()
    document=SimpleDocTemplate(output,pagesize=landscape(A4),leftMargin=22,rightMargin=22,topMargin=22,bottomMargin=25)
    story=[Paragraph(html.escape(title(payload,warehouse)),heading),para(notice(payload)),Spacer(1,8),
           para(f'{payload["number"]} / 出荷予定日 {payload["dispatch"]} / 担当者 {payload["operator"]} / {payload["at"]}'),Spacer(1,10)]
    code_style=ParagraphStyle('jan',fontName='Helvetica',fontSize=9.5,leading=14)
    indexes=[0,1,5,6,7,8,9,12,11,13] if payload.get('_list_scope') else [0,1,5,6,7,8,9,10,12,11]
    if payload.get('_list_scope'):story += [para(scope_note(payload,warehouse)),Spacer(1,8)]
    grid=[[para(headers(payload)[i]) for i in indexes]]+[[(Paragraph(html.escape(str(row[i])),code_style) if i==0 and payload.get('_list_scope') else para(row[i])) for i in indexes] for row in rows(payload,warehouse)]
    table=Table(grid,colWidths=([95,105,48,52,48,48,45,70,80,130] if payload.get('_list_scope') else [55,120,60,65,60,60,35,35,85,100]),repeatRows=1,hAlign='LEFT',splitInRow=1)
    table.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.HexColor('#e5eff5')),('GRID',(0,0),(-1,-1),.4,colors.grey),('VALIGN',(0,0),(-1,-1),'TOP'),('TOPPADDING',(0,0),(-1,-1),6),('BOTTOMPADDING',(0,0),(-1,-1),6)]))
    story += [table,Spacer(1,12),para('根拠・警告（予測は確定在庫ではありません）')]
    for line in payload['lines']:
        if line['warehouse']==warehouse:
            if line.get('source_pdf'):story += [para(f'在庫PDF {line["source_pdf"]} / SHA-256 {line["source_sha256"]} / 転記確認者 {line["inventory_checker"]}')]
            settings=line['settings']
            basis='始業在庫' if line['inventory_basis']=='opening' else '終業在庫'
            story += [para(f'{line["product"]}: 現在庫 {line["inventory_p"]}P / 対象需要予測 {line["demand_p"]}P / 安全在庫(最大) {line["safety_p"]}P'),
                      para(f'在庫基準 {line["inventory_as_of"]} ({basis}) / 入庫網羅確認 {line["transit_checked_at"] if line["transit_checked_at"] is not None else "対象外"}'),
                      para('予測基準 '+', '.join(line['forecast_as_of'])),
                      para('安全在庫の適用元 '+safety_source(payload,line)),
                      para(f'補充サイクル {settings["cycle_days"]}日 / 配送 {settings["lead_days"]}日 / 安全在庫 {settings["safety_days"]}日分 / 対象 {settings["coverage_days"]}日 / {line["warning"]}'),Spacer(1,6)]
    story += [para('実ロット・期限・積載確認と実出荷登録は別業務。必要数量の確定は供給・実出荷を保証しません。')]
    def footer(canvas,doc):
        canvas.setFont(font,8)
        labels=[]
        if payload.get('trial_only'):labels.append('架空試用・実出荷不可')
        if payload.get('restored_view'):labels.append('復元参考・実出荷不可')
        if not payload['formal']:labels.append('未確定・正式使用不可')
        if payload.get('report_status',{}).get('superseded'):labels.append('旧版・出荷使用不可')
        canvas.drawString(22,12,' / '.join(labels+['必要補充指示・実出荷実績ではありません']))
        canvas.drawRightString(810,12,f'{payload["number"]} / {doc.page}')
    document.build(story,onFirstPage=footer,onLaterPages=footer)
    return output.getvalue()


def write_documents(payload, folder, *, scope="calculated"):
    if payload.get('workflow') != 2:
        raise DataError(['旧履歴は従来の履歴再出力を使用してください'])
    if payload['formal'] and any(not r['forecast_confirmed'] for r in payload['lines']):
        raise DataError(['未確定予測の正式帳票は出力できません'])
    if scope not in ('calculated','all','shipping','both'):raise DataError(['出力範囲を全商品・出荷対象・両方から選択してください'])
    folder=Path(folder).resolve();outputs=[]
    for kind in (('all','shipping') if scope=='both' else (scope,)):
        view=payload if kind=='calculated' else list_payload(payload,kind)
        for warehouse in LABELS:
            name=f'{payload["number"]}-{warehouse}'+('' if kind=='calculated' else '-'+SCOPES[kind])
            outputs += [(folder/(name+'.xlsx'),xlsx_bytes(view,warehouse)),
                        (folder/(name+'.pdf'),pdf_bytes(view,warehouse)),
                        (folder/(name+'.html'),report_html(view,warehouse).encode('utf-8'))]
    if any(p.exists() for p,_ in outputs):raise DataError(['既存帳票は上書きしません。別の出力フォルダーを指定してください'])
    folder.mkdir(parents=True,exist_ok=True)
    created=[]
    try:
        for path,content in outputs:
            with path.open('xb') as stream:
                created.append(path);stream.write(content)
    except Exception:
        for path in created:path.unlink(missing_ok=True)
        raise
    return [p for p,_ in outputs]
