from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone

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
V2_SCHEMA = {
    'products': 'product,name,p_per_ball,balls_per_case,cases_per_pl,ship_unit,confirmed',
    'inventory': 'warehouse,product,quantity_p,as_of,basis,confirmed',
    'forecast': SCHEMA['forecast'] + ',status',
    'transit': SCHEMA['transit'],
    'replenishment': 'warehouse,product,cycle_days,lead_days,safety_days,coverage_days,transit_checked_at,confirmed',
}
KEYS['replenishment'] = ('warehouse', 'product')
NUMBERS.update({'cycle_days', 'safety_days'})
POSITIVE.add('cycle_days')


@dataclass(frozen=True)
class ValidationIssue:
    filename: str
    field: str | None
    code: str
    message: str
    line: int | None = None
    product: str | None = None
    warehouse: str | None = None
    date: str | None = None
    lot: str | None = None
    value: str | None = None
    phase: str = 'contract'

    def __str__(self):
        prefix = self.filename + (f':{self.line}' if self.line is not None else '')
        if not any((self.field, self.product, self.warehouse, self.date, self.lot)):
            return f'{prefix}: {self.message}'
        warehouse = '乾麺倉庫' if self.warehouse == 'DRY' else self.warehouse
        context = f'商品={self.product or "-"} 倉庫={warehouse or "-"} 日付={self.date or "-"}'
        if self.lot:
            context += f' ロット={self.lot}'
        return f'{prefix} {context} 項目={self.field or "-"}: {self.message}'


class DataError(ValueError):
    def __init__(self, errors):
        values = list(errors)
        self.issues = [value for value in values if isinstance(value, ValidationIssue)]
        self.errors = [str(value) for value in values]
        super().__init__('\n'.join(self.errors))


@dataclass
class Dataset:
    tables: dict
    raw: dict
    digest: str
    locations: dict = field(default_factory=dict)
    workflow: int = 0


def now_jst():
    return datetime.now(JST)


def stamp(value):
    result = datetime.fromisoformat(value)
    if result.tzinfo is None:
        raise ValueError('タイムゾーン必須')
    return result.astimezone(JST)


def inspect_folder(folder, now=None, *, workflow=None):
    from .ingest import inspect_folder as inspect
    return inspect(folder, now or now_jst(),workflow=workflow)


def load(folder, now=None, *, workflow=None):
    inspection = inspect_folder(folder, now,workflow=workflow)
    if inspection.issues:
        raise DataError(inspection.issues)
    return inspection.dataset


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


def calculate(data, dispatch, *, allow_provisional=False):
    if data.workflow == 2:
        from .replenishment import calculate as calculate_v2
        return calculate_v2(data, dispatch, allow_provisional=allow_provisional)
    t, lines, errors = data.tables, [], []
    products = {r['product']: r for r in t['products']}
    for inv in t['inventory']:
        issue_start = len(errors)
        w, p = inv['warehouse'], inv['product']
        def problem(table, field, code, message, day=None, key=None, value=None):
            record_key = key if key is not None else (w, p)
            errors.append(ValidationIssue(table + '.csv', field, code, message,
                                          line=data.locations.get(table, {}).get(record_key),
                                          product=p, warehouse=w, date=str(day or dispatch),
                                          phase='calculation', value=value))
        policy = next(r for r in t['policies'] if r['warehouse'] == w and r['product'] == p)
        lead = next(r['lead_days'] for r in t['warehouses'] if r['warehouse'] == w)
        try:
            arrival = dispatch + timedelta(days=lead)
        except OverflowError:
            problem('warehouses', 'lead_days', 'DATE_RANGE', '配送日数が日付の計算可能範囲を超えています', key=(w,), value=str(lead))
            continue
        start = stamp(inv['as_of']).date()
        if start > dispatch:
            problem('inventory', 'as_of', 'INVENTORY_AFTER_DISPATCH', '在庫基準日が出荷日より未来', value=inv['as_of'])
            continue
        if stamp(policy['transit_checked_at']) != stamp(inv['as_of']):
            problem('policies', 'transit_checked_at', 'TRANSIT_SNAPSHOT', '輸送中網羅確認日時が在庫基準日時と不一致', value=policy['transit_checked_at'])
        forecasts = {r['date']: r for r in t['forecast'] if r['warehouse'] == w and r['product'] == p}
        before, demand = 0, 0
        try:
            end = arrival + timedelta(days=policy['coverage_days'])
        except OverflowError:
            problem('policies', 'coverage_days', 'DATE_RANGE', '補充期間が日付の計算可能範囲を超えています', value=str(policy['coverage_days']))
            continue
        available_days = sorted(date.fromisoformat(value) for value in forecasts if start <= date.fromisoformat(value) < end)
        next_day = start
        def missing(first, last):
            suffix = f'（{last}までの連続欠落）' if last != first else ''
            problem('forecast', 'quantity_p', 'FORECAST_MISSING', f'予測日={first}: 需要予測欠損{suffix}', first, (w, p, first.isoformat()))
        for day in available_days:
            if day > next_day:
                missing(next_day, day - timedelta(days=1))
            row = forecasts[day.isoformat()]
            if stamp(row['as_of']).date() > dispatch:
                problem('forecast', 'as_of', 'FORECAST_AFTER_DISPATCH', '予測基準日が出荷日より未来', day, (w, p, day.isoformat()), row['as_of'])
            elif day < arrival:
                before += row['quantity_p']
            else:
                demand += row['quantity_p']
            next_day = day + timedelta(days=1)
        if next_day < end:
            missing(next_day, end - timedelta(days=1))
        transit = 0
        receipts = {}
        for r in t['transit']:
            if (r['warehouse'], r['product']) == (w, p):
                day = date.fromisoformat(r['arrival_date'])
                if day <= start:
                    problem('transit', 'arrival_date', 'TRANSIT_ALREADY_ARRIVED', f'輸送中 {r["shipment_id"]} は在庫基準日以前到着（二重計上疑い）', day, (r['shipment_id'],), r['arrival_date'])
                elif day <= arrival:
                    transit += r['quantity_p']
                    receipts[day] = receipts.get(day, 0) + r['quantity_p']
        if len(errors) != issue_start:
            continue
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


