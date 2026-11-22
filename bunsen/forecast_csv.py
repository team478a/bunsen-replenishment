"""Review a base-pack forecast CSV. Bulk orders and receipts are never inputs."""
import base64
import csv
import hashlib
import io
import json
import re
from datetime import date
from pathlib import Path

from .core import DataError, WAREHOUSES, stamp
from .exact_quantity import decimal_quantity
from .input_guard import _signature

REVIEW_FILE='forecast-review.csv'
REVIEW_FIELDS='format,source_sha256,source_base64,mapping_json,operator,approved,scope,forecast_sha256'
FORMAT='bunsen-base-pack-forecast-v1'
SCOPE='base_forecast_only_bulk_orders_and_receipts_excluded'
QUANTITY='予測出荷数量（パック）'
REQUIRED=('JAN','倉庫ID','予測日',QUANTITY,'予測生成日時','予測区分','予測用途')
LIMIT=10*1024*1024


def analyse_bytes(content):
    if len(content)>LIMIT:raise DataError(['予測CSVは10MB以下にしてください'])
    try:text=content.decode('utf-8-sig')
    except UnicodeError as exc:raise DataError(['予測CSVはUTF-8で保存してください']) from exc
    result=dict(format=FORMAT,source_sha256=hashlib.sha256(content).hexdigest(),
                source_base64=base64.b64encode(content).decode('ascii'),rows=[],errors=[])
    reader=csv.DictReader(io.StringIO(text,newline=''),strict=True)
    if not reader.fieldnames or len(set(reader.fieldnames))!=len(reader.fieldnames):raise DataError(['予測CSVの列名が欠損・重複しています'])
    missing=set(REQUIRED)-set(reader.fieldnames)
    if missing:raise DataError(['予測CSVの必須列がありません: '+', '.join(sorted(missing))])
    seen=set();runs=set()
    try:
        for row in reader:
            if len(result['rows'])+len(result['errors'])>=100000:raise DataError(['予測CSVは10万行以下にしてください'])
            context=f"行{reader.line_num} 倉庫={row.get('倉庫ID','未確認')} JAN={row.get('JAN','未確認')} 日付={row.get('予測日','未確認')}"
            try:
                if None in row or any(v is None for v in row.values()):raise ValueError('列数が不正です')
                values={k:row[k].strip() for k in REQUIRED}
                for key,value in values.items():
                    if not value or any(ord(c)<32 for c in value):raise ValueError(key+': 欠損・制御文字があります（0補完不可）')
                jan=values['JAN'];warehouse=values['倉庫ID'];day=values['予測日']
                if not re.fullmatch(r'[0-9]{13}',jan):raise ValueError('JAN: 13桁の数字が必要です')
                if date.fromisoformat(day).isoformat()!=day:raise ValueError('予測日: YYYY-MM-DDが必要です')
                quantity=decimal_quantity(values[QUANTITY])
                generated=stamp(values['予測生成日時']).isoformat()
                kind=values['予測区分'];usage=values['予測用途']
                if kind not in ('通常予測','暫定予測','補完予測'):raise ValueError('予測区分が未対応です')
                if usage not in ('正式Production予測','参考予測'):raise ValueError('予測用途が未対応です')
                status=('confirmed' if kind=='通常予測' and usage=='正式Production予測' else 'imputed' if kind=='補完予測' else 'provisional')
                if row.get('商品・倉庫ID','').strip() and row['商品・倉庫ID'].strip()!=jan+'::'+warehouse:
                    raise ValueError('商品・倉庫IDとJAN/倉庫IDが一致しません')
                key=(warehouse,jan,day)
                if key in seen:raise ValueError('倉庫・JAN・日付が重複しています（合算不可）')
                seen.add(key)
                if '予測Run ID' in row:
                    run=row['予測Run ID'].strip()
                    if not run:raise ValueError('予測Run IDが空欄です')
                    runs.add(run)
                result['rows'].append(dict(source_warehouse=warehouse,product=jan,date=day,quantity_p=quantity,as_of=generated,status=status,line=reader.line_num))
            except (ValueError,TypeError) as exc:result['errors'].append(context+' / '+str(exc))
    except csv.Error as exc:raise DataError(['予測CSVの構造が不正です: '+str(exc)]) from exc
    if len(runs)>1:result['errors'].append('予測Run IDが混在しています。1回の予測出力を選んでください')
    if not result['rows']:result['errors'].append('有効な予測行がありません')
    result['source_codes']=sorted({r['source_warehouse'] for r in result['rows']})
    return result


