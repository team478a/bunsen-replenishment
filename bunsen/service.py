from __future__ import annotations

import copy
import html
import json
import sqlite3
import tempfile
import csv
import io
import uuid
from datetime import date
from pathlib import Path

from .core import DataError, LABELS, WAREHOUSES, allocations, calculate, load, now_jst, units, validate, stamp
from .planning import compare, comparison_text, overlay
from .catalog import CatalogService, catalog_csv
from .jan_catalog import JanCatalog, master_csvs
from .requirements_data import active, PROFILE, NOTICE
from .forecast_service import ForecastService
from .common_settings import CommonSettings


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


class Service(CommonSettings, ForecastService, JanCatalog, CatalogService):
    def __init__(self, db_path, clock=now_jst):
        self.clock = clock
        self.db_path = Path(db_path).resolve()
        self.trial_only = False
        trial_marker = self.db_path.parent / 'trial-info.json'
        if trial_marker.exists():
            try:
                info = json.loads(trial_marker.read_text(encoding='utf-8'))
                if info != {'format': 'bunsen-trial-home', 'version': 1, 'trial_only': True}:
                    raise ValueError('試用識別が不正')
            except (OSError, ValueError) as exc:
                raise DataError(['試用DB識別を確認できません']) from exc
            self.trial_only = True
        self.read_only = (self.db_path.parent / 'restore-info.json').exists()
        if self.read_only:
            from .backup import validate_database
            self.db = sqlite3.connect(self.db_path.as_uri() + '?mode=ro', uri=True, timeout=10)
            try:
                self.db.execute('PRAGMA query_only=ON')
                validate_database(self.db)
                self.trial_only = self.trial_only or self.db.execute("SELECT 1 FROM events WHERE kind='trial_mode' LIMIT 1").fetchone() is not None
            except Exception:
                self.db.close()
                raise
            return
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.db_path, timeout=10)
        self.db.execute('CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY, at TEXT NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL)')
        self.db.execute('''CREATE TABLE IF NOT EXISTS issues (
            id INTEGER PRIMARY KEY, dispatch TEXT NOT NULL, revision INTEGER NOT NULL,
            formal INTEGER NOT NULL, number TEXT NOT NULL UNIQUE, at TEXT NOT NULL,
            payload TEXT NOT NULL, UNIQUE(dispatch, formal, revision))''')
        self.db.commit()
        recorded_trial = self.db.execute("SELECT 1 FROM events WHERE kind='trial_mode' LIMIT 1").fetchone() is not None
        if self.trial_only and not recorded_trial:
            self.event('trial_mode', {'trial_only': True})
        self.trial_only = self.trial_only or recorded_trial

    def close(self):
        self.db.close()

    def ensure_writable(self):
        if self.read_only:
            raise DataError(['復元履歴は閲覧専用です。取込・計算保存・再開・編集保存・新規発行はできません。通常データへ戻ってください'])

    def event(self, kind, payload):
        self.ensure_writable()
        with self.db:
            self.db.execute('INSERT INTO events(at,kind,payload) VALUES(?,?,?)',
                            (self.clock().isoformat(), kind, encode(payload)))

    def import_folder(self, folder, *, workflow=None, use_registered_products=False, use_registered_jan=False,use_common_settings=False):
        self.ensure_writable()
        if use_common_settings:
            if workflow!=2 or use_registered_products:raise DataError(['共通条件はJAN・物理PL方式で使用してください'])
            try:data=self.import_common_folder(folder,use_registered_jan)
            except (DataError,OSError,ValueError,KeyError,csv.Error) as exc:
                error=exc if isinstance(exc,DataError) else DataError(['在庫・予測・商品PLマスタを読み取れません: '+str(exc)])
                self.event('import_failed',{'errors':error.errors});raise error
            from .common_settings import FILE,parse
            payload=dict(digest=data.digest,raw=data.raw,common_settings_revision=parse(data.raw[FILE])['revision'])
            if data.jan_master_revision is not None:payload['jan_master_revision']=data.jan_master_revision
            self.event('import',payload);return data
        try:
            if workflow == 2 and not any((Path(folder)/name).is_file() for name in ('replenishment.csv','requirements.csv')):
                raise DataError(['replenishment.csv: 新方式の補充条件CSVが必要です。旧入力は履歴用です'])
            state=None;jan_state=None
            if use_registered_products:
                if workflow != 2:raise DataError(['登録商品マスタは新方式の取込で使用してください'])
                state=self.product_master()
                if not state['products']:raise DataError(['登録商品マスタがありません。商品マスタ画面で確認登録してください'])
            if use_registered_jan:
                if use_registered_products or not (Path(folder)/'requirements.csv').is_file():raise DataError(['JANマスタはrequirements.csv方式で使用してください'])
                jan_state=self.jan_master()
                if not jan_state['products']:raise DataError(['JAN・PLマスタがありません'])
                from .requirements_data import inspect
                checked=inspect(folder,self.clock(),master_csvs(jan_state['products']))
                if checked.issues:raise DataError(checked.issues)
                data=checked.dataset;data.jan_master_revision=jan_state['revision'];self.assert_jan_current(data)
            else:data = (load(folder,self.clock(),workflow=workflow,product_csv=catalog_csv(state['products']))
                    if state else load(folder,self.clock(),workflow=workflow))
            if state:
                data.product_master_revision=state['revision']
                self.assert_product_master_current(data)
            if workflow is not None and data.workflow != workflow:
                raise DataError(['指定した業務方式とCSV形式が一致しません'])
        except DataError as exc:
            self.event('import_failed', {'errors': exc.errors})
            raise
        payload={'digest': data.digest, 'raw': data.raw}
        if state:payload['product_master_revision']=state['revision']
        if jan_state:payload['jan_master_revision']=jan_state['revision']
        self.event('import', payload)
        return data

    def calculate(self, data, dispatch, *, allow_provisional=False):
        self.ensure_writable()
        self.assert_product_master_current(data)
        lines = calculate(data, dispatch, allow_provisional=allow_provisional)
        payload={'digest': data.digest, 'dispatch': dispatch.isoformat(), 'lines': lines}
        if data.product_master_revision is not None:payload['product_master_revision']=data.product_master_revision
        if data.jan_master_revision is not None:payload['jan_master_revision']=data.jan_master_revision
        self.event('calculate', payload)
        return lines

    def edit_quantity(self, data, lines, key, quantity, reason, operator):
        self.ensure_writable()
        self.assert_product_master_current(data)
        if data.workflow != 2 or type(quantity) is not int or quantity < 0 or not reason.strip() or not operator.strip():
            raise DataError(['修正には新方式データ・0以上の整数数量・修正理由・担当者が必要です'])
        row = next((r for r in lines if (r['warehouse'],r['product']) == key), None)
        if row is None:
            raise DataError(['修正対象がありません'])
        if quantity % row['unit_p']:
            raise DataError(['指示数量は計算行の出荷単位にしてください（JAN方式は物理PLのみ）'])
        before = copy.deepcopy(row)
        after = dict(row,quantity_p=quantity,edit_reason=reason.strip())
        self.event('edit',dict(digest=data.digest,before=before,after=after,operator=operator.strip(),reason=reason.strip()))
        row.update(after)

    def update_settings(self, data, key, values, reason, operator):
        self.ensure_writable()
        self.assert_product_master_current(data)
        allowed = {'cycle_days','lead_days','safety_days','coverage_days'}
        if data.workflow != 2 or set(values) != allowed or not reason.strip() or not operator.strip():
            raise DataError(['設定変更には4設定・変更理由・確認担当者が必要です'])
        if any(type(v) is not int or v < 0 for v in values.values()):
            raise DataError(['設定値は0以上の整数です。サイクル・対象日数は正整数です'])
        rows = copy.deepcopy(data.tables['replenishment'])
        row = next((r for r in rows if (r['warehouse'],r['product']) == key),None)
        if row is None:
            raise DataError(['設定対象がありません'])
        before = dict(row)
        row.update(values)
        stream = io.StringIO(newline='')
        writer = csv.DictWriter(stream,fieldnames=list(row),lineterminator='\n')
        writer.writeheader(); writer.writerows(rows)
        raw = dict(data.raw,**{('requirements.csv' if active(data) else 'replenishment.csv'):stream.getvalue()})
        fresh = self.snapshot_data(raw)
        fresh.product_master_revision=data.product_master_revision
        fresh.jan_master_revision=data.jan_master_revision
        self.event('settings_change',dict(before=before,after=row,reason=reason.strip(),operator=operator.strip(),
                                         before_digest=data.digest,digest=fresh.digest,raw=fresh.raw))
        return fresh

    def latest_revision(self, dispatch, formal=True):
        return self.db.execute('SELECT COALESCE(MAX(revision),0) FROM issues WHERE dispatch=? AND formal=?',
                               (dispatch.isoformat(), int(formal))).fetchone()[0]

    def update_product_expiry(self, data, product, expiry_type, shelf_life_days, reason, operator):
        self.ensure_writable()
        self.assert_product_master_current(data)
        if data.jan_master_revision is not None:raise DataError(['登録JANの期限変更はJAN・PLマスタ画面で保存し再取込してください'])
        if data.workflow != 2 or expiry_type not in ('best_before','use_by') or type(shelf_life_days) is not int or shelf_life_days <= 0 or not reason.strip() or not operator.strip():
            raise DataError(['期限マスタには種別・正整数日数・確認理由・担当者が必要です'])
        reader = csv.DictReader(io.StringIO(data.raw['products.csv']))
        fields, rows = list(reader.fieldnames), list(reader)
        for field in ('expiry_type','shelf_life_days'):
            if field not in fields:fields.insert(fields.index('confirmed'),field)
        row = next((r for r in rows if r['product']==product),None)
        if row is None:raise DataError(['期限マスタの対象商品がありません'])
        # Other products remain explicitly unknown until reviewed individually.
        before = dict(row);row.update(expiry_type=expiry_type,shelf_life_days=str(shelf_life_days))
        stream = io.StringIO(newline='');writer=csv.DictWriter(stream,fieldnames=fields,lineterminator='\n')
        writer.writeheader();writer.writerows(rows)
        fresh=self.snapshot_data(dict(data.raw,**{'products.csv':stream.getvalue()}))
        if data.product_master_revision is not None:
            values=next(dict(p) for p in fresh.tables['products'] if p['product']==product)
            values.pop('confirmed')
            state=self.save_product_master(values,reason,operator,True,expected_revision=data.product_master_revision,create=False)
            fresh.product_master_revision=state['revision']
        self.event('product_expiry_change',dict(product=product,before=before,after=row,reason=reason.strip(),operator=operator.strip(),
                                                before_digest=data.digest,digest=fresh.digest,raw=fresh.raw))
        return fresh

    def calculate_delivery_plan(self, data, start, *, allow_provisional=False):
        from .delivery import build_plan
        self.ensure_writable()
        self.assert_product_master_current(data)
        fresh=self.snapshot_data(data.raw,data.digest)
        report=build_plan(fresh,start,self.clock(),allow_provisional=allow_provisional)
        report.update(plan_id='DP-'+uuid.uuid4().hex,trial_only=self.trial_only)
        if data.product_master_revision is not None:report['product_master_revision']=data.product_master_revision
        if data.jan_master_revision is not None:report['jan_master_revision']=data.jan_master_revision
        self.event('delivery_plan',dict(report=report,raw=fresh.raw))
        return report

    def delivery_plan(self, plan_id):
        for kind,content in self.db.execute("SELECT kind,payload FROM events WHERE kind IN ('delivery_plan','delivery_plan_confirmed') ORDER BY id DESC"):
            saved=json.loads(content)
            if saved['report']['plan_id']==plan_id:
                report=saved['report']
                report['restored_view']=self.read_only
                return report
        raise DataError(['配送計画が見つかりません'])

    def delivery_plans(self):
        return [json.loads(r[0])['report'] for r in self.db.execute("SELECT payload FROM events WHERE kind='delivery_plan' ORDER BY id DESC LIMIT 100")]

    def confirm_delivery_plan(self, plan_id, operator, acknowledged=False):
        from .delivery import build_plan
        self.ensure_writable()
        if not operator.strip() or acknowledged is not True:
            raise DataError(['正式配送予定には担当者と明示確認が必要です'])
        # Serialize confirmations from concurrent application instances.
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            if self.delivery_plan(plan_id)['formal']:
                raise DataError(['この配送予定は確定済みです'])
            saved=next((json.loads(r[0]) for r in self.db.execute("SELECT payload FROM events WHERE kind='delivery_plan'")
                        if json.loads(r[0])['report']['plan_id']==plan_id),None)
            if saved is None:raise DataError(['配送計画の原本がありません'])
            report=saved['report']
            if report.get('jan_master_revision') is not None and report['jan_master_revision']!=self.jan_master()['revision']:raise DataError(['JAN・PLマスタが更新されています。配送計画を再計算してください'])
            if report.get('product_master_revision') is not None and report['product_master_revision']!=self.product_master()['revision']:
                raise DataError(['登録商品マスタが変更されています。配送計画を再取込・再計算・再確認してください'])
            if date.fromisoformat(report['start']) < stamp(self.clock().isoformat()).date():
                raise DataError(['過去日を含む配送予定は確定できません。新たに計算してください'])
            fresh=self.snapshot_data(saved['raw'],report['digest'])
            self.assert_common_settings_current(fresh)
            self.assert_jan_current(fresh)
            verified=build_plan(fresh,date.fromisoformat(report['start']),stamp(report['calculated_at']),allow_provisional=report['allow_provisional'])
            for key,value in verified.items():
                if report.get(key)!=value:raise DataError(['保存計画と原本再計算が一致しません'])
            if not verified['forecast_confirmed']:raise DataError(['未確定予測を使用した配送予定は正式確定できません'])
            report.update(formal=True,operator=operator.strip(),confirmed_at=self.clock().isoformat())
            self.db.execute('INSERT INTO events(at,kind,payload) VALUES(?,?,?)',
                            (self.clock().isoformat(),'delivery_plan_confirmed',encode(dict(report=report,raw=saved['raw']))))
        return report

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
        self.ensure_writable()
        draft = self.draft(number)
        if draft.get('profile')==PROFILE:raise DataError(['JAN・PL方式の参考指示は保存原本の閲覧・出力用です。修正は再取込・再計算してください'])
        data = self.snapshot_data(draft['raw'], draft['digest'])
        data.product_master_revision=draft.get('product_master_revision')
        self.assert_product_master_current(data)
        dispatch = date.fromisoformat(draft['dispatch'])
        lines = overlay(calculate(data, dispatch), draft['lines'])
        self.event('resume_draft', {'number': number, 'digest': data.digest, 'lines': lines})
        return data, lines, dispatch

    def reconcile_draft(self, number, data, working_lines=None):
        self.ensure_writable()
        self.assert_product_master_current(data)
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
        self.ensure_writable()
        self.assert_product_master_current(data)
        # Reparse all typed values and recompute evidence, never reuse saved checks.
        fresh = self.snapshot_data(data.raw, data.digest)
        if fresh.workflow == 2:
            from .replenishment import overlay as overlay_v2
            provisional = any(r.get('allow_provisional',False) for r in lines)
            canonical = overlay_v2(calculate(fresh, dispatch, allow_provisional=provisional), lines)
        else:
            canonical = overlay(calculate(fresh, dispatch), lines)
        source = self.draft(draft_number, dispatch) if draft_number else None
        reference = compare(source, fresh, canonical) if source else None
        errors = validate(fresh, canonical, dispatch, self.clock())
        if formal and (not operator.strip() or not acknowledged):
            errors.append('担当者名と補充数量・予測区分の確認操作が必要' if fresh.workflow == 2 else '担当者名と実ロット・期限・積載の再確認操作が必要')
        if formal and errors:
            self.event('issue_blocked', {'digest': data.digest, 'lines': canonical, 'errors': errors})
            raise DataError(errors)
        at = self.clock().isoformat()
        payload = {'digest': fresh.digest, 'raw': fresh.raw, 'tables': fresh.tables,
                   'lines': canonical, 'operator': operator, 'formal': formal,
                   'validation': errors, 'dispatch': dispatch.isoformat(), 'at': at}
        if fresh.workflow == 2:
            payload.update(workflow=2,meaning='replenishment_instruction_not_shipment_actual',settings=fresh.tables['replenishment'])
            from .product_scope import FILE,parse
            if FILE in fresh.raw:payload['product_scope']=parse(fresh.raw[FILE])
            if active(fresh):payload.update(profile=PROFILE,inbound_scope='excluded',notice=NOTICE)
            from .forecast_csv import metadata
            if metadata(fresh.raw) is not None:payload['forecast_source']=metadata(fresh.raw)
            if data.jan_master_revision is not None:payload['jan_master_revision']=data.jan_master_revision
            if data.product_master_revision is not None:payload['product_master_revision']=data.product_master_revision
        if reference:
            payload['source_draft'] = reference
        if self.trial_only:
            payload['trial_only'] = True
        try:
            self.db.execute('BEGIN IMMEDIATE')
            self.assert_product_master_current(data)
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
        if self.read_only:
            payload['restored_view'] = True
        if self.trial_only:
            payload['trial_only'] = True
        return payload


def report_html(payload, warehouse):
    if payload.get('workflow') == 2:
        from .shipping_output import report_html as v2_report
        return v2_report(payload,warehouse)
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
{'<p style="border:4px solid #9b2600;padding:12px;font-size:24px;font-weight:bold">架空データの試用帳票 — 実出荷に使用しない。現場受入・業務承認の完了を示しません。</p>' if payload.get('trial_only') else ''}
{'<p style="border:4px solid #9b2600;padding:12px;font-size:22px;font-weight:bold">復元履歴の参考帳票 — 実出荷に使用しない。改訂照合は復元時点の履歴内のみです。</p>' if payload.get('restored_view') else ''}
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
