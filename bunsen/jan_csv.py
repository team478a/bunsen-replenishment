"""Explicit six-column JAN master interchange; no inferred business values."""
import base64
import csv
import hashlib
import io
from pathlib import Path
from .core import DataError
from .jan_catalog import FIELDS, validate_jan
from .requirements_data import csv_text
from .input_guard import _signature

HEADERS=('JAN','品名','期限種別','製造からの期限日数','神戸1PLのP数','杉戸1PLのP数')
LABELS=dict(zip(FIELDS,HEADERS))
LIMIT=5*1024*1024

def export_csv(products):
    return csv_text(','.join(HEADERS),[{LABELS[k]:({'best_before':'賞味期限','use_by':'消費期限'}.get(p[k],p[k]) if k=='expiry_type' else p[k]) for k in FIELDS} for p in products])

def analyse(content):
    if len(content)>LIMIT:raise DataError(['JANマスタCSVは5MB以下にしてください'])
    try:text=content.decode('utf-8-sig');encoding='UTF-8'
    except UnicodeError:
        try:text=content.decode('cp932');encoding='CP932'
        except UnicodeError as exc:raise DataError(['JANマスタCSVはUTF-8またはCP932で保存してください']) from exc
    result=dict(source_sha256=hashlib.sha256(content).hexdigest(),source_base64=base64.b64encode(content).decode('ascii'),encoding=encoding,products=[],errors=[])
    try:
        reader=csv.DictReader(io.StringIO(text),strict=True);headers=reader.fieldnames
        if not headers or len(set(headers))!=len(headers):raise DataError(['JANマスタCSVの列名が欠損・重複しています'])
        if set(headers)==set(HEADERS):mapping=dict(zip(HEADERS,FIELDS))
        elif set(headers)==set(FIELDS):mapping={k:k for k in FIELDS}
        else:raise DataError(['JANマスタCSVの列は '+', '.join(HEADERS)+' です。ひな形を使用してください'])
        seen=set()
        for index,row in enumerate(reader,1):
            if index>10000:raise DataError(['JANマスタCSVは1万行以下にしてください'])
            try:
                if None in row or any(v is None for v in row.values()):raise DataError(['列数が不正です'])
                values={mapping[k]:v.strip() for k,v in row.items()}
                if values['product'] in seen:raise DataError(['同じCSV内でJANが重複しています'])
                seen.add(values['product'])
                values['expiry_type']={'賞味期限':'best_before','消費期限':'use_by'}.get(values['expiry_type'],values['expiry_type'])
                for key in FIELDS[3:]:
                    if not values[key].isascii() or not values[key].isdecimal():raise DataError([LABELS[key]+': 正整数を入力してください（欠損の補完不可）'])
                    values[key]=int(values[key])
                result['products'].append(validate_jan(values))
            except (DataError,ValueError) as exc:
                result['errors'].append(f'行{reader.line_num} / JAN {row.get("JAN",row.get("product","未確認"))}: '+str(exc))
    except csv.Error as exc:raise DataError(['JANマスタCSVの構造が不正です: '+str(exc)]) from exc
    if not result['products'] and not result['errors']:result['errors'].append('登録行がありません')
    return result

def read_csv(path):
    path=Path(path);before=_signature(path)
    if path.stat().st_size>LIMIT:raise DataError(['JANマスタCSVは5MB以下にしてください'])
    content=path.read_bytes()
    if before!=_signature(path) or path.read_bytes()!=content:raise DataError(['読取中にJANマスタCSVが変更されました'])
    result=analyse(content);result['path']=str(path.resolve());return result
