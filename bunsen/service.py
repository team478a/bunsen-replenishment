from __future__ import annotations

import copy
import html
import json
import sqlite3
import tempfile
from datetime import date
from pathlib import Path

from .core import DataError, LABELS, WAREHOUSES, calculate, load, now_jst, units, validate


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


class Service:
    def __init__(self, db_path, clock=now_jst):
        self.clock = clock
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(db_path, timeout=10)
        self.db.execute('CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY, at TEXT NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL)')
        self.db.execute('''CREATE TABLE IF NOT EXISTS issues (
            id INTEGER PRIMARY KEY, dispatch TEXT NOT NULL, revision INTEGER NOT NULL,
            formal INTEGER NOT NULL, number TEXT NOT NULL UNIQUE, at TEXT NOT NULL,
            payload TEXT NOT NULL, UNIQUE(dispatch, formal, revision))''')
        self.db.commit()

    def close(self):
        self.db.close()

    def event(self, kind, payload):
        with self.db:
            self.db.execute('INSERT INTO events(at,kind,payload) VALUES(?,?,?)',
                            (self.clock().isoformat(), kind, encode(payload)))

    def import_folder(self, folder):
        try:
            data = load(folder, self.clock())
        except DataError as exc:
            self.event('import_failed', {'errors': exc.errors})
            raise
        self.event('import', {'digest': data.digest, 'raw': data.raw})
        return data

    def calculate(self, data, dispatch):
        lines = calculate(data, dispatch)
        self.event('calculate', {'digest': data.digest, 'dispatch': dispatch.isoformat(), 'lines': lines})
        return lines

    def issue(self, data, lines, dispatch, operator, formal, acknowledged=False):
        # Reparse the source snapshot at issue time, including all typed values.
        with tempfile.TemporaryDirectory() as folder:
            for name, content in data.raw.items():
                if Path(name).name != name or not name.endswith('.csv'):
                    raise DataError(['取込原本のファイル名が不正'])
                Path(folder, name).write_text(content, encoding='utf-8')
            fresh = load(folder, self.clock())
        if fresh.digest != data.digest:
            raise DataError(['取込データが変更されています。再取込が必要'])
        canonical = calculate(fresh, dispatch)
        quantities = {(r['warehouse'], r['product']): r for r in lines}
        if len(quantities) != len(lines) or set(quantities) != {(r['warehouse'], r['product']) for r in canonical}:
            raise DataError(['全倉庫・商品明細の不一致。再計算が必要'])
        for row in canonical:
            edited = quantities[(row['warehouse'], row['product'])]
            row.update(quantity_p=edited['quantity_p'], lot=edited['lot'])
            if type(row['quantity_p']) is not int or row['quantity_p'] < 0:
                raise DataError(['数量は0以上の整数必須'])
        errors = validate(fresh, canonical, dispatch, self.clock())
        if formal and (not operator.strip() or not acknowledged):
            errors.append('担当者名と実ロット・期限・積載の再確認操作が必要')
        if formal and errors:
            self.event('issue_blocked', {'digest': data.digest, 'lines': canonical, 'errors': errors})
            raise DataError(errors)
        at = self.clock().isoformat()
        payload = {'digest': fresh.digest, 'raw': fresh.raw, 'tables': fresh.tables,
                   'lines': canonical, 'operator': operator, 'formal': formal,
                   'validation': errors, 'dispatch': dispatch.isoformat(), 'at': at}
        try:
            self.db.execute('BEGIN IMMEDIATE')
            revision = self.db.execute('SELECT COALESCE(MAX(revision),0)+1 FROM issues WHERE dispatch=? AND formal=?',
                                       (dispatch.isoformat(), int(formal))).fetchone()[0]
            number = f'{"F" if formal else "P"}-{dispatch:%Y%m%d}-R{revision:03d}'
            payload.update(number=number, revision=revision)
            self.db.execute('INSERT INTO issues(dispatch,revision,formal,number,at,payload) VALUES(?,?,?,?,?,?)',
                            (dispatch.isoformat(), revision, int(formal), number, at, encode(payload)))
            self.db.execute('INSERT INTO events(at,kind,payload) VALUES(?,?,?)', (at, 'issue', encode(payload)))
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise
        return copy.deepcopy(payload)

    def history(self):
        return self.db.execute('SELECT number,at,formal FROM issues ORDER BY id DESC').fetchall()

    def get_issue(self, number):
        row = self.db.execute('SELECT payload FROM issues WHERE number=?', (number,)).fetchone()
        if row is None:
            raise DataError(['発行履歴がありません'])
        return json.loads(row[0])


