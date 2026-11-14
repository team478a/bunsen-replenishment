"""V2: destination requirements, independent of Bunsen stock and shipment actuals."""
import copy
from datetime import date, timedelta

from .core import DataError, ValidationIssue, case_p, now_jst, stamp, JST


def calculate(data, dispatch, *, allow_provisional=False):
    errors, result = [], []
    products = {r['product']: r for r in data.tables['products']}
    for inv in data.tables['inventory']:
        w, p = inv['warehouse'], inv['product']
        start_errors = len(errors)
        setting = next(r for r in data.tables['replenishment'] if (r['warehouse'], r['product']) == (w, p))
        def error(table, field, code, message, day=dispatch, key=None):
            key = (w, p) if key is None else key
            errors.append(ValidationIssue(table+'.csv', field, code, message,
                          line=data.locations.get(table, {}).get(key), warehouse=w, product=p,
                          date=str(day), phase='calculation'))
        try:
            snapshot = stamp(inv['as_of']).date()
            first = snapshot + timedelta(days=1 if inv['basis'] == 'closing' else 0)
            arrival = dispatch + timedelta(days=setting['lead_days'])
            period_days = max(setting['cycle_days'], setting['coverage_days'])
            end = arrival + timedelta(days=period_days-1)
            forecast_end = end + timedelta(days=setting['safety_days'])
        except OverflowError:
            error('replenishment', 'lead_days/coverage_days/cycle_days/safety_days', 'DATE_RANGE', '設定が日付の計算可能範囲を超えています')
            continue
        if snapshot > dispatch or first > arrival:
            error('inventory', 'as_of/basis', 'INVENTORY_AFTER_DISPATCH', '在庫基準が出荷日・到着日より未来です')
            continue
        if stamp(setting['transit_checked_at']) != stamp(inv['as_of']):
            error('replenishment', 'transit_checked_at', 'TRANSIT_SNAPSHOT', '確定入庫の網羅確認日時が在庫基準日時と一致しません')
        forecast = {date.fromisoformat(r['date']): r for r in data.tables['forecast'] if (r['warehouse'], r['product']) == (w, p)}
        days = sorted(d for d in forecast if first <= d <= forecast_end)
        cursor = first
        for day in days:
            if day > cursor:
                error('forecast', 'quantity_p', 'FORECAST_MISSING', f'{cursor}から{day-timedelta(days=1)}の予測欠損', cursor, (w,p,str(cursor)))
            row = forecast[day]
            if stamp(row['as_of']).date() > dispatch:
                error('forecast', 'as_of', 'FORECAST_AFTER_DISPATCH', '予測基準日が出荷日より未来です', day, (w,p,str(day)))
            if row['status'] != 'confirmed' and not allow_provisional:
                error('forecast', 'status', 'PROVISIONAL_FORECAST', '未確定予測です。参考計算を許可するか、確認済み予測を再取込してください', day, (w,p,str(day)))
            if day < forecast_end:
                cursor = day + timedelta(days=1)
            else:
                cursor = None
        if cursor is not None:
            error('forecast', 'quantity_p', 'FORECAST_MISSING', f'{cursor}から{forecast_end}の予測欠損', cursor, (w,p,str(cursor)))
        receipts = {}
        for r in data.tables['transit']:
            if (r['warehouse'],r['product']) != (w,p):
                continue
            day = date.fromisoformat(r['arrival_date'])
            if day < first:
                error('transit', 'arrival_date', 'TRANSIT_ALREADY_ARRIVED', '在庫に反映済みの日付の入庫予定です（二重計上疑い）', day, (r['shipment_id'],))
            if first <= day <= end:
                receipts[day] = receipts.get(day,0) + r['quantity_p']
        if len(errors) != start_errors:
            continue
        # Every required date is present before generating a timeline or numeric result.
        prefix = [0]
        for d in days:
            prefix.append(prefix[-1] + forecast[d]['quantity_p'])
        balance, needed, timeline = inv['quantity_p'], 0, []
        for index, day in enumerate(days):
            if day > end:
                break
            opening = balance
            incoming = receipts.get(day,0)
            demand = forecast[day]['quantity_p']
            balance += incoming - demand
            safety = prefix[index+1+setting['safety_days']] - prefix[index+1]
            if day >= arrival:
                needed = max(needed, safety-balance)
            timeline.append(dict(date=str(day),opening_p=opening,transit_p=incoming,forecast_p=demand,
                                 projected_closing_p=balance,safety_p=safety,
                                 timing_unconfirmed=bool(incoming and opening-demand < 0)))
        needed = max(0,needed)
        multiple = case_p(products[p])
        adjusted = ((needed+multiple-1)//multiple)*multiple
        risk = [r['date'] for r in timeline if r['date'] < str(arrival) and r['projected_closing_p'] < 0]
        uncertain = [str(d) for d in days if forecast[d]['status'] != 'confirmed']
        warnings = []
        if risk:
            warnings.append('入荷前欠品リスク（予測）: '+', '.join(risk)+'。今回補充の増量では前日の不足は解消しません。')
        if any(r['timing_unconfirmed'] for r in timeline):
            warnings.append('同日入庫と需要発生の順序は未確認です')
        if uncertain:
            warnings.append('未確定予測使用・正式確定不可: '+', '.join(uncertain))
        if setting['coverage_days'] < setting['cycle_days']:
            warnings.append('対象期間を補充サイクルまで延長して計算しています')
        result.append(dict(workflow=2,warehouse=w,product=p,dispatch=str(dispatch),arrival=str(arrival),
                           end=str(end),inventory_p=inv['quantity_p'],inventory_as_of=inv['as_of'],inventory_basis=inv['basis'],
                           forecast_as_of=sorted({forecast[d]['as_of'] for d in days}),
                           transit_checked_at=setting['transit_checked_at'],transit_p=sum(receipts.values()),
                           before_p=sum(r['forecast_p'] for r in timeline if r['date'] < str(arrival)),
                           demand_p=sum(r['forecast_p'] for r in timeline if r['date'] >= str(arrival)),
                           safety_p=max(r['safety_p'] for r in timeline if r['date'] >= str(arrival)),
                           required_p=needed,suggested_p=adjusted,quantity_p=adjusted,lot='',edit_reason='',
                           settings=dict(setting),allow_provisional=bool(allow_provisional),forecast_confirmed=not uncertain,
                           timeline=timeline,warning=' / '.join(warnings),rounding='ceil_case',unit_p=multiple))
    if errors:
        raise DataError(errors)
    return result


def overlay(calculated, requested):
    expected = {(r['warehouse'],r['product']) for r in calculated}
    keys = [(r['warehouse'],r['product']) for r in requested]
    if set(keys) != expected or len(keys) != len(expected):
        raise DataError(['全倉庫・商品の一意な明細が必要です。旧方式の明細は引き継げません'])
    requested = {(r['warehouse'],r['product']): r for r in requested}
    result = copy.deepcopy(calculated)
    for row in result:
        old = requested[(row['warehouse'],row['product'])]
        if old.get('workflow') != 2 or old.get('allocations') or old.get('lot'):
            raise DataError(['新方式は補充数量の指示です。旧ロット割当明細は使用できません'])
        qty, reason = old['quantity_p'], old.get('edit_reason','')
        if type(qty) is not int or qty < 0 or not isinstance(reason,str):
            raise DataError(['数量は0以上の整数、修正理由は文字列で指定してください'])
        if qty % row['unit_p']:
            raise DataError(['指示数量は商品マスタの甲単位にしてください（自動補正しません）'])
        if qty != row['suggested_p'] and not reason.strip():
            raise DataError(['自動計算値と異なる数量には修正理由が必要です'])
        row.update(quantity_p=qty,edit_reason=reason)
        if qty < row['required_p']:
            row['warning'] += ' / 担当者修正後も安全在庫未達のリスクがあります。'
    return result


def validate(data, lines, dispatch, now=None):
    errors = []
    if dispatch < (now or now_jst()).astimezone(JST).date():
        errors.append('過去日の補充指示は正式確定できません')
    products = {r['product']:r for r in data.tables['products']}
    for row in lines:
        context = f'倉庫={row["warehouse"]} 商品={row["product"]} 出荷日={dispatch}'
        if not row['forecast_confirmed']:
            errors.append(context+': 未確定・補完/暫定予測を使用した結果は正式確定できません')
        if row['quantity_p'] % case_p(products[row['product']]):
            errors.append(context+': 指示数量は商品別の甲単位にしてください')
        if row['quantity_p'] != row['suggested_p'] and not row['edit_reason'].strip():
            errors.append(context+': 自動計算値と異なる数量には修正理由が必要です')
    return errors
