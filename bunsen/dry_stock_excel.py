"""Read the confirmed staging-stock XLSX layout without Excel or recalculation."""
import base64,hashlib,io,posixpath,re,zipfile
from decimal import Decimal,InvalidOperation
from xml.etree import ElementTree as ET
from .core import DataError

FORMAT='zaiko_available_p_v1'
HEADERS=['品番コード','品番名','規格','JANCD','1ケースの個数','1ボールの個数','1パレットのケース数','実在庫数','荷姿換算','状態','入力時刻']
NS={'m':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
REL='http://schemas.openxmlformats.org/officeDocument/2006/relationships'
LIMIT=5*1024*1024

def _integer(value):
    try:
        n=Decimal(value)
        if not n.is_finite() or n<0 or n!=n.to_integral_value() or n>999999999999999:raise ValueError()
        return int(n)
    except (InvalidOperation,ValueError):raise ValueError('0以上の整数パック数を入力してください') from None

def analyse_excel(content):
    if not isinstance(content,bytes) or len(content)>LIMIT:raise DataError(['乾麺在庫Excelは5MB以下にしてください'])
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as z:
            infos=z.infolist()
            if len(infos)>5000 or len({i.filename for i in infos})!=len(infos) or sum(i.file_size for i in infos)>40*1024*1024:raise ValueError('Excelの展開サイズ・項目数が上限を超えています')
            def xml(name):
                data=z.read(name)
                if b'<!DOCTYPE' in data.upper() or b'<!ENTITY' in data.upper():raise ValueError('未対応のXMLです')
                return ET.fromstring(data)
            book=xml('xl/workbook.xml')
            sheets=[s for s in book.findall('m:sheets/m:sheet',NS) if s.get('name')=='実在庫']
            if len(sheets)!=1:raise ValueError('「実在庫」シートが1つ必要です')
            rid=sheets[0].get('{'+REL+'}id')
            links=[r for r in xml('xl/_rels/workbook.xml.rels') if r.get('Id')==rid]
            if len(links)!=1 or links[0].get('TargetMode')=='External':raise ValueError('シート参照が不正です')
            target=links[0].get('Target','')
            name=target.lstrip('/') if target.startswith('/') else posixpath.normpath('xl/'+target)
            if not name.startswith('xl/worksheets/') or '..' in name.split('/') or '\\' in name:raise ValueError('シートの保存先が不正です')
            strings=[]
            if 'xl/sharedStrings.xml' in z.namelist():strings=[''.join(t.text or '' for t in si.findall('.//m:t',NS)) for si in xml('xl/sharedStrings.xml').findall('m:si',NS)]
            sheet=xml(name)
            if sheet.findall('m:mergeCells/m:mergeCell',NS):raise ValueError('結合セルのある実在庫シートは取り込めません')
            raw=[];seen_rows=set()
            for row in sheet.findall('m:sheetData/m:row',NS):
                index=int(row.get('r','0'))
                if index<=0 or index in seen_rows or len(seen_rows)>=10001:raise ValueError('Excelの行番号重複または1万行上限です')
                seen_rows.add(index);cells={}
                for c in row.findall('m:c',NS):
                    match=re.fullmatch(r'([A-Z]+)([1-9][0-9]*)',c.get('r',''))
                    if not match or int(match[2])!=index or match[1] in cells:raise ValueError('セル位置が不正・重複しています')
                    if c.find('m:f',NS) is not None:raise ValueError(f'{c.get("r")}: 数式の結果は確定数量として取り込めません')
                    kind=c.get('t','n');value=c.findtext('m:v',default='',namespaces=NS)
                    if kind=='s':
                        if not re.fullmatch('[0-9]+',value):raise ValueError('共有文字列の参照番号が不正です')
                        value=strings[int(value)]
                    elif kind=='inlineStr':value=''.join(t.text or '' for t in c.findall('.//m:t',NS))
                    elif kind not in ('n','str'):raise ValueError(f'{c.get("r")}: 未対応のセル型です')
                    cells[match[1]]=value.strip()
                if any(cells.values()):raw.append((index,cells))
            if not raw or raw[0][0]!=1 or [raw[0][1].get(chr(65+i),'') for i in range(11)]!=HEADERS:raise ValueError('実在庫シートの列名・順序が指定様式と一致しません')
            if any(v for c,v in raw[0][1].items() if c not in set('ABCDEFGHIJK')):raise ValueError('見出しは指定の11列にしてください')
            groups={};errors=[];details=[];codes=set()
            for index,cells in raw[1:]:
                try:
                    if any(v for c,v in cells.items() if c not in set('ABCDEFGHIJK')):raise ValueError('指定の11列以外にデータがあります')
                    jan=cells.get('D','')
                    if not re.fullmatch('[0-9]{13}',jan):raise ValueError('JANCDは半角13桁が必要です。桁を補完しません')
                    code=cells.get('A','')
                    if not code or not cells.get('B'):raise ValueError('品番コード・品番名が必要です')
                    if code in codes:raise ValueError('品番コードが重複しています。同じ行を二重に合算しません')
                    codes.add(code)
                    state=cells.get('J','');quantity=cells.get('H','')
                    if state not in ('入力済み','未入力'):raise ValueError('状態は「入力済み」または「未入力」です')
                    if (state=='入力済み')!=bool(quantity):raise ValueError('状態と実在庫数の入力有無が一致しません')
                    q=_integer(quantity) if quantity else None
                    detail=dict(row=index,product=jan,product_code=code,name=cells['B'],spec=cells.get('C',''),quantity_p=q,status=state,input_time=cells.get('K',''),case_p=cells.get('E',''),ball_p=cells.get('F',''),cases_per_pallet=cells.get('G',''),packing=cells.get('I',''))
                    details.append(detail);groups.setdefault(jan,[]).append(detail)
                except ValueError as exc:errors.append(f'実在庫!{index}行 / JAN {cells.get("D","未確認")}: {exc}')
            rows=[];warnings=[]
            for jan,parts in sorted(groups.items()):
                known=[p['quantity_p'] for p in parts if p['quantity_p'] is not None]
                complete=len(known)==len(parts)
                total=sum(known) if known else None
                if total is not None and total>999999999999999:errors.append(jan+': JAN合算後のパック数が上限を超えています')
                if not complete:warnings.append(jan+': 未入力行あり・出荷可能数量は未確認')
                rows.append(dict(product=jan,stock_p=None,reserved_p=None,available_p=total if complete else None,entered_p=total,source_rows=[p['row'] for p in parts]))
            if not rows and not errors:errors.append('商品行がありません')
            return dict(source_format=FORMAT,quantity_basis='available_p',rows=rows,details=details,warnings=warnings,errors=errors,source_sha256=hashlib.sha256(content).hexdigest(),source_base64=base64.b64encode(content).decode('ascii'))
    except (ValueError,KeyError,IndexError,ET.ParseError,zipfile.BadZipFile,RuntimeError) as exc:
        raise DataError(['乾麺在庫Excelを読み取れません: '+str(exc)]) from exc
