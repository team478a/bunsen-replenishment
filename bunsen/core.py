from __future__ import annotations

import csv
import hashlib
import io
import json
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

JST = timezone(timedelta(hours=9))
WAREHOUSES = ('KOBE', 'SUGITO')
LABELS = {'KOBE': 'ムロオ神戸', 'SUGITO': 'ムロオ杉戸'}
SCHEMA = {
    'products': 'product,name,p_per_ball,balls_per_case,cases_per_pl,ship_unit,weight_g,min_remaining_days,confirmed',
    'warehouses': 'warehouse,lead_days,confirmed',
    'policies': 'warehouse,product,coverage_days,safety_p,rounding,expiry_basis,transit_checked_at,confirmed',
    'inventory': 'warehouse,product,quantity_p,as_of,confirmed',
    'forecast': 'warehouse,product,date,quantity_p,as_of,order_basis',
    'transit': 'shipment_id,warehouse,product,quantity_p,arrival_date,confirmed',
    'vehicles': 'warehouse,product,capacity_g,confirmed',
    'dry_stock': 'product,lot,quantity_p,best_before,checked_at,checker,source,confirmed',
}
NUMBERS = {'p_per_ball', 'balls_per_case', 'cases_per_pl', 'weight_g', 'min_remaining_days',
           'lead_days', 'coverage_days', 'safety_p', 'quantity_p', 'capacity_g'}
POSITIVE = {'p_per_ball', 'balls_per_case', 'cases_per_pl', 'weight_g', 'coverage_days'}
TIMESTAMPS = {'as_of', 'checked_at', 'transit_checked_at'}
DATES = {'date', 'arrival_date', 'best_before'}
KEYS = {'products': ('product',), 'warehouses': ('warehouse',),
        'policies': ('warehouse', 'product'), 'inventory': ('warehouse', 'product'),
        'forecast': ('warehouse', 'product', 'date'), 'transit': ('shipment_id',),
        'vehicles': ('warehouse', 'product'), 'dry_stock': ('product', 'lot')}


class DataError(ValueError):
    def __init__(self, errors):
        self.errors = list(errors)
        super().__init__('\n'.join(self.errors))


@dataclass
class Dataset:
    tables: dict
    raw: dict
    digest: str


def now_jst():
    return datetime.now(JST)


def stamp(value):
    result = datetime.fromisoformat(value)
    if result.tzinfo is None:
        raise ValueError('タイムゾーン必須')
    return result.astimezone(JST)


