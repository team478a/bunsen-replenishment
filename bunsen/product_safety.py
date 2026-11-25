"""Optional per-warehouse/JAN safety-day overrides with immutable proofs."""
import csv,io,json,re
from .core import DataError,WAREHOUSES,stamp

FILE='product-safety.csv'
HEADERS='revision,values_json,operator,reason,saved_at'

def empty():return {w:{} for w in WAREHOUSES}

def validate(values):
    if not isinstance(values,dict) or set(values)!=set(WAREHOUSES):raise DataError(['商品別安全在庫の倉庫番号が不正です'])
    for w,items in values.items():
        if not isinstance(items,dict):raise DataError(['商品別安全在庫の形式が不正です'])
        for p,days in items.items():
            if not isinstance(p,str) or not re.fullmatch('[0-9]{13}',p) or type(days) is not int or days<0:raise DataError(['商品別安全在庫は13桁JANと0以上の整数日数です'])
    return values

def proof(state):
    from .requirements_data import csv_text
    return csv_text(HEADERS,[dict(revision=state['revision'],values_json=json.dumps(state['values'],ensure_ascii=False,sort_keys=True),operator=state['operator'],reason=state['reason'],saved_at=state['saved_at'])])

def parse(text):
    reader=csv.DictReader(io.StringIO(text),strict=True);rr=list(reader)
    if reader.fieldnames!=HEADERS.split(',') or len(rr)!=1 or None in rr[0] or any(v is None for v in rr[0].values()):raise DataError(['商品別安全在庫の証跡が不正です'])
    r=rr[0]
    if not r['revision'].isascii() or not r['revision'].isdecimal():raise DataError(['商品別安全在庫の版が不正です'])
    state=dict(revision=int(r['revision']),values=validate(json.loads(r['values_json'])),operator=r['operator'],reason=r['reason'],saved_at=r['saved_at'])
    if state['revision']==0:
        if state['values']!=empty() or any(state[k] for k in ('operator','reason','saved_at')):raise DataError(['未設定の安全在庫証跡が不正です'])
    else:
        if any(not state[k].strip() or any(ord(c)<32 for c in state[k]) for k in ('operator','reason')):raise DataError(['商品別安全在庫の確認者・理由が必要です'])
        stamp(state['saved_at'])
    if proof(state)!=text:raise DataError(['商品別安全在庫の証跡形式が不一致です'])
    return state

def known_products(service,data=None):
    result={p['product']:p['name'] for p in service.jan_master()['products']}
    raw=data.raw if data is not None else None
    if raw is None:
        event=service.db.execute("SELECT payload FROM events WHERE kind='import' ORDER BY id DESC LIMIT 1").fetchone()
        raw=json.loads(event[0]).get('raw',{}) if event else {}
    if 'requirements.csv' in raw and 'products.csv' in raw:
        excluded=service.jan_master()['excluded']
        for r in csv.DictReader(io.StringIO(raw['products.csv'])):
            if re.fullmatch('[0-9]{13}',r.get('product','')) and r['product'] not in excluded:result.setdefault(r['product'],r.get('name',r['product']))
    return result

def validate_change(payload,revision,values):
    after=validate(payload.get('after'));p=payload.get('product');days=payload.get('days')
    if not isinstance(p,str) or not re.fullmatch('[0-9]{13}',p) or not isinstance(days,dict) or set(days)!=set(WAREHOUSES):raise DataError(['商品別安全在庫の変更対象が不正です'])
    expected={w:dict(items) for w,items in values.items()}
    for w,value in days.items():
        if value is None:expected[w].pop(p,None)
        elif type(value) is int and value>=0:expected[w][p]=value
        else:raise DataError(['安全在庫日数は0以上の整数、共通へ戻す場合は未設定です'])
    if payload.get('before')!=values or payload.get('base_revision')!=revision or type(payload.get('base_revision')) is not int or payload.get('acknowledged') is not True or after!=expected or any(not isinstance(payload.get(k),str) or not payload[k].strip() or any(ord(c)<32 for c in payload[k]) for k in ('operator','reason')):raise DataError(['商品別安全在庫の変更履歴が不整合です'])
    return after

class ProductSafety:
    def product_safety(self):
        row=self.db.execute("SELECT id,at,payload FROM events WHERE kind='product_safety_change' ORDER BY id DESC LIMIT 1").fetchone()
        if not row:return dict(revision=0,values=empty(),operator='',reason='',saved_at='')
        p=json.loads(row[2]);return dict(revision=row[0],values=p['after'],operator=p['operator'],reason=p['reason'],saved_at=row[1])

    def save_product_safety(self,product,days,operator,reason,acknowledged,expected_revision):
        from .service import encode
        self.ensure_writable()
        with self.db:
            self.db.execute('BEGIN IMMEDIATE');before=self.product_safety()
            if type(expected_revision) is not int or expected_revision!=before['revision']:raise DataError(['商品別安全在庫が更新されています。画面を開き直してください'])
            if product not in known_products(self):raise DataError(['確認済みJANマスタまたは取込済み商品から選択してください'])
            after={w:dict(items) for w,items in before['values'].items()}
            if not isinstance(days,dict) or set(days)!=set(WAREHOUSES):raise DataError(['神戸・杉戸の商品別日数が必要です'])
            for w,d in days.items():
                if d is None:after[w].pop(product,None)
                else:after[w][product]=d
            payload=dict(product=product,days=days,before=before['values'],after=after,base_revision=expected_revision,operator=operator,reason=reason,acknowledged=acknowledged)
            validate_change(payload,before['revision'],before['values'])
            self.db.execute('INSERT INTO events(at,kind,payload) VALUES(?,?,?)',(self.clock().isoformat(),'product_safety_change',encode(payload)))
        return self.product_safety()

    def assert_product_safety_current(self,data):
        if 'common-settings.csv' not in data.raw:return
        state=self.product_safety()
        if (FILE in data.raw and parse(data.raw[FILE])!=state) or (FILE not in data.raw and state['revision']):raise DataError(['商品別安全在庫が更新されています。再取込・再計算してください'])
