"""Read-only arithmetic outlook before any proposed replenishment."""
from dataclasses import asdict
from datetime import date, timedelta
import html
import json
from pathlib import Path

from .core import DataError, LABELS, ValidationIssue, now_jst, stamp
from .diagnostics import _write_outputs

LIMITATIONS = [
    '補充前の算術的な予測残高で、確定在庫・実欠品・出荷指示ではありません。将来の予測精度を保証しません。',
    '仮／正式指示・手修正・乾麺倉庫在庫・未確定製造／入庫予定は反映しません。倉庫間配分も行いません。',
    '同日の到着を需要予測より先に加算します。実際の入庫時刻と需要発生順は未確認です。',
    '算術上、在庫基準日の数量から同日の日次予測全量を差し引きます。日内消費済み分との重複は未確認です。',
    '負残高は累積の算術的不足で、実際の欠品や未充足注文の繰越を確定するものではありません。',
    '予測欠損等の影響日以降は確認不能とし、後日データが存在しても残高を自動復旧しません。',
    '輸送中の網羅性は在庫基準日時と一致する確認済み宣言に依存します。実CSV接続・業務設定承認は未確認です。',
]


def period(offset):
    return '当日' if offset == 0 else ('1〜3日先' if offset <= 3 else ('4〜7日先' if offset <= 7 else '8〜14日先'))


def build_outlook(data, start, now=None):
    try:
        end = start + timedelta(days=14)
    except OverflowError as exc:
        raise DataError(['参考見通しの14日先が日付範囲を超えています']) from exc
    now = now or now_jst()
    rows, issues, bases = [], [], []
    products = {r['product']: r for r in data.tables['products']}
    for inv in data.tables['inventory']:
        w, p = inv['warehouse'], inv['product']
        policy = next(r for r in data.tables['policies'] if (r['warehouse'], r['product']) == (w, p))
        forecasts = {date.fromisoformat(r['date']): r for r in data.tables['forecast'] if (r['warehouse'], r['product']) == (w, p)}
        shipments = [r for r in data.tables['transit'] if (r['warehouse'], r['product']) == (w, p)]
        snapshot = stamp(inv['as_of']).date()
        blocked = []
        def problem(table, field, code, message, day, key):
            issue = ValidationIssue(table + '.csv', field, code, message,
                                    line=data.locations.get(table, {}).get(key), product=p,
                                    warehouse=w, date=day.isoformat(), phase='outlook')
            issues.append(asdict(issue))
            return message
        if snapshot > start:
            blocked.append(problem('inventory', 'as_of', 'INVENTORY_AFTER_START', '在庫基準日が見通し基準日より未来', snapshot, (w, p)))
        basis_matches = stamp(policy['transit_checked_at']) == stamp(inv['as_of'])
        transit_known = basis_matches and snapshot <= start
        if not basis_matches:
            blocked.append(problem('policies', 'transit_checked_at', 'TRANSIT_SNAPSHOT', '輸送中網羅確認日時が在庫基準日時と不一致', start, (w, p)))
        if any(date.fromisoformat(r['arrival_date']) <= snapshot for r in shipments):
            transit_known = False
            for shipment in shipments:
                arrival = date.fromisoformat(shipment['arrival_date'])
                if arrival <= snapshot:
                    blocked.append(problem('transit', 'arrival_date', 'TRANSIT_ALREADY_ARRIVED', '在庫基準日以前に到着する輸送中数量（二重計上疑い）', arrival, (shipment['shipment_id'],)))
        # Inspect only supplied history dates; a huge missing range never becomes a loop.
        history_days = sorted(day for day in forecasts if snapshot <= day < start)
        cursor = snapshot
        history_demand = 0
        for day in history_days:
            if day > cursor:
                break
            row = forecasts[day]
            if stamp(row['as_of']).date() > start:
                blocked.append(problem('forecast', 'as_of', 'FORECAST_AFTER_START', '予測基準日が見通し基準日より未来', day, (w, p, day.isoformat())))
            history_demand += row['quantity_p']
            cursor = day + timedelta(days=1)
        if cursor < start:
            blocked.append(problem('forecast', 'quantity_p', 'FORECAST_HISTORY_MISSING', '在庫基準日から見通し開始前までの需要予測欠損。以降の残高は確認不能', cursor, (w, p, cursor.isoformat())))
        receipts = {}
        for shipment in shipments:
            day = date.fromisoformat(shipment['arrival_date'])
            if snapshot < day <= end:
                receipts.setdefault(day, []).append(shipment)
        balance = inv['quantity_p'] + sum(r['quantity_p'] for day, rs in receipts.items() if day < start for r in rs) - history_demand
        if blocked:
            balance = None
        bases.append(dict(warehouse=w, product=p, inventory_p=inv['quantity_p'], inventory_as_of=inv['as_of'],
                          transit_checked_at=policy['transit_checked_at'], history_forecast_p=history_demand if not blocked else None,
                          forecast_as_of=sorted({r['as_of'] for day, r in forecasts.items() if snapshot <= day <= end})))
        for offset in range(15):
            day = start + timedelta(days=offset)
            forecast = forecasts.get(day)
            today_reasons = []
            demand = None
            if forecast is None:
                today_reasons.append(problem('forecast', 'quantity_p', 'FORECAST_MISSING', '需要予測欠損', day, (w, p, day.isoformat())))
            elif stamp(forecast['as_of']).date() > start:
                today_reasons.append(problem('forecast', 'as_of', 'FORECAST_AFTER_START', '予測基準日が見通し基準日より未来', day, (w, p, day.isoformat())))
            else:
                demand = forecast['quantity_p']
            if today_reasons:
                blocked.extend(f'{day}: {reason}' for reason in today_reasons)
                balance = None
            arrivals = receipts.get(day, [])
            receipt_p = sum(r['quantity_p'] for r in arrivals) if transit_known else None
            opening = balance
            if balance is not None:
                balance += receipt_p - demand
            status = 'unknown' if balance is None else ('shortage_risk' if balance < 0 else 'reference')
            timing = bool(receipt_p and opening is not None and opening - demand < 0)
            rows.append(dict(warehouse=w, product=p, name=products[p]['name'], date=day.isoformat(), period=period(offset),
                             opening_p=opening, transit_p=receipt_p, forecast_p=demand, projected_closing_p=balance,
                             forecast_as_of=forecast['as_of'] if forecast else None,
                             shipments=[dict(r) for r in arrivals], status=status, timing_unconfirmed=timing,
                             reason=' / '.join(blocked) if blocked else ('入庫時刻と需要発生順は未確認' if timing else '')))
    return dict(format='bunsen-reference-outlook', version=1, schema='v0-provisional',
                start=start.isoformat(), end=end.isoformat(), checked_at=now.isoformat(), dataset_digest=data.digest,
                formal_issue_allowed=False, planned_replenishment_included=False,
                status='partial_unknown' if issues else 'reference_only', rows=rows, issues=issues,
                bases=bases, limitations=list(LIMITATIONS))