def report_html(payload, warehouse):
    esc = lambda v: html.escape(str(v), quote=True)
    formal = payload['formal']
    title = '正式出荷指示書' if formal else '仮出荷指示書'
    products = {r['product']: r for r in payload['tables']['products']}
    stocks = {(r['product'], r['lot']): r for r in payload['tables']['dry_stock']}
    body = []
    for row in payload['lines']:
        if row['warehouse'] != warehouse:
            continue
        product = products[row['product']]
        stock = stocks.get((row['product'], row['lot']))
        values = [row['product'], product['name'], row['quantity_p'], units(row['quantity_p'], product),
                  row['lot'] or '未確認', stock['best_before'] if stock else '未確認', row['dispatch'], row['arrival']]
        body.append('<tr>' + ''.join(f'<td>{esc(v)}</td>' for v in values) + '</tr>')
        basis = (f'在庫 {row["inventory_p"]}P（{row["inventory_as_of"]}）、輸送中 {row["transit_p"]}P'
                 f'（網羅確認 {row["transit_checked_at"]}）、到着前予測 {row["before_p"]}P、'
                 f'対象予測 {row["demand_p"]}P（予測基準 {", ".join(row["forecast_as_of"])}）、'
                 f'安全在庫 {row["safety_p"]}P → 必要 {row["required_p"]}P → '
                 f'{row["rounding"]}推奨 {row["suggested_p"]}P → 指示 {row["quantity_p"]}P。{row["warning"]}')
        if stock:
            basis += f' 現物確認 {stock["checked_at"]}／{stock["checker"]}'
        body.append(f'<tr><td colspan="8" class="basis">{esc(basis)}</td></tr>')
    status = ('担当者による実ロット・期限・専用車両重量の再確認済み。'
              if formal else '乾麺倉庫の当日在庫確認前。期限・積載等は以下の検証結果を参照。')
    warnings = '<br>'.join(esc(e) for e in payload['validation']) or '検証エラーなし'
    return f'''<!doctype html><html lang="ja"><meta charset="utf-8"><title>{esc(title)} {esc(payload['number'])}</title>
<style>body{{font-family:Meiryo,sans-serif;font-size:14px;margin:24px}}h1{{color:{'#174d37' if formal else '#9b5200'}}}table{{border-collapse:collapse;width:100%}}td,th{{border:1px solid #777;padding:7px}}.basis{{font-size:12px;background:#f3f3f3}}@media print{{button{{display:none}}body{{margin:0}}}}</style>
<h1>{esc(title)} — {esc(LABELS[warehouse])}</h1><p>{esc(status)}</p>
<p>発行番号 {esc(payload['number'])}／改訂 {payload['revision']}／担当者 {esc(payload['operator'] or '未確認')}<br>
発行時刻 {esc(payload['at'])}／データ識別 {esc(payload['digest'][:16])}</p>
<p>同日正式指示は全倉庫一括の改訂差替えです。最新版を履歴画面で確認し、旧帳票を回収してください。追加出荷には使用できません。</p>
<button onclick="window.print()">印刷・PDFに保存</button>
<table><thead><tr>{''.join('<th>'+v+'</th>' for v in ['商品','品名','数量P','単位','実ロット','賞味期限','出荷日','到着予定日'])}</tr></thead>
<tbody>{''.join(body)}</tbody></table><h2>検証結果</h2><p>{warnings}</p>
<p>専用便・重量条件のみを検証。サンプル仕様。未承認の混載・容積・段積みは対象外。</p></html>'''


def write_reports(payload, folder):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    result = []
    for w in WAREHOUSES:
        path = folder / f'{payload["number"]}-{w}.html'
        path.write_text(report_html(payload, w), encoding='utf-8')
        result.append(path.resolve())
    return result
