from __future__ import annotations

import copy
import html
import json
import sqlite3
import tempfile
from datetime import date
from pathlib import Path

from .core import DataError, LABELS, WAREHOUSES, allocations, calculate, load, now_jst, units, validate
from .planning import compare, comparison_text, overlay


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

    def latest_revision(self, dispatch, formal=True):
        return self.db.execute('SELECT COALESCE(MAX(revision),0) FROM issues WHERE dispatch=? AND formal=?',
                               (dispatch.isoformat(), int(formal))).fetchone()[0]

    def snapshot_data(self, raw, digest=None):
        with tempfile.TemporaryDirectory() as folder:
            for name, content in raw.items():
                if Path(name).name != name or not name.endswith('.csv'):
                    raise DataError(['取込原本のファイル名が不正'])
                Path(folder, name).write_text(content, encoding='utf-8')
            fresh = load(folder, self.clock())
        if digest is not None and fresh.digest != digest:
            raise DataError(['取込データが変更されています。再取込が必要'])
        return fresh

    def draft(self, number, dispatch=None):
        payload = self.report_payload(number)
        if payload['formal']:
            raise DataError(['正式指示は仮指示として再開できません'])
        if payload['report_status']['superseded']:
            raise DataError(['旧版の仮指示は再開・発行参照できません。最新の仮指示を選択してください'])
        if dispatch is not None and payload['dispatch'] != dispatch.isoformat():
            raise DataError(['再開元仮指示の出荷日が一致しません'])
        return payload

    def resume_draft(self, number):
        draft = self.draft(number)
        data = self.snapshot_data(draft['raw'], draft['digest'])
        dispatch = date.fromisoformat(draft['dispatch'])
        lines = overlay(calculate(data, dispatch), draft['lines'])
        self.event('resume_draft', {'number': number, 'digest': data.digest, 'lines': lines})
        return data, lines, dispatch

    def reconcile_draft(self, number, data, working_lines=None):
        draft = self.draft(number)
        dispatch = date.fromisoformat(draft['dispatch'])
        fresh = self.snapshot_data(data.raw, data.digest)
        # Compare both the persisted reference and working copy to current keys.
        current = calculate(fresh, dispatch)
        overlay(current, draft['lines'])
        lines = overlay(current, draft['lines'] if working_lines is None else working_lines)
        reference = compare(draft, fresh, lines)
        self.event('reconcile_draft', {'reference': reference, 'lines': lines})
        return lines

    def draft_comparison(self, number, data, lines):
        return compare(self.draft(number), data, lines)

    def issue(self, data, lines, dispatch, operator, formal, acknowledged=False, *, expected_revision=0, draft_number=None):
        # Reparse all typed values and recompute evidence, never reuse saved checks.
        fresh = self.snapshot_data(data.raw, data.digest)
        canonical = overlay(calculate(fresh, dispatch), lines)
        source = self.draft(draft_number, dispatch) if draft_number else None
        reference = compare(source, fresh, canonical) if source else None
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
        if reference:
            payload['source_draft'] = reference
        try:
            self.db.execute('BEGIN IMMEDIATE')
            if source and self.latest_revision(dispatch, False) != source['revision']:
                raise DataError(['再開元の仮指示が更新されました。最新の仮指示を再開し、再確認してください'])
            current = self.latest_revision(dispatch, formal)
            if formal and (type(expected_revision) is not int or expected_revision != current):
                raise DataError([f'出荷日={dispatch}: 正式指示の改訂が更新されています（計算時={expected_revision}、現在={current}）。再計算・再確認が必要です'])
            revision = current + 1
            payload['replaces_number'] = f'F-{dispatch:%Y%m%d}-R{current:03d}' if formal and current else None
            number = f'{"F" if formal else "P"}-{dispatch:%Y%m%d}-R{revision:03d}'
            payload.update(number=number, revision=revision)
            self.db.execute('INSERT INTO issues(dispatch,revision,formal,number,at,payload) VALUES(?,?,?,?,?,?)',
                            (dispatch.isoformat(), revision, int(formal), number, at, encode(payload)))
            self.db.execute('INSERT INTO events(at,kind,payload) VALUES(?,?,?)', (at, 'issue', encode(payload)))
            self.db.commit()
        except DataError as exc:
            self.db.rollback()
            self.event('issue_blocked', {'digest': fresh.digest, 'lines': canonical,
                                         'expected_revision': expected_revision, 'errors': exc.errors})
            raise
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

    def report_payload(self, number):
        """Add current status without modifying the immutable issuance snapshot."""
        row = self.db.execute('''SELECT old.payload, latest.number FROM issues old
            JOIN issues latest ON latest.dispatch=old.dispatch AND latest.formal=old.formal
            WHERE old.number=? ORDER BY latest.revision DESC LIMIT 1''', (number,)).fetchone()
        if row is None:
            raise DataError(['発行履歴がありません'])
        payload = json.loads(row[0])
        payload['report_status'] = {'latest_number': row[1], 'superseded': row[1] != number,
                                    'checked_at': self.clock().isoformat()}
        return payload


