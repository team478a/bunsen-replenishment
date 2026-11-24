"""Confirmed staging stock intake and quantity-only comparison; no allocation."""
import base64,csv,hashlib,io,json
from pathlib import Path
from datetime import date
from .core import DataError,stamp,WAREHOUSES
from .exact_quantity import number,canonical
from .requirements_data import csv_text

HEADERS=('JAN','実在庫P','他出荷向け取置P')
LIMIT=5*1024*1024

def template():return csv_text(','.join(HEADERS),[])

def analyse(content):
    if not isinstance(content,bytes) or len(content)>LIMIT:raise DataError(['乾麺倉庫CSVは5MB以下にしてください'])
    try:text=content.decode('utf-8-sig')
    except UnicodeError:
        try:text=content.decode('cp932')
        except UnicodeError as exc:raise DataError(['CSVはUTF-8またはCP932で保存してください']) from exc
    rows=[];errors=[];seen=set()
    try:
        reader=csv.DictReader(io.StringIO(text),strict=True)
        if reader.fieldnames!=list(HEADERS):raise DataError(['乾麺倉庫CSVの列は '+','.join(HEADERS)+' です。ひな形を使用してください'])
        for index,row in enumerate(reader,1):
            if index>10000:raise DataError(['CSVは1万行以下にしてください'])
            try:
                if None in row or any(v is None for v in row.values()):raise ValueError('列数が不正です')
                jan=row['JAN'].strip()
                if not jan.isascii() or not jan.isdecimal() or len(jan)!=13:raise ValueError('JANは半角13桁です')
                if jan in seen:raise ValueError('JANが重複しています')
                seen.add(jan);quantities=[]
                for key in HEADERS[1:]:
                    value=row[key].strip()
                    if not value.isascii() or not value.isdecimal() or len(value)>15:raise ValueError(key+': 0以上の整数Pを明示してください。空欄は0にしません')
                    quantities.append(int(value))
                stock,reserved=quantities
                if reserved>stock:raise ValueError('他出荷向け取置Pが実在庫Pを超えています')
                rows.append(dict(product=jan,stock_p=stock,reserved_p=reserved))
            except ValueError as exc:errors.append(f'行{reader.line_num} / JAN {row.get("JAN","未確認")}: {exc}')
    except csv.Error as exc:raise DataError(['CSVの構造が不正です: '+str(exc)]) from exc
    if not rows and not errors:errors.append('在庫行がありません')
    return dict(rows=rows,errors=errors,source_sha256=hashlib.sha256(content).hexdigest(),source_base64=base64.b64encode(content).decode('ascii'))

def read_csv(path):
    path=Path(path)
    if path.stat().st_size>LIMIT:raise DataError(['乾麺倉庫CSVは5MB以下にしてください'])
    content=path.read_bytes();report=analyse(content)
    if path.read_bytes()!=content:raise DataError(['読取中にCSVが変更されました'])
    return dict(report,path=str(path.resolve()))

def read_stock(path):
    if Path(path).suffix.lower()=='.csv':return read_csv(path)
    if Path(path).suffix.lower()!='.xlsx':raise DataError(['実在庫はCSVまたは.xlsxを選択してください'])
    from .dry_stock_excel import analyse_excel
    path=Path(path)
    if path.stat().st_size>LIMIT:raise DataError(['乾麺在庫Excelは5MB以下にしてください'])
    content=path.read_bytes();report=analyse_excel(content)
    if path.read_bytes()!=content:raise DataError(['読取中にExcelが変更されました'])
    return dict(report,path=str(path.resolve()))

def available_p(row):
    return row['available_p'] if 'available_p' in row else row['stock_p']-row['reserved_p']

def checked_report(report):
    if not isinstance(report,dict) or not isinstance(report.get('source_base64'),str) or len(report['source_base64'])>LIMIT*2:raise DataError(['実在庫の原本証跡が不正です'])
    from .dry_stock_excel import FORMAT,analyse_excel
    if 'source_format' in report and report['source_format']!=FORMAT:raise DataError(['未対応の実在庫原本形式です'])
    try:fresh=(analyse_excel if report.get('source_format')==FORMAT else analyse)(base64.b64decode(report['source_base64'],validate=True))
    except (ValueError,TypeError) as exc:raise DataError(['実在庫の原本証跡が不正です']) from exc
    if any(report.get(k)!=v for k,v in fresh.items()):raise DataError(['実在庫の読取結果が原本と不一致です'])
    if fresh['errors']:raise DataError(fresh['errors'])
    return fresh

def validate_import(payload,at):
    checked_report(payload['report'])
    if not isinstance(payload.get('operator'),str) or not payload['operator'].strip() or any(ord(c)<32 for c in payload['operator']):raise DataError(['実在庫の確認者を入力してください'])
    if stamp(payload['as_of'])>stamp(at):raise DataError(['実在庫の基準日時が未来です'])
    if payload.get('acknowledged') is not True:raise DataError(['確認済み実在庫としての保存操作が必要です'])