def morning_stock_issues(data, dispatch, now=None):
    now = now or now_jst()
    issues = []
    if dispatch != now.astimezone(JST).date():
        issues.append(ValidationIssue('対象日', 'dispatch', 'FORMAL_DAY', '正式発行は本日のみ', date=dispatch.isoformat(), phase='morning_stock'))
    for product in data.tables['products']:
        p = product['product']
        rows = [r for r in data.tables['dry_stock'] if r['product'] == p]
        if not rows:
            issues.append(ValidationIssue('dry_stock.csv', 'quantity_p', 'MORNING_MISSING', '当日朝の現物確認在庫欠損', product=p, warehouse='DRY', date=dispatch.isoformat(), phase='morning_stock'))
        for r in rows:
            def problem(field, code, message):
                issues.append(ValidationIssue('dry_stock.csv', field, code, message,
                                              line=data.locations.get('dry_stock', {}).get((p, r['lot'])),
                                              product=p, warehouse='DRY', lot=r['lot'], date=dispatch.isoformat(),
                                              value=r[field], phase='morning_stock'))
            checked = stamp(r['checked_at'])
            if checked.date() != dispatch or checked.time() >= time(12) or checked > now:
                problem('checked_at', 'MORNING_TIME', '当日朝の現物確認ではない')
            if r['source'] != 'physical':
                problem('source', 'PHYSICAL_REQUIRED', '現物確認不能')
            if r['confirmed'] != 'yes':
                problem('confirmed', 'CONFIRMED_REQUIRED', '現物確認不能')
            if not r['checker'].strip():
                problem('checker', 'CHECKER_REQUIRED', '現物確認者が欠損')
    return issues


def validate(data, lines, dispatch, now=None):
    """Recompute all safety checks; never rely on previous UI confirmations."""
    if data.workflow == 2:
        from .replenishment import validate as validate_v2
        return validate_v2(data, lines, dispatch, now)
    errors = [str(issue) for issue in morning_stock_issues(data, dispatch, now)]
    expected = calculate(data, dispatch)
    expected_keys = {(r['warehouse'], r['product']) for r in expected}
    actual_keys = [(r['warehouse'], r['product']) for r in lines]
    if set(actual_keys) != expected_keys or len(actual_keys) != len(expected_keys):
        errors.append('指示明細: 全倉庫・商品の一意な明細が必要')
    t = data.tables
    products = {r['product']: r for r in t['products']}
    stocks = {(r['product'], r['lot']): r for r in t['dry_stock']}
    used = {}
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