def read_forecast(path):
    path=Path(path);before=_signature(path)
    if path.stat().st_size>LIMIT:raise DataError(['予測CSVは10MB以下にしてください'])
    content=path.read_bytes()
    if before!=_signature(path) or path.read_bytes()!=content:raise DataError(['読取中に予測CSVが変更されました'])
    result=analyse_bytes(content);result['path']=str(path.resolve());return result


def validate_mapping(report,mapping):
    if (not isinstance(mapping,dict) or set(mapping)!=set(report['source_codes']) or len(mapping)!=2
            or any(value not in WAREHOUSES for value in mapping.values()) or set(mapping.values())!=set(WAREHOUSES)):
        raise DataError(['倉庫コードの確認が必要です。元コードから神戸・杉戸への一意な対応を指定してください（初期値は未設定）'])


def make_outputs(report,mapping,operator):
    from .requirements_data import csv_text, SCHEMA
    if not isinstance(operator,str) or not operator.strip() or any(ord(c)<32 for c in operator):raise DataError(['予測を確認した担当者が必要です'])
    original=base64.b64decode(report['source_base64'],validate=True);fresh=analyse_bytes(original)
    if any(report.get(k)!=v for k,v in fresh.items()):raise DataError(['読取候補が変更されています。予測CSVを再読取してください'])
    if fresh['errors']:raise DataError(fresh['errors'])
    validate_mapping(fresh,mapping)
    rows=[dict(warehouse=mapping[r['source_warehouse']],product=r['product'],date=r['date'],quantity_p=r['quantity_p'],as_of=r['as_of'],order_basis='not_added',status=r['status']) for r in fresh['rows']]
    rows.sort(key=lambda r:(r['warehouse'],r['product'],r['date']))
    forecast=csv_text(SCHEMA['forecast'],rows)
    review=dict(format=FORMAT,source_sha256=fresh['source_sha256'],source_base64=fresh['source_base64'],
                mapping_json=json.dumps(mapping,ensure_ascii=False,sort_keys=True),operator=operator.strip(),approved='yes',scope=SCOPE,
                forecast_sha256=hashlib.sha256(forecast.encode('utf-8')).hexdigest())
    return {'forecast.csv':forecast,REVIEW_FILE:csv_text(REVIEW_FIELDS,[review])},review


def verify_review(text):
    reader=csv.DictReader(io.StringIO(text),strict=True)
    if reader.fieldnames!=REVIEW_FIELDS.split(','):raise ValueError('予測確認証跡の列が不正です')
    records=list(reader)
    if len(records)!=1 or None in records[0] or any(v is None for v in records[0].values()):raise ValueError('予測確認証跡は一意な1行が必要です')
    review=records[0]
    if len(review['source_base64'])>14*1024*1024:raise ValueError('予測原本が10MB上限を超えています')
    original=base64.b64decode(review['source_base64'],validate=True)
    if hashlib.sha256(original).hexdigest()!=review['source_sha256']:raise ValueError('予測原本のハッシュが不一致です')
    outputs,canonical=make_outputs(analyse_bytes(original),json.loads(review['mapping_json']),review['operator'])
    if review!=canonical:raise ValueError('採用列・確認・倉庫対応の証跡が不一致です')
    return outputs['forecast.csv'],review


def metadata(raw):
    if REVIEW_FILE not in raw:return None
    row=next(csv.DictReader(io.StringIO(raw[REVIEW_FILE])))
    return dict(quantity_column=QUANTITY,scope=SCOPE,mapping=json.loads(row['mapping_json']),
                operator=row['operator'],source_sha256=row['source_sha256'])
