import json
import unittest
from pathlib import Path

from bunsen.core import DataError, calculate
from bunsen.service import Service, report_html
import test_workflow as fixtures
from test_workflow import DAY, NOW


class SafetyTests(unittest.TestCase):
    setUp = fixtures.WorkflowTests.setUp
    tearDown = fixtures.WorkflowTests.tearDown
    change = fixtures.WorkflowTests.change
    data = fixtures.WorkflowTests.data
    valid = fixtures.WorkflowTests.valid

    def test_late_transit_cannot_hide_earlier_stockout(self):
        self.change('transit', lambda r: r.append(dict(shipment_id='T001', warehouse='KOBE', product='P001', quantity_p='240', arrival_date='2026-11-10', confirmed='yes')))
        line = calculate(self.data(), DAY)[0]
        self.assertEqual(line['suggested_p'], 0)
        self.assertIn('到着前の不足リスク', line['warning'])
        self.assertIn('2026-11-08', line['warning'])
        self.assertIn('2026-11-09', line['warning'])
        self.assertEqual([r['projected_closing_p'] for r in line['timeline']], [-60, -120, 60])
        self.assertEqual([r['transit_p'] for r in line['timeline']], [0, 0, 240])
        self.assertIn('入庫時刻', line['warning'])

    def test_no_false_warning_when_each_day_is_covered(self):
        self.change('inventory', lambda r: r[0].update(quantity_p='120'))
        self.change('transit', lambda r: r.append(dict(shipment_id='T001', warehouse='KOBE', product='P001', quantity_p='120', arrival_date='2026-11-09', confirmed='yes')))
        line = calculate(self.data(), DAY)[0]
        self.assertEqual(line['warning'], '')
        self.assertEqual([r['projected_closing_p'] for r in line['timeline']], [60, 120, 60])

    def test_day_of_receipt_order_is_not_claimed_confirmed(self):
        self.change('inventory', lambda r: r[0].update(quantity_p='60'))
        self.change('transit', lambda r: r.append(dict(shipment_id='T001', warehouse='KOBE', product='P001', quantity_p='180', arrival_date='2026-11-09', confirmed='yes')))
        line = calculate(self.data(), DAY)[0]
        self.assertNotIn('到着前の不足リスク', line['warning'])
        self.assertIn('入庫時刻と需要発生順は未確認: 2026-11-09', line['warning'])

    def test_unknown_csv_including_orders_is_rejected(self):
        for filename in ('orders.csv', 'manufacturing.CSV', 'inventory_copy.csv'):
            with self.subTest(filename=filename):
                extra = self.folder / filename
                extra.write_text('quantity_p\n100\n', encoding='utf-8')
                with self.assertRaisesRegex(DataError, filename + ': 未対応CSV'):
                    self.data()
                extra.unlink()
        (self.folder / 'notes.txt').write_text('memo', encoding='utf-8')
        self.data()

    def test_missing_folder_is_clear_error(self):
        with self.assertRaisesRegex(DataError, 'CSVフォルダーが存在しません'):
            self.service.import_folder(self.folder / 'missing')

    def test_stale_second_window_cannot_replace_formal_issue(self):
        second = Service(Path(self.temp.name) / 'history.db', lambda: NOW)
        try:
            data, lines = self.valid()
            revision_a = self.service.latest_revision(DAY)
            revision_b = second.latest_revision(DAY)
            self.assertEqual(revision_a, 0)
            first = self.service.issue(data, lines, DAY, '担当者A', True, True, expected_revision=revision_a)
            with self.assertRaisesRegex(DataError, '改訂が更新されています'):
                second.issue(data, lines, DAY, '担当者B', True, True, expected_revision=revision_b)
            self.assertEqual(len(second.history()), 1)
            event = json.loads(second.db.execute("SELECT payload FROM events WHERE kind='issue_blocked' ORDER BY id DESC").fetchone()[0])
            self.assertIn('再計算・再確認', event['errors'][0])
            current = second.latest_revision(DAY)
            second.calculate(data, DAY)
            updated = second.issue(data, lines, DAY, '担当者B', True, True, expected_revision=current)
            self.assertEqual(updated['revision'], 2)
            self.assertEqual(updated['replaces_number'], first['number'])
            self.assertEqual(self.service.get_issue(first['number']), first)
        finally:
            second.close()

    def test_superseded_report_marked_without_mutating_history(self):
        data, lines = self.valid()
        first = self.service.issue(data, lines, DAY, '担当者', True, True)
        latest_view = self.service.report_payload(first['number'])
        self.assertFalse(latest_view['report_status']['superseded'])
        second = self.service.issue(data, lines, DAY, '担当者', True, True, expected_revision=1)
        old_view = self.service.report_payload(first['number'])
        self.assertTrue(old_view['report_status']['superseded'])
        self.assertEqual(old_view['report_status']['latest_number'], second['number'])
        old_html = report_html(old_view, 'KOBE')
        self.assertIn('旧版・差替え済み — 出荷に使用しない', old_html)
        self.assertIn(second['number'], old_html)
        self.assertIn('予測残高', old_html)
        self.assertIn('確定在庫ではありません', old_html)
        self.assertEqual(self.service.get_issue(first['number']), first)
        self.assertIn('履歴照合時点の最新改訂', report_html(self.service.report_payload(second['number']), 'KOBE'))
        self.assertIn('現行改訂は未照合', report_html(first, 'KOBE'))

    def test_legacy_payload_without_timeline_still_prints(self):
        data, lines = self.valid()
        old = self.service.issue(data, lines, DAY, '担当者', True, True)
        old.pop('replaces_number')
        for line in old['lines']:
            line.pop('timeline')
        self.service.db.execute('UPDATE issues SET payload=? WHERE number=?', (json.dumps(old), old['number']))
        self.service.db.commit()
        report = self.service.report_payload(old['number'])
        self.assertIn('正式出荷指示書', report_html(report, 'KOBE'))
        self.assertFalse(report['report_status']['superseded'])

    def test_draft_revision_does_not_block_initial_formal(self):
        data, lines = self.valid()
        self.service.issue(data, lines, DAY, '', False)
        self.service.issue(data, lines, DAY, '', False)
        self.assertEqual(self.service.latest_revision(DAY), 0)
        formal = self.service.issue(data, lines, DAY, '担当者', True, True)
        self.assertEqual(formal['revision'], 1)

    def test_revision_guard_cannot_be_bypassed_with_bool_or_none(self):
        data, lines = self.valid()
        for revision in (False, None, -1):
            with self.subTest(revision=revision):
                with self.assertRaises(DataError):
                    self.service.issue(data, lines, DAY, '担当者', True, True, expected_revision=revision)
        self.assertEqual(self.service.history(), [])