def report_html(payload, warehouse):
    esc = lambda v: html.escape(str(v), quote=True)
    formal = payload['formal']
    title = '正式出荷指示書' if formal else '仮出荷指示書'
    revision_status = payload.get('report_status')
    if revision_status is None:
        revision_notice = '現行改訂は未照合。出荷前にアプリの発行履歴で最新版を確認してください。'
    elif revision_status['superseded']:
        revision_notice = f'旧版・差替え済み — 出荷に使用しない。現行番号: {revision_status["latest_number"]}'
    else:
        revision_notice = f'履歴照合時点の最新改訂: {revision_status["latest_number"]}'
    if revision_status:
        revision_notice += f'（照合日時: {revision_status["checked_at"]}）'
    status_color = '#174d37' if revision_status and not revision_status['superseded'] else '#9b2600'
    products = {r['product']: r for r in payload['tables']['products']}
    stocks = {(r['product'], r['lot']): r for r in payload['tables']['dry_stock']}
    body = []
    for row in payload['lines']:
        if row['warehouse'] != warehouse:
            continue
        product = products[row['product']]
        assigned = allocations(row)
        allocation_total = sum(a['quantity_p'] for a in assigned)
        if 'allocations' in row:
            body.append(f'<tr><td colspan="8" class="basis">{esc(row["product"])} 指示合計 {row["quantity_p"]} P（{esc(units(row["quantity_p"], product))}）／ロット割当合計 {allocation_total} P</td></tr>')
        for allocation in assigned:
            stock = stocks.get((row['product'], allocation['lot']))
            values = [row['product'], product['name'], allocation['quantity_p'], units(allocation['quantity_p'], product),
                      allocation['lot'] or '未確認', stock['best_before'] if stock else '未確認', row['dispatch'], row['arrival']]
            body.append('<tr>' + ''.join(f'<td>{esc(v)}</td>' for v in values) + '</tr>')
            if stock:
                body.append(f'<tr><td colspan="8" class="basis">ロット {esc(allocation["lot"])} 現物確認 {esc(stock["checked_at"])}／{esc(stock["checker"])}</td></tr>')
        if not assigned:
            values = [row['product'], product['name'], row['quantity_p'], units(row['quantity_p'], product),
                      '未割当／合計不一致' if row['quantity_p'] else '出荷なし', '未確認', row['dispatch'], row['arrival']]
            body.append('<tr>' + ''.join(f'<td>{esc(v)}</td>' for v in values) + '</tr>')
        elif allocation_total != row['quantity_p']:
            difference = row['quantity_p'] - allocation_total
            body.append(f'<tr><td colspan="8" class="basis">ロット別数量の合計不一致: {"未割当" if difference > 0 else "割当超過"} {abs(difference)} P。正式指示は発行不可。</td></tr>')
        basis = (f'在庫 {row["inventory_p"]}P（{row["inventory_as_of"]}）、輸送中 {row["transit_p"]}P'
                 f'（網羅確認 {row["transit_checked_at"]}）、到着前予測 {row["before_p"]}P、'
                 f'対象予測 {row["demand_p"]}P（予測基準 {", ".join(row["forecast_as_of"])}）、'
                 f'安全在庫 {row["safety_p"]}P → 必要 {row["required_p"]}P → '
                 f'{row["rounding"]}推奨 {row["suggested_p"]}P → 指示 {row["quantity_p"]}P。{row["warning"]}')
        body.append(f'<tr><td colspan="8" class="basis">{esc(basis)}</td></tr>')
        timeline = row.get('timeline', [])
        if timeline:
            projection = ' / '.join(f'{r["date"]}: 始業予測残高 {r["opening_p"]}P + 輸送中到着 {r["transit_p"]}P - 需要予測 {r["forecast_p"]}P = 終業予測残高 {r["projected_closing_p"]}P' for r in timeline)
            body.append(f'<tr><td colspan="8" class="basis">今回補充前の日別参考値（確定在庫ではありません・日内の入庫順は未確認）: {esc(projection)}</td></tr>')
    status = ('担当者による実ロット・期限・専用車両重量の再確認済み。'
              if formal else '乾麺倉庫の当日在庫確認前。期限・積載等は以下の検証結果を参照。')
    warnings = '<br>'.join(esc(e) for e in payload['validation']) or '検証エラーなし'
    return f'''<!doctype html><html lang="ja"><meta charset="utf-8"><title>{esc(title)} {esc(payload['number'])}</title>
<style>body{{font-family:Meiryo,sans-serif;font-size:14px;margin:24px}}h1{{color:{'#174d37' if formal else '#9b5200'}}}table{{border-collapse:collapse;width:100%}}td,th{{border:1px solid #777;padding:7px}}.basis{{font-size:12px;background:#f3f3f3}}@media print{{button{{display:none}}body{{margin:0}}}}</style>
<h1>{esc(title)} — {esc(LABELS[warehouse])}</h1><p>{esc(status)}</p>
<p style="border:3px solid {status_color};padding:12px;font-size:18px;font-weight:bold">{esc(revision_notice)}</p>
<p>発行番号 {esc(payload['number'])}／改訂 {payload['revision']}／担当者 {esc(payload['operator'] or '未確認')}<br>
発行時刻 {esc(payload['at'])}／データ識別 {esc(payload['digest'][:16])}</p>
{('<h2>仮指示との照合</h2><p>' + esc(comparison_text(payload['source_draft'], warehouse)).replace(chr(10), '<br>') + '</p>') if payload.get('source_draft') else ''}
<p>同日正式指示は全倉庫一括の改訂差替えです。最新版を履歴画面で確認し、旧帳票を回収してください。追加出荷には使用できません。保存済みHTML・紙・PDFの改訂表示は自動更新されません。</p>
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
