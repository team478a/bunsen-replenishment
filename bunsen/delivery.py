"""Deterministic destination-only 14-day planning. Expiry is an estimate only."""
from datetime import date, timedelta

from .core import DataError, JST, ValidationIssue, calculate, case_p
from .requirements_data import active, pallet_p, PROFILE, NOTICE as REQUIREMENTS_NOTICE
from .exact_quantity import number, canonical, ceil_multiple

NOTICE = '今日製造した場合の概算期限（計算実行日を固定）。実商品の期限適合性・供給可能性・実出荷を保証しません。実在庫の期限・廃棄数量は不明です。'


def build_plan(data, start, calculated_at, *, allow_provisional=False):
    if data.workflow != 2:
        raise DataError(['14日配送計画にはムロオ新方式データが必要です'])
    if calculated_at.tzinfo is None:
        raise DataError(['計算実行日時はタイムゾーン必須です'])
    calculated_at = calculated_at.astimezone(JST)
    manufactured = calculated_at.date()
    if start < manufactured:
        raise DataError(['計画開始日は計算実行日以降にしてください'])
    products = {r['product']: r for r in data.tables['products']}
    errors = []
    for p in products.values():
        for field in ('expiry_type', 'shelf_life_days'):
            if field not in p:
                errors.append(ValidationIssue('products.csv', field, 'EXPIRY_MISSING', '14日計画の概算期限マスタが不足しています', product=p['product'], date=str(manufactured), phase='calculation'))
    if errors:
        raise DataError(errors)
    try:
        end = start + timedelta(days=13)
        expiry = {p: manufactured + timedelta(days=r['shelf_life_days']) for p,r in products.items()}
        # Validate the entire horizon before returning any partially valid plan.
        baselines = [calculate(data, start+timedelta(days=i), allow_provisional=allow_provisional) for i in range(14)]
    except OverflowError as exc:
        raise DataError(['計画/概算期限の日付が計算可能範囲を超えています']) from exc
    receipts, last_dispatch, shipments = {}, {}, []
    for index, rows in enumerate(baselines):
        dispatch = start+timedelta(days=index)
        for row in sorted(rows, key=lambda r:(r['warehouse'],r['product'])):
            key = (row['warehouse'], row['product'])
            if key in last_dispatch and (dispatch-last_dispatch[key]).days < row['settings']['cycle_days']:
                continue
            prior = receipts.setdefault(key, [])
            arrival = date.fromisoformat(row['arrival'])
            deficit = max([0]+[number(d['safety_p'])-number(d['projected_closing_p'])-sum(q for a,q in prior if a <= date.fromisoformat(d['date']))
                                 for d in row['timeline'] if d['date'] >= row['arrival']])
            p = products[key[1]]
            multiple=row['unit_p'] if active(data) else case_p(p)
            qty = ceil_multiple(deficit,multiple)
            if not qty:
                continue
            prior.append((arrival,qty));last_dispatch[key] = dispatch
            shipments.append(dict(warehouse=key[0],product=key[1],name=p['name'],dispatch=str(dispatch),arrival=str(arrival),
                                  required_p=deficit,quantity_p=qty,p_per_pl=pallet_p(data,*key) if active(data) else case_p(p)*p['cases_per_pl'],pl_only=active(data),
                                  expiry_type=p['expiry_type'],estimated_expiry=str(expiry[key[1]]),
                                  remaining_dispatch_days=(expiry[key[1]]-dispatch).days,
                                  remaining_arrival_days=(expiry[key[1]]-arrival).days))
    if not active(data):
        for shipment in shipments:shipment.pop('pl_only',None)
    trips = pack_trips(shipments)
    timelines, warnings = [], []
    for row in baselines[-1]:
        key = (row['warehouse'],row['product'])
        for day in row['timeline']:
            if not str(start) <= day['date'] <= str(end):
                continue
            incoming = sum(q for a,q in receipts.get(key,[]) if str(a)==day['date'])
            cumulative = sum(q for a,q in receipts.get(key,[]) if str(a)<=day['date'])
            closing = number(day['projected_closing_p'])+cumulative
            timelines.append(dict(warehouse=key[0],product=key[1],**day,planned_inbound_p=incoming,projected_after_plan_p=closing))
            if closing < 0:
                warnings.append(f'{key[0]} {key[1]} {day["date"]}: 入荷前を含む欠品リスク（予測） {canonical(closing)}P')
            elif closing < number(day['safety_p']):
                warnings.append(f'{key[0]} {key[1]} {day["date"]}: 安全在庫未達リスク（予測）')
            if (day['transit_p'] or incoming) and number(day['opening_p'])+cumulative-incoming-number(day['forecast_p']) < 0:
                warnings.append(f'{key[0]} {key[1]} {day["date"]}: 同日入庫と需要の時刻順は未確認')
    for row in shipments:
        if row['remaining_arrival_days'] < 0:
            warnings.append(f'{row["warehouse"]} {row["product"]} {row["arrival"]}: 入荷時に概算期限超過（参考、実期限不明）')
    risks = []
    for key in sorted(receipts):
        selected = [r for r in shipments if (r['warehouse'],r['product'])==key]
        if not selected:
            continue
        first = min(date.fromisoformat(r['arrival']) for r in selected)
        until = expiry[key[1]]
        forecasts = {date.fromisoformat(r['date']):r['quantity_p'] for r in data.tables['forecast'] if (r['warehouse'],r['product'])==key and r['status']=='confirmed'}
        available = sorted(d for d in forecasts if first <= d <= until)
        complete = until < first or (len(available)==(until-first).days+1)
        demand = sum(number(forecasts[d]) for d in available) if complete else None
        total = sum(r['quantity_p'] for r in selected)
        excess = max(0,total-demand) if complete else None
        risks.append(dict(warehouse=key[0],product=key[1],estimated_expiry=str(until),planned_p=total,forecast_until_expiry_p=demand,
                          excess_vs_forecast_p=excess,forecast_complete=complete))
        if not complete:
            warnings.append(f'{key[0]} {key[1]}: 概算期限までの予測不足のため過大補充リスクは確認不能')
        elif excess:
            warnings.append(f'{key[0]} {key[1]}: 追加補充が概算期限内の予測需要を{canonical(excess)}P超過（参考比較、廃棄量ではない）')
    report = dict(format='bunsen-delivery-plan',version=1,start=str(start),end=str(end),calculated_at=calculated_at.isoformat(),
                assumed_manufacture_date=str(manufactured),notice=NOTICE,capacity_pl=16,single_product_per_pl=True,
                method='日付順・倉庫別・商品コード順のルール計算（大域最適性は未保証）',
                allow_provisional=bool(allow_provisional),forecast_confirmed=all(r['forecast_confirmed'] for rows in baselines for r in rows),
                digest=data.digest,products=data.tables['products'],settings=data.tables['replenishment'],
                inventory_basis=data.tables['inventory'],forecast_basis=data.tables['forecast'],confirmed_inbound=None if active(data) else data.tables['transit'],
                shipments=shipments,trips=trips,timeline=timelines,expiry_risks=risks,warnings=warnings,formal=False)
    if active(data):
        report.update(profile=PROFILE,inbound_scope='excluded',pallet_masters=data.tables['pallets'],method='日付順・倉庫別・JAN順の物理PLルール計算（大域最適性は未保証）')
        report['warnings'].insert(0,REQUIREMENTS_NOTICE)
    from .forecast_csv import metadata
    if metadata(data.raw) is not None:report['forecast_source']=metadata(data.raw)
    from .product_scope import FILE,parse
    if FILE in data.raw:report['product_scope']=parse(data.raw[FILE])
    return canonical(report)


def pack_trips(shipments):
    """A partial pallet consumes one exclusive product slot; never mix warehouses."""
    trips = []
    for row in sorted(shipments,key=lambda r:(r['dispatch'],r['warehouse'],r['product'])):
        qty = row['quantity_p']
        while qty:
            if not trips or trips[-1]['dispatch']!=row['dispatch'] or trips[-1]['warehouse']!=row['warehouse'] or trips[-1]['pallets']==16:
                if len(trips)>=10000:
                    raise DataError(['計画便数が検証可能上限10000便を超えています。数量・単位を確認してください'])
                trips.append(dict(trip_id=f'T{len(trips)+1:04d}',dispatch=row['dispatch'],warehouse=row['warehouse'],pallets=0,items=[]))
            trip = trips[-1]
            take = min(qty,(16-trip['pallets'])*row['p_per_pl'])
            slots = (take+row['p_per_pl']-1)//row['p_per_pl']
            trip['items'].append(dict(row,quantity_p=take,pallets=slots))
            trip['pallets']+=slots;qty-=take
    return trips