STATUS = {'unknown': '確認不能', 'shortage_risk': '不足リスク（予測）', 'reference': '参考値（予測）'}


def report_html(report):
    esc = lambda value: html.escape(str(value), quote=True)
    value = lambda number: '確認不能' if number is None else str(number)
    body = []
    for row in report['rows']:
        cells = [LABELS[row['warehouse']], row['product'], row['name'], row['date'], row['period'],
                 value(row['opening_p']), value(row['transit_p']), value(row['forecast_p']),
                 value(row['projected_closing_p']), STATUS[row['status']], row['reason']]
        body.append('<tr>' + ''.join('<td>' + esc(v) + '</td>' for v in cells) + '</tr>')
    return f'''<!doctype html><html lang="ja"><meta charset="utf-8"><title>補充前の14日参考見通し</title>
<style>body{{font-family:Meiryo,sans-serif;margin:24px}}table{{border-collapse:collapse;width:100%;font-size:12px}}td,th{{border:1px solid #777;padding:6px;overflow-wrap:anywhere}}.notice{{border:3px solid #a54000;padding:12px}}@media print{{button{{display:none}}thead{{display:table-header-group}}}}</style>
<h1>補充前の14日参考見通し</h1><p class="notice">予測に基づく参考値・出荷指示ではありません。仮／正式指示・手修正・乾麺在庫の割当は未反映です。</p>
<p>基準日 {esc(report['start'])} ～ {esc(report['end'])}／作成日時 {esc(report['checked_at'])}<br>データ識別 {esc(report['dataset_digest'])}</p>
<button onclick="window.print()">印刷・PDFに保存</button><ul>{''.join('<li>'+esc(v)+'</li>' for v in report['limitations'])}</ul>
<table><thead><tr>{''.join('<th>'+v+'</th>' for v in ['倉庫','商品','品名','日付','期間','始業予測P','輸送中到着P','需要予測P','終業予測P','状態','確認不能・注意の理由'])}</tr></thead><tbody>{''.join(body)}</tbody></table>
<h2>計算基準・基準日時</h2><pre>{esc(json.dumps(report['bases'], ensure_ascii=False, indent=2))}</pre>
<h2>不備の商品・倉庫・日付・項目</h2><pre>{esc(json.dumps(report['issues'], ensure_ascii=False, indent=2))}</pre></html>'''


def export_outlook(report, path, protected_folder=None):
    path = Path(path)
    if path.suffix.lower() != '.json':
        raise DataError(['参考見通しの保存先は.jsonを指定してください'])
    return _write_outputs([(path, json.dumps(report, ensure_ascii=False, indent=2)),
                           (path.with_suffix('.html'), report_html(report))], protected_folder)
