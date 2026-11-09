import copy
import csv
import io
import json
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path

from bunsen.core import DataError, JST, calculate, case_p, load, shortages, unit_p, units, validate
from bunsen.service import Service, report_html, write_reports

ROOT = Path(__file__).resolve().parent.parent
NOW = datetime(2026, 11, 9, 9, tzinfo=JST)
DAY = date(2026, 11, 9)


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name) / 'csv'
        self.folder.mkdir()
        for path in (ROOT / 'samples/morning').glob('*.csv'):
            (self.folder / path.name).write_bytes(path.read_bytes())
        self.service = Service(Path(self.temp.name) / 'history.db', lambda: NOW)

    def tearDown(self):
        self.service.close()
        self.temp.cleanup()

    def change(self, table, change):
        path = self.folder / f'{table}.csv'
        reader = csv.DictReader(io.StringIO(path.read_text(encoding='utf-8')))
        columns = reader.fieldnames
        rows = list(reader)
        change(rows)
        with path.open('w', encoding='utf-8', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=columns)
            writer.writeheader()
            writer.writerows(rows)

    def data(self):
        return self.service.import_folder(self.folder)

    def valid(self):
        data = self.data()
        lines = self.service.calculate(data, DAY)
        for row in lines:
            row.update(quantity_p=120, lot='L001')
        return data, lines

    def test_full_flow_and_persistence(self):
        before = self.service.import_folder(ROOT / 'samples/before_morning')
        initial = self.service.calculate(before, DAY)
        draft = self.service.issue(before, initial, DAY, '', False)
        self.assertIn('乾麺倉庫の当日在庫確認前', report_html(draft, 'KOBE'))
        self.assertIn('未確認', report_html(draft, 'KOBE'))
        with self.assertRaises(DataError):
            self.service.issue(before, initial, DAY, '担当者', True, True)
        data, lines = self.valid()
        self.assertEqual([r['suggested_p'] for r in lines], [240, 240])
        self.assertIn('不足 240 P', shortages(data, calculate(data, DAY))[0])
        self.assertEqual(validate(data, lines, DAY, NOW), [])
        final = self.service.issue(data, lines, DAY, '担当者', True, True)
        paths = write_reports(final, Path(self.temp.name) / 'reports')
        self.assertEqual(len(paths), 2)
        self.assertIn('正式出荷指示書', paths[0].read_text(encoding='utf-8'))
        self.assertEqual(final, self.service.get_issue(final['number']))
        self.assertEqual(final['raw'], data.raw)
        self.assertEqual(final['lines'][0]['quantity_p'], 120)
        self.assertEqual(final['lines'][0]['suggested_p'], 240)

    def test_shared_lot_cannot_be_allocated_twice(self):
        data, lines = self.valid()
        for row in lines:
            row['quantity_p'] = 240
        self.assertTrue(any('二重割当' in e for e in validate(data, lines, DAY, NOW)))
        with self.assertRaises(DataError):
            self.service.issue(data, lines, DAY, '担当者', True, True)
        self.assertEqual(self.service.history(), [])

    def test_missing_is_not_zero(self):
        self.assertEqual(self.data().tables['inventory'][0]['quantity_p'], 0)
        self.change('inventory', lambda r: r[0].update(quantity_p=''))
        with self.assertRaisesRegex(DataError, '商品=P001.*倉庫=KOBE.*欠損'):
            self.data()

    def test_unconfirmed_is_not_zero(self):
        self.change('inventory', lambda r: r[0].update(confirmed='no'))
        with self.assertRaisesRegex(DataError, '未確認'):
            self.data()

    def test_product_specific_conversion(self):
        a = self.data().tables['products'][0]
        b = dict(a, p_per_ball=6, balls_per_case=3, cases_per_pl=8, ship_unit='CASE')
        self.assertEqual(case_p(a), 24)
        self.assertEqual(unit_p(a), 240)
        self.assertEqual(unit_p(b), 18)
        self.assertEqual(units(264, a), '1 PL + 1 甲')
        self.assertEqual(units(162, b), '1 PL + 1 甲')

    def test_rounding_both_policies(self):
        self.change('policies', lambda r: [x.update(safety_p='1') for x in r])
        self.assertEqual(calculate(self.data(), DAY)[0]['suggested_p'], 480)
        self.change('policies', lambda r: [x.update(rounding='floor') for x in r])
        self.assertEqual(calculate(self.data(), DAY)[0]['suggested_p'], 240)

    def test_expiry_uses_arrival(self):
        self.change('dry_stock', lambda r: r[0].update(best_before='2026-12-09'))
        data, lines = self.valid()
        self.assertTrue(any('期限違反' in e for e in validate(data, lines, DAY, NOW)))
        self.change('policies', lambda r: [x.update(expiry_basis='shipment') for x in r])
        data, lines = self.valid()
        self.assertEqual(validate(data, lines, DAY, NOW), [])

    def test_capacity_and_case_remainder(self):
        self.change('vehicles', lambda r: r[0].update(capacity_g='11999'))
        data, lines = self.valid()
        self.assertTrue(any('積載超過' in e for e in validate(data, lines, DAY, NOW)))
        lines[0]['quantity_p'] = 119
        self.assertTrue(any('端数' in e for e in validate(data, lines, DAY, NOW)))

    def test_no_morning_stock(self):
        (self.folder / 'dry_stock.csv').unlink()
        data = self.data()
        self.assertTrue(any('当日朝' in e for e in validate(data, calculate(data, DAY), DAY, NOW)))

    def test_previous_day_stock_rejected(self):
        self.change('dry_stock', lambda r: r[0].update(checked_at='2026-11-08T07:00:00+09:00'))
        data, lines = self.valid()
        self.assertTrue(any('当日朝' in e for e in validate(data, lines, DAY, NOW)))

    def test_future_and_afternoon_stock_rejected(self):
        self.change('dry_stock', lambda r: r[0].update(checked_at='2026-11-09T10:00:00+09:00'))
        with self.assertRaisesRegex(DataError, '未来'):
            self.data()
        self.change('dry_stock', lambda r: r[0].update(checked_at='2026-11-09T12:00:00+09:00'))
        data = load(self.folder, datetime(2026, 11, 9, 13, tzinfo=JST))
        self.assertTrue(any('当日朝' in e for e in validate(data, calculate(data, DAY), DAY, datetime(2026, 11, 9, 13, tzinfo=JST))))

    def test_book_stock_never_substitutes(self):
        self.change('dry_stock', lambda r: r[0].update(source='book'))
        with self.assertRaisesRegex(DataError, 'source'):
            self.data()

    def test_forecast_gap_stops_calculation(self):
        self.change('forecast', lambda r: r.pop(1))
        with self.assertRaisesRegex(DataError, '予測日=2026-11-09.*欠損'):
            calculate(self.data(), DAY)

    def test_transit_offsets_need_and_boundary(self):
        self.change('transit', lambda r: r.append(dict(shipment_id='T001', warehouse='KOBE', product='P001', quantity_p='240', arrival_date='2026-11-10', confirmed='yes')))
        lines = calculate(self.data(), DAY)
        self.assertEqual(lines[0]['suggested_p'], 0)
        self.assertEqual(lines[1]['suggested_p'], 240)
        self.change('transit', lambda r: r[0].update(arrival_date='2026-11-08'))
        with self.assertRaisesRegex(DataError, '二重計上'):
            calculate(self.data(), DAY)

    def test_missing_transit_coverage_cannot_mean_zero(self):
        self.change('policies', lambda r: r[0].update(transit_checked_at=''))
        with self.assertRaises(DataError):
            self.data()

    def test_transit_snapshot_mismatch(self):
        self.change('policies', lambda r: r[0].update(transit_checked_at='2026-11-08T09:00:00+09:00'))
        with self.assertRaisesRegex(DataError, '不一致'):
            calculate(self.data(), DAY)

    def test_order_double_counting_contract(self):
        self.change('forecast', lambda r: r[0].update(order_basis='excluded'))
        with self.assertRaisesRegex(DataError, 'order_basis'):
            self.data()

    def test_duplicate_unknown_and_bad_numeric_rejected(self):
        for table, change in [('inventory', lambda r: r.append(r[0].copy())), ('inventory', lambda r: r[0].update(warehouse='UNKNOWN')), ('inventory', lambda r: r[0].update(quantity_p='-1'))]:
            with self.subTest(table=table, change=change):
                original = (self.folder / f'{table}.csv').read_bytes()
                self.change(table, change)
                with self.assertRaises(DataError):
                    self.data()
                (self.folder / f'{table}.csv').write_bytes(original)

    def test_revision_and_revalidation_after_edit(self):
        data, lines = self.valid()
        first = self.service.issue(data, lines, DAY, '担当者', True, True)
        second = self.service.issue(data, lines, DAY, '担当者', True, True)
        self.assertEqual(second['revision'], first['revision'] + 1)
        lines[0]['quantity_p'] = 240
        with self.assertRaises(DataError):
            self.service.issue(data, lines, DAY, '担当者', True, True)
        self.assertEqual(len(self.service.history()), 2)

    def test_changed_master_reparsed_at_issue(self):
        data, lines = self.valid()
        data.tables['dry_stock'][0]['quantity_p'] = 9999
        lines[0]['quantity_p'] = 240
        with self.assertRaises(DataError):
            self.service.issue(data, lines, DAY, '担当者', True, True)

    def test_changed_raw_requires_reimport(self):
        data, lines = self.valid()
        data.raw['dry_stock.csv'] = data.raw['dry_stock.csv'].replace('240', '9999')
        with self.assertRaisesRegex(DataError, '再取込'):
            self.service.issue(data, lines, DAY, '担当者', True, True)

    def test_unacknowledged_and_future_issue_blocked(self):
        data, lines = self.valid()
        with self.assertRaises(DataError):
            self.service.issue(data, lines, DAY, '担当者', True)
        tomorrow = date(2026, 11, 10)
        plan = calculate(data, tomorrow)
        draft = self.service.issue(data, plan, tomorrow, '', False)
        self.assertFalse(draft['formal'])
        with self.assertRaises(DataError):
            self.service.issue(data, plan, tomorrow, '担当者', True, True)

    def test_missing_or_duplicate_instruction_blocked(self):
        data, lines = self.valid()
        with self.assertRaises(DataError):
            self.service.issue(data, lines[:1], DAY, '担当者', True, True)
        with self.assertRaises(DataError):
            self.service.issue(data, lines + lines[:1], DAY, '担当者', True, True)

    def test_report_escapes_imported_text(self):
        self.change('products', lambda r: r[0].update(name='<script>alert(1)</script>'))
        data, lines = self.valid()
        issued = self.service.issue(data, lines, DAY, '<b>担当者</b>', True, True)
        text = report_html(issued, 'KOBE')
        self.assertNotIn('<script>', text)
        self.assertIn('&lt;script&gt;', text)

    def test_database_rollback_on_issue_failure(self):
        data, lines = self.valid()
        self.service.db.execute("CREATE TRIGGER fail_issue BEFORE INSERT ON issues BEGIN SELECT RAISE(ABORT,'test'); END")
        import sqlite3
        with self.assertRaises(sqlite3.IntegrityError):
            self.service.issue(data, lines, DAY, '担当者', True, True)
        self.assertEqual(self.service.history(), [])


if __name__ == '__main__':
    unittest.main()
