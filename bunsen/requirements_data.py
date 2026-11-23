"""JAN/physical-pallet requirements contract; historical v2 remains unchanged."""
import base64
import csv
import hashlib
import io
import json
from datetime import date
from pathlib import Path

from .core import DataError, Dataset, ValidationIssue, WAREHOUSES, stamp
from .input_guard import InputGuard
from .exact_quantity import decimal_quantity

# A 20MB PDF evidence field can exceed the csv module's 128KB default.
csv.field_size_limit(32*1024*1024)

PROFILE = 'jan_physical_pl_no_inbound'
NOTICE = 'ムロオ必要量・物理PL単位。確定入庫・輸送中数量は計算対象外（確認済み0件ではありません）。供給可能量・実出荷実績ではありません。'
SCHEMA = {
    'products': 'product,name,expiry_type,shelf_life_days,confirmed',
    'pallets': 'warehouse,product,p_per_pl,confirmed',
    'inventory': 'warehouse,product,quantity_p,as_of,basis,source_pdf,source_sha256,checker,confirmed',
    'forecast': 'warehouse,product,date,quantity_p,as_of,order_basis,status',
    'requirements': 'warehouse,product,cycle_days,lead_days,safety_days,coverage_days,confirmed',
}
EVIDENCE = 'source_pdf,sha256,content_base64'
KEYS = {'products': ('product',), 'pallets': ('warehouse','product'),
        'inventory': ('warehouse','product'), 'forecast': ('warehouse','product','date'),
        'requirements': ('warehouse','product')}


def csv_text(fields, rows):
    out = io.StringIO(newline='')
    writer = csv.DictWriter(out, fieldnames=fields.split(','), lineterminator='\n')
    writer.writeheader(); writer.writerows(rows)
    return out.getvalue()


def active(data):
    return 'requirements.csv' in data.raw


def pallet_p(data, warehouse, product):
    return next(r['p_per_pl'] for r in data.tables['pallets']
                if (r['warehouse'],r['product']) == (warehouse,product))


def line_units(row, product):
    if row.get('rounding') == 'ceil_physical_pl' or row.get('pl_only'):
        return f'{row["quantity_p"] // row.get("unit_p",row.get("p_per_pl",1))} PL（物理）'
    from .core import units
    return units(row['quantity_p'], product)


class SourceGuard(InputGuard):
    def _inventory(self):
        result = super()._inventory()
        from .input_guard import _signature
        result.update({p.name: _signature(p) for p in self.folder.iterdir() if p.suffix.lower()=='.pdf'})
        return result


