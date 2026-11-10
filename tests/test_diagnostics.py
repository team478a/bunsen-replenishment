import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

import test_workflow as fixtures
from test_workflow import DAY, NOW, ROOT
from bunsen.core import DataError, SCHEMA, calculate, inspect_folder, load
from bunsen.diagnostics import diagnose, export_report, export_template, mapping_template, report_html


class DiagnosticTests(unittest.TestCase):
    setUp = fixtures.WorkflowTests.setUp
    tearDown = fixtures.WorkflowTests.tearDown
    change = fixtures.WorkflowTests.change

    def inspect(self):
        return inspect_folder(self.folder, NOW)

    def test_valid_zero_input_passes_provisional_contract_but_never_authorizes_issue(self):
        result = diagnose(self.folder, DAY, NOW)
        self.assertTrue(result['contract_compatible'])
        self.assertTrue(result['calculation_possible'])
        self.assertTrue(result['morning_stock_checked'])
        self.assertEqual(result['issues'], [])
        self.assertFalse(result['formal_issue_allowed'])
        self.assertEqual(result['actual_mapping_status'], 'unconfirmed')
        self.assertEqual(load(self.folder, NOW).tables['inventory'][0]['quantity_p'], 0)
        self.assertIn('正式発行可', report_html(result))

    def test_all_bad_fields_in_one_row_are_identified(self):
        self.change('inventory', lambda rows: rows[0].update(quantity_p='', as_of='bad date', confirmed='no'))
        inspection = self.inspect()
        problems = [issue for issue in inspection.issues if issue.filename == 'inventory.csv' and issue.line == 2]
        self.assertEqual({issue.field for issue in problems}, {'quantity_p', 'as_of', 'confirmed'})
        self.assertTrue(all(issue.product == 'P001' and issue.warehouse == 'KOBE' for issue in problems))
        self.assertTrue(all(issue.date == 'bad date' for issue in problems))
        self.assertIsNone(inspection.dataset)
        with self.assertRaises(DataError) as failure:
            load(self.folder, NOW)
        self.assertEqual(failure.exception.issues, inspection.issues)
        result = diagnose(self.folder, DAY, NOW)
        self.assertFalse(result['calculation_checked'])
        self.assertFalse(result['morning_stock_checked'])
        self.assertTrue(result['skipped_checks'])

    def test_zero_and_empty_are_not_interchangeable(self):
        self.change('inventory', lambda rows: rows[0].update(quantity_p=''))
        result = diagnose(self.folder, DAY, NOW)
        issue = next(issue for issue in result['issues'] if issue['field'] == 'quantity_p')
        self.assertEqual(issue['value'], '')
        self.assertEqual(issue['code'], 'VALUE_MISSING')
        self.assertIn('空欄', report_html(result))

    def test_unknown_product_warehouse_and_duplicate_key_are_separate(self):
        self.change('inventory', lambda rows: rows.append(dict(rows[0])))
        self.change('transit', lambda rows: rows.append(dict(shipment_id='T001', warehouse='UNKNOWN', product='UNKNOWN', quantity_p='0', arrival_date='2026-11-10', confirmed='yes')))
        problems = self.inspect().issues
        self.assertTrue({'KEY_DUPLICATE', 'PRODUCT_UNKNOWN', 'WAREHOUSE_UNKNOWN'} <= {issue.code for issue in problems})

    def test_header_missing_unknown_duplicate_and_order_are_explicit(self):
        path = self.folder / 'inventory.csv'
        original = path.read_text(encoding='utf-8')
        for header, codes in [
            ('warehouse,product,quantity_p,as_of', {'HEADER_MISSING'}),
            ('warehouse,product,quantity_p,as_of,confirmed,order_quantity', {'HEADER_UNKNOWN'}),
            ('warehouse,product,quantity_p,as_of,confirmed,confirmed', {'HEADER_DUPLICATE'}),
            ('product,warehouse,quantity_p,as_of,confirmed', {'HEADER_ORDER'}),
        ]:
            with self.subTest(header=header):
                path.write_text(header + '\n' + '\n'.join(original.splitlines()[1:]), encoding='utf-8')
                self.assertTrue(codes <= {issue.code for issue in self.inspect().issues})

    def test_unknown_csv_and_wrong_case_are_not_auto_mapped(self):
        (self.folder / 'inventory.csv').rename(self.folder / 'inventory-renaming.tmp')
        (self.folder / 'inventory-renaming.tmp').rename(self.folder / 'inventory.CSV')
        (self.folder / 'inventory.CSV').write_text('実倉庫,実商品,実数量\n神戸,架空品,0\n', encoding='utf-8')
        result = diagnose(self.folder, DAY, NOW)
        self.assertIn('UNSUPPORTED_FILE', {issue['code'] for issue in result['issues']})
        observed = next(info for info in result['files'] if info['filename'] == 'inventory.CSV')
        self.assertEqual(observed['headers'], ['実倉庫', '実商品', '実数量'])
        self.assertFalse(observed['expected'])
        self.assertIsNone(self.inspect().dataset)

    def test_encoding_is_reported_without_guessing_cp932(self):
        (self.folder / 'products.csv').write_bytes('商品,数量\n架空,0\n'.encode('cp932'))
        result = diagnose(self.folder, DAY, NOW)
        self.assertTrue(any(issue['code'] == 'ENCODING' and issue['field'] == 'encoding' for issue in result['issues']))
        self.assertFalse(result['contract_compatible'])

    def test_bom_crlf_and_quoted_newlines_keep_existing_digest_behavior(self):
        data = load(self.folder, NOW)
        path = self.folder / 'products.csv'
        original = path.read_text(encoding='utf-8')
        path.write_bytes(b'\xef\xbb\xbf' + original.replace('\n', '\r\n').encode('utf-8'))
        self.assertEqual(load(self.folder, NOW).digest, data.digest)
        self.change('products', lambda rows: rows[0].update(name='架空\n商品'))
        inspection = self.inspect()
        self.assertIsNotNone(inspection.dataset)
        self.assertEqual(inspection.dataset.tables['products'][0]['name'], '架空\n商品')

    def test_row_width_and_csv_syntax_do_not_hide_other_files(self):
        with (self.folder / 'inventory.csv').open('a', encoding='utf-8') as stream:
            stream.write('KOBE,P001\n')
        (self.folder / 'forecast.csv').write_text('"unterminated', encoding='utf-8')
        inspection = self.inspect()
        self.assertTrue({'ROW_WIDTH', 'CSV_READ'} <= {issue.code for issue in inspection.issues})
        self.assertTrue(any(issue.field == 'quantity_p' and issue.code == 'VALUE_MISSING' for issue in inspection.issues))

    def test_calculation_issues_have_fields_dates_and_rows(self):
        self.change('forecast', lambda rows: rows.pop(1))
        self.change('policies', lambda rows: rows[0].update(transit_checked_at='2026-11-08T09:00:00+09:00'))
        result = diagnose(self.folder, DAY, NOW)
        self.assertTrue(result['contract_compatible'])
        self.assertFalse(result['calculation_possible'])
        missing = next(issue for issue in result['issues'] if issue['code'] == 'FORECAST_MISSING')
        self.assertEqual((missing['warehouse'], missing['product'], missing['date'], missing['field']), ('KOBE', 'P001', '2026-11-09', 'quantity_p'))
        snapshot = next(issue for issue in result['issues'] if issue['code'] == 'TRANSIT_SNAPSHOT')
        self.assertEqual(snapshot['line'], 2)
        self.assertEqual(snapshot['phase'], 'calculation')

    def test_large_missing_period_is_grouped_and_date_overflow_is_blocked(self):
        self.change('policies', lambda rows: rows[0].update(coverage_days='1000000'))
        result = diagnose(self.folder, DAY, NOW)
        missing = [issue for issue in result['issues'] if issue['code'] == 'FORECAST_MISSING']
        self.assertLessEqual(len(missing), 2)
        self.assertFalse(result['calculation_possible'])
        self.change('warehouses', lambda rows: rows[0].update(lead_days='99999999999999999999'))
        result = diagnose(self.folder, DAY, NOW)
        self.assertIn('DATE_RANGE', {issue['code'] for issue in result['issues']})

    def test_before_morning_is_importable_but_formal_stock_is_missing(self):
        result = diagnose(ROOT / 'samples/before_morning', DAY, NOW)
        self.assertTrue(result['contract_compatible'])
        self.assertTrue(result['calculation_possible'])
        issue = next(issue for issue in result['issues'] if issue['code'] == 'MORNING_MISSING')
        self.assertEqual((issue['phase'], issue['product'], issue['warehouse']), ('morning_stock', 'P001', 'DRY'))
        self.assertFalse(result['formal_issue_allowed'])

    def test_previous_day_stock_is_not_reconfirmed_by_diagnosis(self):
        self.change('dry_stock', lambda rows: rows[0].update(checked_at='2026-11-08T07:00:00+09:00'))
        result = diagnose(self.folder, DAY, NOW)
        issue = next(issue for issue in result['issues'] if issue['code'] == 'MORNING_TIME')
        self.assertEqual((issue['lot'], issue['field'], issue['line']), ('L001', 'checked_at', 2))

    def test_reports_escape_values_and_never_change_csv_or_database(self):
        self.change('inventory', lambda rows: rows[0].update(quantity_p='<script>alert(1)</script>'))
        before = {path.name: (path.read_bytes(), path.stat().st_mtime_ns) for path in self.folder.iterdir()}
        db_before = self.service.db.execute('SELECT COUNT(*) FROM events').fetchone()[0]
        report = diagnose(self.folder, DAY, NOW)
        target = Path(self.temp.name) / 'reports/diagnostic.json'
        paths = export_report(report, target)
        self.assertEqual(json.loads(paths[0].read_text(encoding='utf-8')), report)
        self.assertIn('&lt;script&gt;', paths[1].read_text(encoding='utf-8'))
        self.assertNotIn('<script>', paths[1].read_text(encoding='utf-8'))
        self.assertEqual({path.name: (path.read_bytes(), path.stat().st_mtime_ns) for path in self.folder.iterdir()}, before)
        self.assertEqual(self.service.db.execute('SELECT COUNT(*) FROM events').fetchone()[0], db_before)

    def test_report_overwrite_input_folder_and_partial_publication_are_blocked(self):
        report = diagnose(self.folder, DAY, NOW)
        with self.assertRaisesRegex(DataError, '入力フォルダー'):
            export_report(report, self.folder / 'report.json')
        target = Path(self.temp.name) / 'report.json'
        target.with_suffix('.html').write_text('keep', encoding='utf-8')
        with self.assertRaisesRegex(DataError, '上書き'):
            export_report(report, target)
        self.assertFalse(target.exists())
        self.assertEqual(target.with_suffix('.html').read_text(encoding='utf-8'), 'keep')
        target.with_suffix('.html').unlink()
        original = Path.open
        def fail_second(path, *args, **kwargs):
            if path.suffix == '.html':
                raise OSError('disk full')
            return original(path, *args, **kwargs)
        with patch('pathlib.Path.open', new=fail_second):
            with self.assertRaises(OSError):
                export_report(report, target)
        self.assertFalse(target.exists())

    def test_template_covers_every_target_but_all_real_source_fields_are_unconfirmed(self):
        template = mapping_template()
        self.assertEqual(template['status'], 'unconfirmed')
        for table in template['tables']:
            self.assertIsNone(table['source_file'])
            self.assertIsNone(table['source_encoding'])
            expected = SCHEMA[table['target_file'][:-4]].split(',')
            self.assertEqual([field['target_field'] for field in table['fields']], expected)
            self.assertTrue(all(field['source_field'] is None and field['source_unit'] is None and field['conversion_rule'] is None and field['status'] == 'unconfirmed' for field in table['fields']))
        path = Path(self.temp.name) / 'mapping.json'
        export_template(path)
        self.assertEqual(json.loads(path.read_text(encoding='utf-8')), template)
        with self.assertRaises(DataError):
            export_template(path)

    def test_cli_reports_and_template_do_not_create_business_database(self):
        root = Path(self.temp.name)
        output = root / 'cli/diagnostic.json'
        env = dict(os.environ, LOCALAPPDATA=str(root / 'unused'), PYTHONIOENCODING='cp1252')
        result = subprocess.run([sys.executable, '-m', 'bunsen.tools', 'diagnose', str(ROOT / 'samples/before_morning'), '--dispatch', '2026-11-09', '--output', str(output)], cwd=ROOT, env=env, capture_output=True, encoding='utf-8', timeout=30)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertTrue(output.exists() and output.with_suffix('.html').exists())
        self.assertFalse(json.loads(output.read_text(encoding='utf-8'))['formal_issue_allowed'])
        mapping = root / 'cli/mapping.json'
        result = subprocess.run([sys.executable, '-m', 'bunsen.tools', 'mapping-template', '--output', str(mapping)], cwd=ROOT, env=env, capture_output=True, encoding='utf-8', timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((root / 'unused').exists())
