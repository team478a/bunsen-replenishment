"""Read-only pre-import diagnostics against the provisional v0 contract."""
from dataclasses import asdict
import hashlib
import html
import json
from pathlib import Path

from .core import (DataError, NUMBERS, POSITIVE, SCHEMA, TIMESTAMPS, DATES,
                   ValidationIssue, calculate, inspect_folder, morning_stock_issues, now_jst)
from .ingest import ENUMS

PHASE_LABELS = {'contract': '入力契約', 'calculation': '計算条件', 'morning_stock': '朝現物在庫'}
STATUS_LABELS = {'missing': '未取得', 'checked': '読取・構造チェック済み', 'invalid': '不備あり',
                 'unsupported': '未対応', 'header_error': '列不一致', 'encoding_error': '文字コード確認不能', 'read_error': '読取エラー',
                 'source_changed': '読取中変更／照合不能'}
READ_LABELS = {'not_checked': '未実施', 'stable_observed': '前後の観測で変更検出なし（同一バッチ保証ではありません）',
               'changed': '変更検出・取込停止', 'unverifiable': '照合不能・取込停止'}

LIMITATIONS = [
    '既存の実CSV仕様・項目対応・業務設定の承認は未確認です。診断対象は仮データ契約v0です。',
    '入力CSV・業務DB・取込済みデータは変更していません。診断はデータ取込ではありません。',
    '実ロット選択後の数量・期限・積載の出荷検証は未実施です。正式発行前に必ず再検証します。',
    'CSVの確認済み申告だけで現物確認の真正性・全ロットの網羅性を証明することはできません。',
    '前後のCSV照合は原子的取得や同一業務バッチの証明ではありません。取込前の世代混在・観測間の完全復元は検出できない場合があります。',
]