def inspect(folder, now, master_csvs=None):
    from .ingest import Inspection
    folder = Path(folder)
    errors, files, raw, tables, locations = [], [], {}, {}, {}
    def error(table, field, message, row=None, line=None):
        r = row or {}
        errors.append(ValidationIssue(table+'.csv',field,'REQUIREMENTS_INPUT',message,
                      line=line,warehouse=r.get('warehouse'),product=r.get('product'),date=r.get('date') or r.get('as_of')))
    ignored = {'transit.csv','dry_stock.csv','vehicles.csv','warehouses.csv','policies.csv','replenishment.csv'}
    replacements = master_csvs or {}
    ignored.update(replacements)
    if set(replacements)-{'products.csv','pallets.csv','requirements.csv','common-settings.csv','product-scope.csv'}:
        raise DataError(['登録JANマスタの置換対象が不正です'])
    try:
        guard = SourceGuard(folder, ignored)
        from .forecast_csv import REVIEW_FILE, verify_review
        expected_forecast=None
        if (folder/REVIEW_FILE).exists():
            proof=(folder/REVIEW_FILE).read_bytes();guard.remember(folder/REVIEW_FILE,proof)
            raw[REVIEW_FILE]=proof.decode('utf-8-sig').replace('\r\n','\n').replace('\r','\n')
            try:expected_forecast,_=verify_review(raw[REVIEW_FILE])
            except (ValueError,TypeError,KeyError,csv.Error) as exc:error('forecast-review','proof',str(exc))
        from .common_settings import FILE,parse,requirements
        from .product_scope import FILE as SCOPE_FILE,parse as parse_scope
        scope=None
        if SCOPE_FILE in replacements or (folder/SCOPE_FILE).exists():
            content=replacements[SCOPE_FILE].encode('utf-8') if SCOPE_FILE in replacements else (folder/SCOPE_FILE).read_bytes()
            if SCOPE_FILE not in replacements:guard.remember(folder/SCOPE_FILE,content)
            raw[SCOPE_FILE]=content.decode('utf-8-sig').replace('\r\n','\n').replace('\r','\n');scope=parse_scope(raw[SCOPE_FILE])
        common=None
        if FILE in replacements or (folder/FILE).exists():
            content=replacements[FILE].encode('utf-8') if FILE in replacements else (folder/FILE).read_bytes()
            if FILE not in replacements:guard.remember(folder/FILE,content)
            raw[FILE]=content.decode('utf-8-sig').replace('\r\n','\n').replace('\r','\n')
            common=parse(raw[FILE])
            if stamp(common['saved_at'])>now:raise ValueError('共通条件の保存日時が未来です')
        allowed = {k+'.csv' for k in SCHEMA} | {'source_evidence.csv',REVIEW_FILE,FILE,SCOPE_FILE}
        for filename in guard.initial:
            if filename.endswith('.csv') and filename not in allowed:
                guard.remember(folder/filename,(folder/filename).read_bytes())
                error(filename[:-4],None,'未対応CSVです')
        for table, fields in SCHEMA.items():
            path=folder/(table+'.csv'); tables[table]=[]; locations[table]={}
            info=dict(filename=path.name,exists=path.exists() or path.name in replacements,expected=True,
                      optional=False,headers=None,rows=0,sha256=None,bytes=None,basis_times={},status='missing')
            files.append(info)
            try:
                content=(replacements[path.name].encode('utf-8') if path.name in replacements else path.read_bytes())
                if path.name not in replacements:guard.remember(path,content)
                text=content.decode('utf-8-sig').replace('\r\n','\n').replace('\r','\n')
                raw[path.name]=text
                info.update(bytes=len(content),sha256=hashlib.sha256(content).hexdigest(),status='checked')
                reader=csv.DictReader(io.StringIO(text),strict=True);info['headers']=reader.fieldnames
                if reader.fieldnames != fields.split(','):
                    error(table,'headers','JAN・物理PL方式の列名・順序と一致しません');info['status']='header_error';continue
                seen=set()
                for source in reader:
                    line=reader.line_num;info['rows']+=1;before=len(errors)
                    if None in source or any(v is None for v in source.values()):
                        error(table,'columns','列数が不正です',line=line);continue
                    row={k:v.strip() for k,v in source.items()};converted=dict(row)
                    for field,value in row.items():
                        if table=='products' and field in ('expiry_type','shelf_life_days') and not row['expiry_type'] and not row['shelf_life_days']:
                            converted.pop(field,None);continue
                        try:
                            if not value:raise ValueError('欠損・未確認です（0補完不可）')
                            if any(ord(c)<32 for c in value):raise ValueError('制御文字は使用できません')
                            if field=='product' and (not value.isascii() or not value.isdecimal() or len(value)!=13):raise ValueError('13桁のJAN文字列が必要です')
                            if field in ('quantity_p','p_per_pl','cycle_days','lead_days','safety_days','coverage_days','shelf_life_days'):
                                if table=='forecast' and field=='quantity_p' and expected_forecast is not None:
                                    converted[field]=decimal_quantity(value);continue
                                if not value.isascii() or not value.isdecimal():raise ValueError('0以上の整数が必要です。丸め方は自動決定しません')
                                converted[field]=int(value)
                                if field not in ('quantity_p','lead_days','safety_days') and int(value)==0:raise ValueError('正整数が必要です')
                            if field=='as_of' and stamp(value)>now:raise ValueError('未来の基準日時です')
                            if field=='date' and date.fromisoformat(value).isoformat()!=value:raise ValueError('YYYY-MM-DDが必要です')
                            choices={'warehouse':WAREHOUSES,'confirmed':('yes',),'basis':('opening','closing'),
                                     'expiry_type':('best_before','use_by'),'order_basis':('not_added',) if expected_forecast is not None else ('included',),
                                     'status':('confirmed','provisional','imputed')}
                            if field in choices and value not in choices[field]:raise ValueError('未対応・未確認の区分です')
                            if field=='source_sha256' and (len(value)!=64 or any(c not in '0123456789abcdef' for c in value)):raise ValueError('SHA-256が不正です')
                            if field=='source_pdf' and (Path(value).name!=value or not value.lower().endswith('.pdf') or '/' in value or '\\' in value):raise ValueError('同じフォルダー内のPDFファイル名が必要です')
                        except (ValueError,OverflowError) as exc:error(table,field,str(exc),row,line)
                    key=tuple(row[k] for k in KEYS[table])
                    if key in seen:error(table,'+'.join(KEYS[table]),'重複しています',row,line)
                    seen.add(key)
                    if before==len(errors):tables[table].append(converted);locations[table][key]=line
            except (OSError,UnicodeError,csv.Error) as exc:
                info['status']='read_error';error(table,'file',f'読取不能: {exc}')
        if expected_forecast is not None and raw.get('forecast.csv')!=expected_forecast:
            error('forecast','quantity_p','予測CSVと確認済み原本・採用列・倉庫対応が不一致です。再変換してください')
        if scope:
            for table,rows in tables.items():tables[table]=[r for r in rows if r['product'] not in scope['excluded']]
        products={r['product'] for r in tables['products']}
        if common is not None and raw.get('requirements.csv')!=requirements(common,products):error('requirements',None,'共通補充条件と計算用の条件が不一致です')
        if not products:error('products','product','商品がありません')
        for table,rows in tables.items():
            for row in rows:
                if row['product'] not in products:error(table,'product','商品マスタにないJANです',row)
        for w in WAREHOUSES:
            for p in sorted(products):
                for table in ('inventory','requirements','pallets'):
                    if not any((r['warehouse'],r['product'])==(w,p) for r in tables[table]):
                        error(table,'warehouse+product','必須行がありません',dict(warehouse=w,product=p))
        embedded={}; evidence_path=folder/'source_evidence.csv'
        if evidence_path.exists():
            content=evidence_path.read_bytes();guard.remember(evidence_path,content)
            reader=csv.DictReader(io.StringIO(content.decode('utf-8-sig')),strict=True)
            if reader.fieldnames!=EVIDENCE.split(','):error('source_evidence','headers','PDF証跡の列が不正です')
            else:
                for r in reader:
                    if None in r or any(v is None for v in r.values()):raise ValueError('PDF証跡の列数が不正')
                    if len(r['content_base64'])>28*1024*1024:raise ValueError('PDF証跡が20MB上限を超えています')
                    name=r['source_pdf']
                    if name in embedded:raise ValueError('PDF証跡の重複')
                    content=base64.b64decode(r['content_base64'],validate=True)
                    if hashlib.sha256(content).hexdigest()!=r['sha256']:raise ValueError('PDF証跡ハッシュが不一致')
                    embedded[name]=content
        evidence=[];used={}
        for row in tables['inventory']:
            name=row['source_pdf']
            if name in used:
                if used[name]!=row['source_sha256']:error('inventory','source_sha256','同じPDF名のハッシュが不一致です',row)
                continue
            used[name]=row['source_sha256'];path=folder/name
            try:
                if path.exists():
                    content=path.read_bytes();guard.remember(path,content)
                    if name in embedded and content!=embedded[name]:raise ValueError('原本PDFと保存証跡が異なります')
                else:content=embedded[name]
                if len(content)>20*1024*1024 or not content.startswith(b'%PDF-'):raise ValueError('20MB以下のPDF原本が必要です')
                digest=hashlib.sha256(content).hexdigest()
                if digest!=row['source_sha256']:raise ValueError('PDF原本のSHA-256が不一致です')
                evidence.append(dict(source_pdf=name,sha256=digest,content_base64=base64.b64encode(content).decode('ascii')))
            except (OSError,KeyError,ValueError) as exc:error('inventory','source_pdf',f'PDF証跡を確認できません: {exc}',row)
        if set(embedded)-set(used):error('source_evidence','source_pdf','在庫に対応しないPDF証跡があります')
        raw['source_evidence.csv']=csv_text(EVIDENCE,sorted(evidence,key=lambda r:r['source_pdf']))
        observed,consistency=guard.finish();errors.extend(observed)
    except (OSError,ValueError,csv.Error,UnicodeError) as exc:
        error('requirements','file',f'入力またはPDF証跡が不正です: {exc}')
        consistency=dict(status='unverifiable',atomic_snapshot=False,changed_files=[],unverified_files=[str(folder)],method='before_after_names_metadata_and_bytes')
    digest=hashlib.sha256(json.dumps(raw,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    tables['replenishment']=tables.get('requirements',[])
    tables['transit']=[]  # Structural compatibility only; never displayed as confirmed zero.
    return Inspection(None if errors else Dataset(tables,raw,digest,locations,2),errors,files,[NOTICE],consistency)