def load(folder, now=None):
    now = now or now_jst()
    tables, raw, errors = {}, {}, []
    folder = Path(folder)
    if not folder.is_dir():
        raise DataError([f'{folder}: CSVフォルダーが存在しません'])
    allowed = {table + '.csv' for table in SCHEMA}
    for path in sorted(folder.iterdir()):
        if path.suffix.lower() == '.csv' and path.name not in allowed:
            errors.append(f'{path.name}: 未対応CSV。仮データ契約外のため取込停止')
    for table, columns in SCHEMA.items():
        path = Path(folder) / (table + '.csv')
        if not path.exists():
            if table == 'dry_stock':
                tables[table] = []
                continue
            errors.append(f'{table}.csv: 必須ファイル欠損')
            tables[table] = []
            continue
        try:
            content = path.read_text(encoding='utf-8-sig')
            raw[path.name] = content
            reader = csv.DictReader(io.StringIO(content), strict=True)
            if reader.fieldnames != columns.split(','):
                raise ValueError('列名・列順が仮データ契約と一致しません')
            rows, seen = [], set()
            for line, row in enumerate(reader, 2):
                context = f'{table}.csv:{line} 商品={row.get("product", "-")} 倉庫={row.get("warehouse", "乾麺")} 日付={row.get("date", row.get("checked_at", row.get("as_of", "-")))}'
                try:
                    if None in row or any(v is None or not v.strip() for v in row.values()):
                        raise ValueError('項目欠損（ゼロとは区別）')
                    row = {k: v.strip() for k, v in row.items()}
                    for field in row:
                        value = row[field]
                        if field in NUMBERS:
                            if not value.isascii() or not value.isdecimal():
                                raise ValueError(f'{field}: 0以上の整数必須')
                            row[field] = int(value)
                            if field in POSITIVE and row[field] == 0:
                                raise ValueError(f'{field}: 正整数必須')
                        elif field in TIMESTAMPS:
                            parsed = stamp(value)
                            if parsed > now:
                                raise ValueError(f'{field}: 未来の基準日時')
                        elif field in DATES:
                            if date.fromisoformat(value).isoformat() != value:
                                raise ValueError(f'{field}: YYYY-MM-DD必須')
                    if 'confirmed' in row and row['confirmed'] != 'yes':
                        raise ValueError('confirmed: 未確認')
                    enums = {'ship_unit': ('PL', 'CASE'), 'rounding': ('ceil', 'floor'),
                             'expiry_basis': ('shipment', 'arrival'), 'order_basis': ('included',),
                             'source': ('physical',)}
                    for field, choices in enums.items():
                        if field in row and row[field] not in choices:
                            raise ValueError(f'{field}: 未対応または未確認 {row[field]}')
                    key = tuple(row[k] for k in KEYS[table])
                    if key in seen:
                        raise ValueError(f'重複キー {key}')
                    seen.add(key)
                    rows.append(row)
                except (ValueError, TypeError) as exc:
                    errors.append(f'{context}: {exc}')
            tables[table] = rows
        except (ValueError, OSError, UnicodeError, csv.Error) as exc:
            tables[table] = []
            errors.append(f'{path.name}: {exc}')
    products = {r['product'] for r in tables['products']}
    if not products:
        errors.append('products.csv: 商品なし')
    for table, rows in tables.items():
        for r in rows:
            if 'product' in r and table != 'products' and r['product'] not in products:
                errors.append(f'{table}: 未知商品 {r["product"]}')
            if 'warehouse' in r and r['warehouse'] not in WAREHOUSES:
                errors.append(f'{table}: 未知倉庫 {r["warehouse"]}')
    for w in WAREHOUSES:
        if not any(r['warehouse'] == w for r in tables['warehouses']):
            errors.append(f'warehouses: {w} 欠損')
        for p in products:
            for table in ('inventory', 'policies', 'vehicles'):
                if not any(r['warehouse'] == w and r['product'] == p for r in tables[table]):
                    errors.append(f'{table}: 倉庫={w} 商品={p} 必須行欠損')
    if errors:
        raise DataError(errors)
    digest = hashlib.sha256(json.dumps(raw, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return Dataset(tables, raw, digest)


def case_p(product):
    return product['p_per_ball'] * product['balls_per_case']


def unit_p(product):
    return case_p(product) * (product['cases_per_pl'] if product['ship_unit'] == 'PL' else 1)


def units(quantity, product):
    pl, remainder = divmod(quantity, case_p(product) * product['cases_per_pl'])
    cases, packs = divmod(remainder, case_p(product))
    return f'{pl} PL + {cases} 甲' + (f' + {packs} P（端数）' if packs else '')


def allocations(line):
    """Read a split instruction or a legacy single-lot instruction, without inference."""
    if 'allocations' not in line:
        return [{'lot': line['lot'], 'quantity_p': line['quantity_p']}] if line['quantity_p'] else []
    if line.get('lot'):
        raise DataError(['単一ロットと複数ロットの併用はできません'])
    rows = line['allocations']
    if not isinstance(rows, list):
        raise DataError(['ロット別割当はリストで指定してください'])
    for row in rows:
        if not isinstance(row, dict) or set(row) != {'lot', 'quantity_p'}:
            raise DataError(['ロット別割当の項目はlotとquantity_pのみです'])
        if not isinstance(row['lot'], str):
            raise DataError(['ロット名は文字列で指定してください'])
        if type(row['quantity_p']) is not int or row['quantity_p'] <= 0:
            raise DataError(['ロット別数量Pは正整数必須（ゼロは行を削除）'])
    return [dict(row) for row in rows]


def allocation_label(line):
    if 'allocations' not in line:
        return line['lot'] or '未確認'
    rows = allocations(line)
    total = sum(r['quantity_p'] for r in rows)
    summary = ' / '.join(f'{r["lot"] or "未確認"}: {r["quantity_p"]}P' for r in rows) or '未割当'
    if total != line['quantity_p']:
        summary += f'（合計不一致: 割当{total}P／指示{line["quantity_p"]}P）'
    return summary


def calculate(data, dispatch):
    t, lines, errors = data.tables, [], []
    products = {r['product']: r for r in t['products']}
    for inv in t['inventory']:
        w, p = inv['warehouse'], inv['product']
        context = f'倉庫={w} 商品={p} 出荷日={dispatch}'
        policy = next(r for r in t['policies'] if r['warehouse'] == w and r['product'] == p)
        lead = next(r['lead_days'] for r in t['warehouses'] if r['warehouse'] == w)
        arrival = dispatch + timedelta(days=lead)
        start = stamp(inv['as_of']).date()
        if start > dispatch:
            errors.append(f'{context}: 在庫基準日が出荷日より未来')
            continue
        if stamp(policy['transit_checked_at']) != stamp(inv['as_of']):
            errors.append(f'{context}: 輸送中網羅確認日時が在庫基準日時と不一致')
        forecasts = {r['date']: r for r in t['forecast'] if r['warehouse'] == w and r['product'] == p}
        before, demand = 0, 0
        end = arrival + timedelta(days=policy['coverage_days'])
        day = start
        while day < end:
            row = forecasts.get(day.isoformat())
            if row is None:
                errors.append(f'{context} 予測日={day}: 需要予測欠損')
            elif stamp(row['as_of']).date() > dispatch:
                errors.append(f'{context} 予測日={day}: 予測基準日が出荷日より未来')
            elif day < arrival:
                before += row['quantity_p']
            else:
                demand += row['quantity_p']
            day += timedelta(days=1)
        transit = 0
        receipts = {}
        for r in t['transit']:
            if (r['warehouse'], r['product']) == (w, p):
                day = date.fromisoformat(r['arrival_date'])
                if day <= start:
                    errors.append(f'{context}: 輸送中 {r["shipment_id"]} は在庫基準日以前到着（二重計上疑い）')
                elif day <= arrival:
                    transit += r['quantity_p']
                    receipts[day] = receipts.get(day, 0) + r['quantity_p']
        # Preserve the aggregate replenishment formula while exposing timing risk.
        # A receipt on arrival day must not conceal a deficit on an earlier day.
        timeline, balance = [], inv['quantity_p']
        day = start
        while day <= arrival:
            opening = balance
            receipt = receipts.get(day, 0)
            predicted = forecasts.get(day.isoformat(), {}).get('quantity_p')
            if predicted is not None:
                balance += receipt - predicted
                timeline.append(dict(date=day.isoformat(), opening_p=opening,
                                     transit_p=receipt, forecast_p=predicted,
                                     projected_closing_p=balance))
            day += timedelta(days=1)
        risk_days = [r['date'] for r in timeline if r['date'] < arrival.isoformat() and r['projected_closing_p'] < 0]
        timing_days = [r['date'] for r in timeline if r['transit_p'] > 0 and r['opening_p'] - r['forecast_p'] < 0]
        warnings = []
        if risk_days:
            warnings.append(f'到着前の不足リスク（予測日別残高）: {", ".join(risk_days)}')
        if timing_days:
            warnings.append(f'輸送中の入庫時刻と需要発生順は未確認: {", ".join(timing_days)}')
        available = inv['quantity_p'] + transit - before
        need = max(0, demand + policy['safety_p'] - available)
        multiple = unit_p(products[p])
        adjusted = ((need + multiple - 1) // multiple if policy['rounding'] == 'ceil' else need // multiple) * multiple
        lines.append(dict(warehouse=w, product=p, dispatch=dispatch.isoformat(), arrival=arrival.isoformat(),
                          inventory_p=inv['quantity_p'], inventory_as_of=inv['as_of'],
                          forecast_as_of=sorted({r['as_of'] for r in forecasts.values()}),
                          transit_checked_at=policy['transit_checked_at'], transit_p=transit,
                          before_p=before, demand_p=demand, safety_p=policy['safety_p'],
                          required_p=need, suggested_p=adjusted, quantity_p=adjusted, lot='',
                          warning=' / '.join(warnings), timeline=timeline,
                          rounding=policy['rounding'], unit_p=multiple))
    if errors:
        raise DataError(errors)
    return lines


def validate(data, lines, dispatch, now=None):
    """Recompute all safety checks; never rely on previous UI confirmations."""
    now = now or now_jst()
    errors = []
    if dispatch != now.astimezone(JST).date():
        errors.append(f'出荷日={dispatch}: 正式発行は本日のみ')
    expected = calculate(data, dispatch)
    expected_keys = {(r['warehouse'], r['product']) for r in expected}
    actual_keys = [(r['warehouse'], r['product']) for r in lines]
    if set(actual_keys) != expected_keys or len(actual_keys) != len(expected_keys):
        errors.append('指示明細: 全倉庫・商品の一意な明細が必要')
    t = data.tables
    products = {r['product']: r for r in t['products']}
    stocks = {(r['product'], r['lot']): r for r in t['dry_stock']}
    used = {}
    for p in products:
        rows = [r for r in t['dry_stock'] if r['product'] == p]
        if not rows:
            errors.append(f'乾麺倉庫 商品={p}: 当日朝の現物確認在庫欠損')
        for r in rows:
            checked = stamp(r['checked_at'])
            if checked.date() != dispatch or checked.time() >= time(12) or checked > now:
                errors.append(f'乾麺倉庫 商品={p} ロット={r["lot"]}: 当日朝の現物確認ではない')
            if r['source'] != 'physical' or r['confirmed'] != 'yes' or not r['checker'].strip():
                errors.append(f'乾麺倉庫 商品={p}: 現物確認不能')
    for line in lines:
        w, p, qty = line['warehouse'], line['product'], line['quantity_p']
        context = f'倉庫={w} 商品={p} 出荷日={dispatch}'
        if p not in products or (w, p) not in expected_keys:
            errors.append(f'{context}: 未知の明細')
            continue
        product = products[p]
        if type(qty) is not int or qty < 0:
            errors.append(f'{context}: 出荷Pは0以上の整数必須')
            continue
        if qty % case_p(product):
            errors.append(f'{context}: 甲単位の端数積付は禁止')
        vehicle = next(r for r in t['vehicles'] if r['warehouse'] == w and r['product'] == p)
        if qty * product['weight_g'] > vehicle['capacity_g']:
            errors.append(f'{context}: 積載超過 {qty * product["weight_g"]}>{vehicle["capacity_g"]}g')
        try:
            assigned = allocations(line)
        except DataError as exc:
            errors.extend(f'{context}: {e}' for e in exc.errors)
            continue
        total = sum(r['quantity_p'] for r in assigned)
        if total != qty:
            errors.append(f'{context}: ロット別数量の合計不一致 割当={total}P 指示={qty}P')
        seen_lots = set()
        policy = next(r for r in t['policies'] if r['warehouse'] == w and r['product'] == p)
        original = next(r for r in expected if (r['warehouse'], r['product']) == (w, p))
        basis = dispatch if policy['expiry_basis'] == 'shipment' else date.fromisoformat(original['arrival'])
        for allocation in assigned:
            lot, amount = allocation['lot'], allocation['quantity_p']
            if lot in seen_lots:
                errors.append(f'{context} ロット={lot}: 同一明細内のロット重複')
            seen_lots.add(lot)
            if amount % case_p(product):
                errors.append(f'{context} ロット={lot}: 甲単位の端数積付は禁止')
            key = (p, lot)
            stock = stocks.get(key)
            if stock is None:
                errors.append(f'{context}: 実ロット未確認 {lot}')
                continue
            used[key] = used.get(key, 0) + amount
            if (date.fromisoformat(stock['best_before']) - basis).days < product['min_remaining_days']:
                errors.append(f'{context} ロット={lot}: 期限違反（基準日={basis}）')
    for key, qty in used.items():
        if qty > stocks[key]['quantity_p']:
            errors.append(f'乾麺倉庫 商品={key[0]} ロット={key[1]}: 二重割当／不足 {qty - stocks[key]["quantity_p"]} P')
    return errors


def shortages(data, lines):
    result = []
    for product in data.tables['products']:
        p = product['product']
        need = sum(r['quantity_p'] for r in lines if r['product'] == p)
        stocks = [r for r in data.tables['dry_stock'] if r['product'] == p]
        if not stocks:
            result.append(f'商品={p}: 朝の現物在庫未確認／不足数量は確認不能')
        else:
            stock = sum(r['quantity_p'] for r in stocks)
            result.append(f'商品={p}: 指示 {need} P / 現物 {stock} P / 不足 {max(0, need-stock)} P（期限・ロット別検証は別途必須）')
    return result
