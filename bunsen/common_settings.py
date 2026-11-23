"""Reviewed warehouse conditions; preserve legacy application-wide proofs."""
import csv,io,json
from .core import DataError,WAREHOUSES,LABELS,stamp

FIELDS=('cycle_days','lead_days','safety_days','coverage_days')
FILE='common-settings.csv'
HEADERS=('revision',)+FIELDS+('operator','reason','saved_at')
WAREHOUSE_HEADERS=('warehouse',)+HEADERS

def warehouse_specific(values):
    return isinstance(values,dict) and set(values)==set(WAREHOUSES)

def validate(values):
    if isinstance(values,dict) and set(values)&set(WAREHOUSES) and not warehouse_specific(values):raise DataError(['倉庫別補充条件にはKOBE（神戸）・SUGITO（杉戸）の両方が必要です'])
    if warehouse_specific(values):
        result={}
        for w in WAREHOUSES:
            try:
                if not isinstance(values[w],dict) or set(values[w])!=set(FIELDS):raise DataError(['補充条件の4項目が必要です'])
                result[w]=validate(values[w])
            except DataError as exc:raise DataError([LABELS[w]+' / '+w+': '+e for e in exc.errors]) from exc
        return result
    if not isinstance(values,dict) or set(values)!=set(FIELDS):raise DataError(['補充条件の4項目が必要です'])
    for key,value in values.items():
        if type(value) is not int or value<0 or (key in ('cycle_days','coverage_days') and value==0):raise DataError(['補充間隔・対象日数は1以上、配送日数・安全日数は0以上の整数です'])
    return dict(values)

def proof(state):
    from .requirements_data import csv_text
    if warehouse_specific(state['values']):
        return csv_text(','.join(WAREHOUSE_HEADERS),[dict(warehouse=w,revision=state['revision'],**state['values'][w],operator=state['operator'],reason=state['reason'],saved_at=state['saved_at']) for w in WAREHOUSES])
    return csv_text(','.join(HEADERS),[dict(revision=state['revision'],**state['values'],operator=state['operator'],reason=state['reason'],saved_at=state['saved_at'])])

def parse(text):
    reader=csv.DictReader(io.StringIO(text),strict=True);rows=list(reader)
    if reader.fieldnames==list(WAREHOUSE_HEADERS):
        if len(rows)!=len(WAREHOUSES) or [r.get('warehouse') for r in rows]!=list(WAREHOUSES):raise DataError(['倉庫別補充条件の倉庫番号に欠損・重複・不一致があります'])
        from .requirements_data import csv_text
        states=[parse(csv_text(','.join(HEADERS),[{k:r[k] for k in HEADERS}])) for r in rows]
        base=states[0]
        if any(any(s[k]!=base[k] for k in ('revision','operator','reason','saved_at')) for s in states):raise DataError(['倉庫別補充条件の版・確認記録が一致しません'])
        state=dict(base,values={w:s['values'] for w,s in zip(WAREHOUSES,states)})
        if proof(state)!=text:raise DataError(['倉庫別補充条件の証跡形式が不一致です'])
        return state
    if reader.fieldnames!=list(HEADERS) or len(rows)!=1 or None in rows[0] or any(v is None for v in rows[0].values()):raise DataError(['共通条件の保存証跡が不正です'])
    row=rows[0]
    for key in ('revision',)+FIELDS:
        if not row[key].isascii() or not row[key].isdecimal():raise DataError(['共通条件の数値証跡が不正です'])
    values=validate({k:int(row[k]) for k in FIELDS})
    if int(row['revision'])<=0 or any(not row[k].strip() or any(ord(c)<32 for c in row[k]) for k in ('operator','reason')):raise DataError(['共通条件の確認記録が不正です'])
    stamp(row['saved_at'])
    state=dict(revision=int(row['revision']),values=values,operator=row['operator'],reason=row['reason'],saved_at=row['saved_at'])
    if proof(state)!=text:raise DataError(['共通条件の証跡形式が不一致です'])
    return state

def requirements(state,products):
    from .requirements_data import SCHEMA,csv_text
    if warehouse_specific(state['values']):
        return csv_text(SCHEMA['requirements'],[dict(warehouse=w,product=p,**state['values'][w],confirmed='yes') for w in WAREHOUSES for p in sorted(products)])
    return csv_text(SCHEMA['requirements'],[dict(warehouse=w,product=p,**state['values'],confirmed='yes') for w in WAREHOUSES for p in sorted(products)])

class CommonSettings:
    def common_settings(self):
        row=self.db.execute("SELECT id,at,payload FROM events WHERE kind='common_settings_change' ORDER BY id DESC LIMIT 1").fetchone()
        if not row:return dict(revision=0,values=None)
        p=json.loads(row[2]);return dict(revision=row[0],values=p['after'],operator=p['operator'],reason=p['reason'],saved_at=row[1])

    def save_common_settings(self,values,operator,reason,acknowledged,expected_revision):
        from .service import encode
        self.ensure_writable();values=validate(values)
        if acknowledged is not True or any(not isinstance(v,str) or not v.strip() or any(ord(c)<32 for c in v) for v in (operator,reason)):raise DataError(['担当者・設定理由・確認保存が必要です'])
        with self.db:
            self.db.execute('BEGIN IMMEDIATE');before=self.common_settings()
            if type(expected_revision) is not int or before['revision']!=expected_revision:raise DataError(['共通条件が更新されています。画面を開き直してください'])
            self.db.execute('INSERT INTO events(at,kind,payload) VALUES(?,?,?)',(self.clock().isoformat(),'common_settings_change',encode(dict(before=before['values'],after=values,base_revision=before['revision'],operator=operator.strip(),reason=reason.strip(),acknowledged=True))))
        return self.common_settings()

    def assert_common_settings_current(self,data):
        if FILE in data.raw:
            state=parse(data.raw[FILE])
            if state!=self.common_settings():raise DataError(['補充条件が更新されています。データを再取込・再計算してください'])

    def import_common_folder(self,folder,use_registered_jan):
        from pathlib import Path
        from .requirements_data import inspect
        from .jan_catalog import master_csvs
        folder=Path(folder);state=self.common_settings()
        if state['values'] is None:raise DataError(['先に「補充条件」で共通条件を入力して保存してください。補充条件CSVは不要です'])
        replacements={};master=None
        if use_registered_jan:
            master=self.jan_master()
            if not master['products']:raise DataError(['先にJAN・倉庫別PLマスタを登録してください'])
            replacements.update(master_csvs(master['products']));products=[p['product'] for p in master['products']]
        else:
            with (folder/'products.csv').open(encoding='utf-8-sig',newline='') as f:products=[r['product'] for r in csv.DictReader(f)]
        scope=self.jan_master()
        if scope['excluded']:
            from .product_scope import FILE as SCOPE_FILE,text
            products=[p for p in products if p not in scope['excluded']]
            replacements[SCOPE_FILE]=text(scope)
        replacements.update({'requirements.csv':requirements(state,products),FILE:proof(state)})
        checked=inspect(folder,self.clock(),replacements)
        if checked.issues:raise DataError(checked.issues)
        data=checked.dataset
        if master:data.jan_master_revision=master['revision'];self.assert_jan_current(data)
        self.assert_common_settings_current(data)
        self.assert_jan_current(data)
        return data