def compare(stock,planned,dispatch,at):
    day=date.fromisoformat(dispatch);stamp(at)
    if stock:checked_report(stock['report'])
    if not isinstance(planned,list) or not planned:raise DataError(['照合する指示明細がありません'])
    inventory={r['product']:r for r in stock['report']['rows']} if stock else {}
    same_day=stock is not None and stamp(stock['as_of']).date()==day and stamp(stock['as_of'])<=stamp(at)
    totals={};seen=set()
    for line in planned:
        w,p=line['warehouse'],line['product'];q=line['quantity_p'];key=(w,p)
        if w not in WAREHOUSES or key in seen or type(q) is not int or q<0:raise DataError(['照合する指示明細が不正・重複しています'])
        if not isinstance(p,str) or not p.isascii() or not p.isdecimal() or len(p)!=13 or type(line['required_p']) not in (int,str) or number(line['required_p'])<0:raise DataError(['照合するJAN・必要量が不正です'])
        seen.add(key);entry=totals.setdefault(p,dict(product=p,required_p=0,quantity_p=0,KOBE=0,SUGITO=0))
        entry['required_p']+=number(line['required_p']);entry['quantity_p']+=q;entry[w]+=q
    result=[]
    for p,total in sorted(totals.items()):
        row=inventory.get(p);usable=available_p(row) if row else None
        valid=same_day and row is not None and usable is not None
        shortage=max(0,total['quantity_p']-usable) if valid else None
        status=('不足' if shortage else '数量上は充足') if valid else ('在庫未取込・確認不能' if not stock else 'JAN未掲載・確認不能' if row is None else '出荷可能数量未確認' if usable is None else '基準日違い・確認不能')
        result.append(dict(total,stock_p=row['stock_p'] if row else None,reserved_p=row['reserved_p'] if row else None,usable_p=usable,shortage_p=shortage,status=status))
    return canonical(result)

def validate_check(payload,stock,at):
    digest=payload.get('data_digest');operator=payload.get('operator')
    if (not isinstance(digest,str) or len(digest)!=64 or any(c not in '0123456789abcdef' for c in digest)
        or type(payload.get('stock_revision')) is not int or payload['stock_revision']!=(stock['revision'] if stock else 0)
        or not isinstance(operator,str) or not operator.strip() or any(ord(c)<32 for c in operator)
        or stamp(payload['at'])>stamp(at) or compare(stock,payload['planned'],payload['dispatch'],payload['at'])!=payload['rows']):raise DataError(['乾麺倉庫の照合履歴が不整合です。最新の数量で照合し直してください'])

class DryStockIntake:
    def dry_stock(self):
        row=self.db.execute("SELECT id,payload FROM events WHERE kind='dry_stock_import' ORDER BY id DESC LIMIT 1").fetchone()
        return dict(revision=row[0],**json.loads(row[1])) if row else None
    def save_dry_stock(self,report,as_of,operator,acknowledged,expected_revision):
        from .service import encode
        self.ensure_writable();fresh=checked_report(report)
        if not isinstance(operator,str) or not operator.strip():raise DataError(['実在庫の確認者を入力してください'])
        if report.get('path') and read_stock(report['path'])['source_sha256']!=fresh['source_sha256']:raise DataError(['元の実在庫ファイルが変更されています。再読取してください'])
        payload=dict(report=fresh,as_of=stamp(as_of).isoformat(),operator=operator.strip(),acknowledged=acknowledged,base_revision=expected_revision)
        validate_import(payload,self.clock().isoformat())
        with self.db:
            self.db.execute('BEGIN IMMEDIATE');state=self.dry_stock()
            if type(expected_revision) is not int or expected_revision!=(state['revision'] if state else 0):raise DataError(['実在庫が更新されています。取込画面を開き直してください'])
            self.db.execute('INSERT INTO events(at,kind,payload) VALUES(?,?,?)',(self.clock().isoformat(),'dry_stock_import',encode(payload)))
        return self.dry_stock()
    def dry_stock_comparison(self,data,lines,dispatch):
        from .replenishment import overlay
        from .core import calculate
        from .requirements_data import active
        self.assert_product_master_current(data)
        if not active(data):raise DataError(['乾麺倉庫の数量照合はJAN・物理PL方式で使用してください'])
        fresh=self.snapshot_data(data.raw,data.digest)
        checked=overlay(calculate(fresh,dispatch,allow_provisional=any(r.get('allow_provisional') for r in lines)),lines)
        planned=[{k:r[k] for k in ('warehouse','product','required_p','quantity_p')} for r in checked]
        stock=self.dry_stock();at=self.clock().isoformat()
        return dict(data_digest=data.digest,dispatch=dispatch.isoformat(),planned=planned,stock_revision=stock['revision'] if stock else 0,at=at,rows=compare(stock,planned,dispatch.isoformat(),at))
    def save_dry_stock_comparison(self,report,operator):
        from .service import encode
        self.ensure_writable()
        if not isinstance(operator,str) or not operator.strip():raise DataError(['照合結果を保存する担当者を入力してください'])
        with self.db:
            self.db.execute('BEGIN IMMEDIATE');stock=self.dry_stock()
            if report['stock_revision']!=(stock['revision'] if stock else 0):raise DataError(['実在庫が変更されています。照合画面を開き直してください'])
            payload=dict(report,operator=operator.strip());at=self.clock().isoformat();validate_check(payload,stock,at)
            self.db.execute('INSERT INTO events(at,kind,payload) VALUES(?,?,?)',(at,'dry_stock_check',encode(payload)))
