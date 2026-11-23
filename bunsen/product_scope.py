"""Explicit inactive JANs; absence from a PDF never implies zero stock."""
import csv,io,json,re
from .core import DataError

FILE='product-scope.csv'
HEADERS='revision,excluded_json'

def validate(excluded,known=None):
    if not isinstance(excluded,dict):raise DataError(['対象外JANの形式が不正です'])
    for jan,detail in excluded.items():
        if not isinstance(jan,str) or not re.fullmatch(r'[0-9]{13}',jan) or (known is not None and jan not in known):raise DataError(['対象外JANがマスタにありません'])
        if not isinstance(detail,dict) or set(detail)!= {'operator','reason'} or any(not isinstance(v,str) or not v.strip() or any(ord(c)<32 for c in v) for v in detail.values()):raise DataError(['対象外にした担当者・理由が必要です'])
    return excluded

def text(state):
    from .requirements_data import csv_text
    return csv_text(HEADERS,[dict(revision=state['revision'],excluded_json=json.dumps(state['excluded'],ensure_ascii=False,sort_keys=True))])

def parse(content):
    reader=csv.DictReader(io.StringIO(content),strict=True);rows=list(reader)
    if reader.fieldnames!=HEADERS.split(',') or len(rows)!=1 or None in rows[0] or any(v is None for v in rows[0].values()):raise DataError(['対象外JANの証跡が不正です'])
    row=rows[0]
    if not row['revision'].isascii() or not row['revision'].isdecimal() or int(row['revision'])<=0:raise DataError(['対象JANの版が不正です'])
    state=dict(revision=int(row['revision']),excluded=validate(json.loads(row['excluded_json'])))
    if text(state)!=content:raise DataError(['対象外JANの証跡形式が不一致です'])
    return state

def csv_excluded(before,incoming,operator,reason,mode):
    if mode not in ('merge','replace'):raise DataError(['CSV登録方式が不正です'])
    if mode=='merge':return {p:d for p,d in before['excluded'].items() if p not in incoming}
    return {p['product']:before['excluded'].get(p['product'],dict(operator=operator.strip(),reason=reason.strip())) for p in before['all_products'] if p['product'] not in incoming}