def diagnose(folder, dispatch, now=None):
    now = now or now_jst()
    inspection = inspect_folder(folder, now)
    issues = list(inspection.issues)
    skipped = list(inspection.skipped_checks)
    calculation_checked, calculation_possible, morning_checked = False, False, False
    warnings = []
    if inspection.dataset is not None:
        calculation_checked = True
        try:
            lines = calculate(inspection.dataset, dispatch)
            calculation_possible = True
            warnings = [dict(warehouse=row['warehouse'], product=row['product'], date=row['arrival'], message=row['warning'])
                        for row in lines if row['warning']]
        except DataError as exc:
            issues.extend(exc.issues or [ValidationIssue('計算条件', None, 'CALCULATION_FAILURE', message, phase='calculation') for message in exc.errors])
        issues.extend(morning_stock_issues(inspection.dataset, dispatch, now))
        morning_checked = True
    else:
        skipped.extend(['計算条件チェック（入力不備のため未実施）', '朝現物在庫条件チェック（入力不備のため未実施）'])
    files = inspection.files
    fingerprint = hashlib.sha256(json.dumps([(f['filename'], f['exists'], f['sha256']) for f in files], ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    return dict(format='bunsen-csv-diagnostic', version=1, schema='v0-provisional',
                checked_at=now.isoformat(), input_folder=str(Path(folder).resolve()),
                dispatch=dispatch.isoformat(), input_fingerprint=fingerprint,
                dataset_digest=inspection.dataset.digest if inspection.dataset else None,
                actual_mapping_status='unconfirmed', formal_issue_allowed=False,
                read_consistency=inspection.read_consistency,
                contract_compatible=inspection.dataset is not None,
                calculation_checked=calculation_checked, calculation_possible=calculation_possible,
                morning_stock_checked=morning_checked,
                status='issues_found' if issues else 'no_detected_input_issues',
                issues=[asdict(issue) for issue in issues], files=files,
                warnings=warnings, skipped_checks=skipped, limitations=list(LIMITATIONS))


def target_rule(field):
    if field in POSITIVE:
        return '正整数。商品別換算・重量等は確認済みマスタを使用'
    if field in NUMBERS:
        return '0以上の整数。空欄・未確認をゼロで補完しない'
    if field in TIMESTAMPS:
        return 'タイムゾーン付きISO8601基準日時。確認手順・締時刻の実対応は未確認'
    if field in DATES:
        return 'YYYY-MM-DD。出荷日・到着日・予測対象日を区別'
    if field == 'confirmed':
        return '確認済みのみyes。未確認を確認済みに変換しない'
    if field in ENUMS:
        return '仮契約の許容値: ' + ', '.join(ENUMS[field])
    return '空欄不可。コード対応・意味は業務側で確認'


def mapping_template():
    return dict(format='bunsen-csv-mapping-review', version=1, target_schema='v0-provisional',
                status='unconfirmed', owner=None, approved_at=None,
                note='確認用のひな形です。取込設定として自動適用しません。未確認値は推測で埋めません。',
                tables=[dict(target_file=name + '.csv', source_file=None, source_encoding=None,
                             source_time_basis=None, confirmation_owner=None,
                             fields=[dict(target_field=field, target_rule=target_rule(field),
                                          source_field=None, source_unit=None, conversion_rule=None,
                                          confirmed_by=None, confirmed_at=None, status='unconfirmed')
                                     for field in columns.split(',')])
                        for name, columns in SCHEMA.items()])


def report_html(report):
    esc = lambda value: html.escape(str(value), quote=True)
    state = '不備あり' if report['issues'] else '仮契約の入力不備は検出されませんでした'
    checks = [('仮契約の入力検証', report['contract_compatible']), ('計算条件を確認できた', report['calculation_possible']),
              ('朝現物在庫条件のチェック実施', report['morning_stock_checked'])]
    rows = []
    for issue in report['issues']:
        values = [PHASE_LABELS[issue['phase']], issue['filename'], issue['line'] if issue['line'] is not None else '—',
                  issue['product'] or '—', issue['warehouse'] or '—', issue['date'] or '—',
                  issue['lot'] or '—', issue['field'] or '—',
                  '未取得' if issue['value'] is None else ('空欄' if issue['value'] == '' else issue['value']),
                  issue['code'], issue['message']]
        rows.append('<tr>' + ''.join(f'<td>{esc(value)}</td>' for value in values) + '</tr>')
    inventory = []
    for info in report['files']:
        values = [info['filename'], STATUS_LABELS[info['status']], info['rows'] if info['rows'] is not None else '未取得',
                  ', '.join(info['headers']) if info['headers'] is not None else '未取得', info['sha256'] or '未取得',
                  json.dumps(info['basis_times'], ensure_ascii=False)]
        inventory.append('<tr>' + ''.join(f'<td>{esc(value)}</td>' for value in values) + '</tr>')
    warnings = [f'{row["warehouse"]} {row["product"]}: {row["message"]}' for row in report['warnings']]
    read_check = report.get('read_consistency', {'status': 'not_checked', 'atomic_snapshot': False})
    return f'''<!doctype html><html lang="ja"><meta charset="utf-8"><title>取込前診断レポート</title>
<style>body{{font-family:Meiryo,sans-serif;font-size:14px;margin:24px}}table{{border-collapse:collapse;width:100%;font-size:12px}}td,th{{border:1px solid #777;padding:6px;overflow-wrap:anywhere}}.notice{{border:3px solid #a54000;padding:12px;font-weight:bold}}@media print{{button{{display:none}}thead{{display:table-header-group}}}}</style>
<h1>取込前診断レポート — {esc(state)}</h1><p class="notice">診断結果は正式出荷指示ではありません。正式発行可の判定や過去の確認の再利用には使用できません。実CSV接続・業務承認は未確認です。</p>
<p>診断日時 {esc(report['checked_at'])}／対象出荷日 {esc(report['dispatch'])}<br>対象 {esc(report['input_folder'])}<br>入力識別 {esc(report['input_fingerprint'])}</p>
<button onclick="window.print()">印刷・PDFに保存</button><h2>実施状況</h2><ul>{''.join('<li>'+esc(label)+': '+('はい' if value else 'いいえ')+'</li>' for label,value in checks)}</ul>
<h2>未実施・制約</h2><ul>{''.join('<li>'+esc(value)+'</li>' for value in report['skipped_checks'] + report['limitations'])}</ul>
<h2>読取中のCSV変更検査</h2><p>{esc(READ_LABELS[read_check['status']])}。原子的な一括取得ではありません。</p><pre>{esc(json.dumps(read_check, ensure_ascii=False, indent=2))}</pre>
<h2>不備一覧（{len(rows)}件）</h2><table><thead><tr>{''.join('<th>'+label+'</th>' for label in ['区分','ファイル','行','商品','倉庫','対象日／基準日時','ロット','項目','記録値','コード','理由'])}</tr></thead><tbody>{''.join(rows)}</tbody></table>
<h2>ファイル・列・基準日時の観察結果</h2><p>行数は読取できた行数です。エラー時は全件を読めたことを意味しません。基準日時はCSV記載値で、業務上の承認を示しません。</p><table><thead><tr>{''.join('<th>'+label+'</th>' for label in ['ファイル','状態','読取行数','観察した列名','原本SHA-256','記載された基準日時'])}</tr></thead><tbody>{''.join(inventory)}</tbody></table>
<h2>予測に基づく参考警告</h2><p>{'<br>'.join(esc(value) for value in warnings) or '検出なし／入力不備時は未実施'}</p></html>'''


def _write_outputs(outputs, protected_folder=None):
    paths = [Path(path).resolve() for path, _ in outputs]
    if len(set(paths)) != len(paths):
        raise DataError(['出力先が重複しています'])
    for path in paths:
        if path.exists():
            raise DataError(['出力は上書きしません。別名を指定してください'])
        if protected_folder is not None and path.is_relative_to(Path(protected_folder).resolve()):
            raise DataError(['入力フォルダー内には結果を保存できません'])
    created = []
    try:
        for path, (_, content) in zip(paths, outputs):
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open('x', encoding='utf-8') as stream:
                created.append(path)
                stream.write(content)
    except Exception:
        for path in created:
            path.unlink(missing_ok=True)
        raise
    return paths


def export_report(report, json_path):
    json_path = Path(json_path)
    if json_path.suffix.lower() != '.json':
        raise DataError(['診断結果の保存先は.jsonを指定してください'])
    return _write_outputs([(json_path, json.dumps(report, ensure_ascii=False, indent=2)),
                           (json_path.with_suffix('.html'), report_html(report))], report['input_folder'])


def export_template(path, protected_folder=None):
    path = Path(path)
    if path.suffix.lower() != '.json':
        raise DataError(['対応表の保存先は.jsonを指定してください'])
    return _write_outputs([(path, json.dumps(mapping_template(), ensure_ascii=False, indent=2))], protected_folder)[0]
