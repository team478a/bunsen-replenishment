"""Shared strict CSV inspection. Invalid values never become a Dataset."""
import csv
from dataclasses import dataclass
import hashlib
import io
import json
from pathlib import Path
from datetime import date

from .core import (DATES, KEYS, NUMBERS, POSITIVE, SCHEMA, TIMESTAMPS, WAREHOUSES,
                   Dataset, ValidationIssue, stamp)

ENUMS = {'ship_unit': ('PL', 'CASE'), 'rounding': ('ceil', 'floor'),
         'expiry_basis': ('shipment', 'arrival'), 'order_basis': ('included',),
         'source': ('physical',)}
DATE_FIELD = {'forecast': 'date', 'dry_stock': 'checked_at', 'inventory': 'as_of',
              'transit': 'arrival_date', 'policies': 'transit_checked_at'}


@dataclass
class Inspection:
    dataset: Dataset | None
    issues: list
    files: list
    skipped_checks: list


def inspect_folder(folder, now):
    folder = Path(folder)
    issues, files, skipped, raw, locations = [], [], [], {}, {}
    tables = {name: [] for name in SCHEMA}
    records = {name: [] for name in SCHEMA}
    headers_ok = set()

    def issue(table, field, code, message, *, row=None, line=None, value=None):
        row = row or {}
        issues.append(ValidationIssue(table + '.csv', field, code, message, line=line,
                                      product=row.get('product') or None,
                                      warehouse=row.get('warehouse') or ('DRY' if table == 'dry_stock' else None),
                                      date=row.get(DATE_FIELD.get(table, '')) or None,
                                      lot=row.get('lot') or None, value=value))

    if not folder.is_dir():
        return Inspection(None, [ValidationIssue(str(folder), None, 'FOLDER_MISSING', 'CSVフォルダーが存在しません')], [], ['CSV検証未実施'])
    try:
        unknown = sorted(path for path in folder.iterdir() if path.suffix.lower() == '.csv' and path.name not in {name + '.csv' for name in SCHEMA})
    except OSError as exc:
        return Inspection(None, [ValidationIssue(str(folder), None, 'FOLDER_UNREADABLE', f'CSVフォルダーを読めません: {exc}')], [], ['CSV検証未実施'])
    sources = [(name, folder / (name + '.csv'), columns) for name, columns in SCHEMA.items()]
    sources.extend((path.stem, path, None) for path in unknown)
    for name, path, expected in sources:
        info = dict(filename=path.name, exists=path.exists(), expected=expected is not None,
                    optional=expected is not None and name == 'dry_stock', headers=None, rows=None, sha256=None,
                    bytes=None, status='missing', basis_times={})
        files.append(info)
        if not path.exists():
            if name != 'dry_stock':
                issue(name, None, 'FILE_MISSING', '必須ファイル欠損')
            continue
        if expected is None:
            issues.append(ValidationIssue(path.name, None, 'UNSUPPORTED_FILE', '未対応CSV。仮データ契約外のため取込停止'))
        reader = None
        try:
            binary = path.read_bytes()
            info.update(bytes=len(binary), sha256=hashlib.sha256(binary).hexdigest())
            # Match the previous read_text(newline=None) normalization for digests.
            content = binary.decode('utf-8-sig').replace('\r\n', '\n').replace('\r', '\n')
            reader = csv.reader(io.StringIO(content), strict=True)
            headers = next(reader, [])
            info['headers'] = headers
            info['rows'] = 0
            if expected is not None:
                raw[path.name] = content
                columns = expected.split(',')
                if headers != columns:
                    info['status'] = 'header_error'
                    for field in columns:
                        if field not in headers:
                            issue(name, field, 'HEADER_MISSING', '必須列がありません', line=1)
                    for field in dict.fromkeys(headers):
                        if headers.count(field) > 1:
                            issue(name, field, 'HEADER_DUPLICATE', '列名が重複しています', line=1, value=field)
                        if field not in columns:
                            issue(name, field, 'HEADER_UNKNOWN', '未定義の列です', line=1, value=field)
                    if set(headers) == set(columns) and len(headers) == len(columns):
                        issue(name, 'header', 'HEADER_ORDER', '列順が仮データ契約と一致しません', line=1)
                else:
                    headers_ok.add(name)
                    info['status'] = 'checked'
            else:
                info['status'] = 'unsupported'
            seen = set()
            basis = {field: set() for field in TIMESTAMPS if field in headers}
            for cells in reader:
                if not cells:
                    continue
                info['rows'] += 1
                if expected is None or name not in headers_ok:
                    continue
                line = reader.line_num
                row = {field: (cells[i].strip() if i < len(cells) else '') for i, field in enumerate(headers)}
                records[name].append((line, row))
                before = len(issues)
                if len(cells) != len(headers):
                    issue(name, 'columns', 'ROW_WIDTH', '行の列数がヘッダーと一致しません', line=line, row=row)
                converted = dict(row)
                for field, value in row.items():
                    if field in basis and value:
                        basis[field].add(value)
                    if not value:
                        issue(name, field, 'VALUE_MISSING', '項目欠損（ゼロとは区別）', row=row, line=line, value=value)
                        continue
                    try:
                        if field in NUMBERS:
                            if not value.isascii() or not value.isdecimal():
                                raise ValueError('0以上の整数必須')
                            converted[field] = int(value)
                            if field in POSITIVE and converted[field] == 0:
                                raise ValueError('正整数必須')
                        elif field in TIMESTAMPS:
                            if stamp(value) > now:
                                raise ValueError('未来の基準日時')
                        elif field in DATES:
                            if date.fromisoformat(value).isoformat() != value:
                                raise ValueError('YYYY-MM-DD必須')
                        if field == 'confirmed' and value != 'yes':
                            raise ValueError('未確認')
                        if field in ENUMS and value not in ENUMS[field]:
                            raise ValueError('未対応または未確認の値')
                    except (ValueError, OverflowError) as exc:
                        issue(name, field, 'VALUE_INVALID', str(exc), row=row, line=line, value=value)
                key = tuple(row[field] for field in KEYS[name])
                if all(key):
                    if key in seen:
                        issue(name, '+'.join(KEYS[name]), 'KEY_DUPLICATE', f'重複キー {key}', row=row, line=line)
                    seen.add(key)
                if len(issues) == before:
                    tables[name].append(converted)
                    locations.setdefault(name, {})[key] = line
            info['basis_times'] = {field: sorted(values) for field, values in basis.items()}
        except UnicodeError:
            info['status'] = 'encoding_error'
            issues.append(ValidationIssue(path.name, 'encoding', 'ENCODING', 'UTF-8（BOM可）として読めません。実CSVの文字コードは未確認です'))
        except (OSError, csv.Error) as exc:
            info['status'] = 'read_error'
            issues.append(ValidationIssue(path.name, 'csv', 'CSV_READ', f'CSVを読めません: {exc}', line=reader.line_num if reader else None))
    products = {row['product'] for _, row in records['products'] if row['product']}
    catalog_read = next(info for info in files if info['filename'] == 'products.csv')['status'] == 'checked'
    catalog_ready = catalog_read and 'products' in headers_ok and bool(products) and all(row['product'] for _, row in records['products'])
    if not products and 'products' in headers_ok:
        issue('products', 'product', 'NO_PRODUCTS', '商品なし')
    if not catalog_ready:
        skipped.append('商品コード参照・商品別全組合せチェック（商品マスタの識別子が不十分）')
    for name, rows in records.items():
        for line, row in rows:
            if catalog_ready and 'product' in row and name != 'products' and row['product'] and row['product'] not in products:
                issue(name, 'product', 'PRODUCT_UNKNOWN', '未知商品', row=row, line=line, value=row['product'])
            if 'warehouse' in row and row['warehouse'] and row['warehouse'] not in WAREHOUSES:
                issue(name, 'warehouse', 'WAREHOUSE_UNKNOWN', '未知倉庫', row=row, line=line, value=row['warehouse'])
    for warehouse in WAREHOUSES:
        if 'warehouses' in headers_ok and not any(row['warehouse'] == warehouse for _, row in records['warehouses']):
            issue('warehouses', 'warehouse', 'ROW_MISSING', '必須の倉庫行欠損', row={'warehouse': warehouse})
        if catalog_ready:
            for product in sorted(products):
                for name in ('inventory', 'policies', 'vehicles'):
                    if name in headers_ok and not any(row['warehouse'] == warehouse and row['product'] == product for _, row in records[name]):
                        issue(name, 'warehouse+product', 'ROW_MISSING', '必須行欠損', row={'warehouse': warehouse, 'product': product})
    digest = hashlib.sha256(json.dumps(raw, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    invalid_files = {problem.filename for problem in issues}
    for info in files:
        if info['status'] == 'checked' and info['filename'] in invalid_files:
            info['status'] = 'invalid'
    return Inspection(None if issues else Dataset(tables, raw, digest, locations), issues, files, skipped)
