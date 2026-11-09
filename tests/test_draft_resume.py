import json
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

import test_workflow as fixtures
from test_workflow import DAY, NOW, ROOT
from bunsen.core import DataError, calculate
from bunsen.service import Service, report_html


class DraftResumeTests(unittest.TestCase):
    setUp = fixtures.WorkflowTests.setUp
    tearDown = fixtures.WorkflowTests.tearDown
    change = fixtures.WorkflowTests.change
    data = fixtures.WorkflowTests.data

    def draft(self, data=None):
        data = data or self.service.import_folder(ROOT / 'samples/before_morning')
        lines = self.service.calculate(data, DAY)
        return self.service.issue(data, lines, DAY, '前日担当者', False)

    def test_resume_after_database_reopen_preserves_manual_instructions(self):
        data = self.service.import_folder(ROOT / 'samples/before_morning')
        lines = calculate(data, DAY)
        lines[0].update(quantity_p=144, lot='前日指定ロット')
        draft = self.service.issue(data, lines, DAY, '前日担当者', False)
        self.service.close()
        self.service = Service(Path(self.temp.name) / 'history.db', lambda: NOW)
        restored, working, dispatch = self.service.resume_draft(draft['number'])
        self.assertEqual(dispatch, DAY)
        self.assertEqual(restored.digest, data.digest)
        self.assertEqual(working[0]['quantity_p'], 144)
        self.assertEqual(working[0]['lot'], '前日指定ロット')
        self.assertNotIn('validation', working[0])
        with self.assertRaisesRegex(DataError, '当日朝'):
            self.service.issue(restored, working, DAY, '朝担当者', True, True, draft_number=draft['number'])
        self.assertEqual(self.service.get_issue(draft['number']), draft)

    def test_morning_reconciliation_shortage_edit_and_formal_provenance(self):
        draft = self.draft()
        data = self.data()
        lines = self.service.reconcile_draft(draft['number'], data)
        self.assertEqual([r['quantity_p'] for r in lines], [240, 240])
        for line in lines:
            line['lot'] = 'L001'
        with self.assertRaisesRegex(DataError, '二重割当'):
            self.service.issue(data, lines, DAY, '朝担当者', True, True, draft_number=draft['number'])
        for line in lines:
            line['quantity_p'] = 120
        final = self.service.issue(data, lines, DAY, '朝担当者', True, True, draft_number=draft['number'])
        self.assertEqual(final['source_draft']['number'], draft['number'])
        self.assertEqual(final['source_draft']['rows'][0]['draft_quantity_p'], 240)
        self.assertEqual(final['source_draft']['rows'][0]['current_quantity_p'], 120)
        self.assertIn('dry_stock', final['source_draft']['changed_tables'])
        text = report_html(final, 'KOBE')
        self.assertIn('仮指示との照合', text)
        self.assertIn(draft['number'], text)
        self.assertIn('仮指示 240P', text)
        self.assertEqual(self.service.get_issue(draft['number']), draft)

    def test_new_forecast_and_arrival_do_not_override_manual_quantity(self):
        draft = self.draft()
        self.change('forecast', lambda r: [x.update(quantity_p='0') for x in r])
        self.change('warehouses', lambda r: r[0].update(lead_days='2'))
        data = self.data()
        lines = self.service.reconcile_draft(draft['number'], data)
        self.assertEqual(lines[0]['quantity_p'], 240)
        self.assertEqual(lines[0]['suggested_p'], 0)
        self.assertEqual(lines[0]['arrival'], '2026-11-11')
        reference = self.service.draft_comparison(draft['number'], data, lines)
        self.assertEqual(reference['rows'][0]['draft_arrival'], '2026-11-10')
        self.assertIn('forecast', reference['changed_tables'])
        self.assertIn('warehouses', reference['changed_tables'])
        lines[0].update(quantity_p=120, lot='L001')
        again = self.service.reconcile_draft(draft['number'], data, lines)
        self.assertEqual(again[0]['quantity_p'], 120)
        self.assertEqual(again[0]['lot'], 'L001')

    def test_split_lots_preserved_and_missing_current_lot_blocks_issue(self):
        data = self.service.import_folder(ROOT / 'samples/multi_lot')
        lines = calculate(data, DAY)
        for line in lines:
            line.update(quantity_p=120, allocations=[{'lot': 'L001', 'quantity_p': 72}, {'lot': 'L002', 'quantity_p': 48}])
        draft = self.service.issue(data, lines, DAY, '', False)
        _, restored, _ = self.service.resume_draft(draft['number'])
        self.assertEqual(restored[0]['allocations'], lines[0]['allocations'])
        morning = self.data()  # L002 does not exist in this snapshot.
        working = self.service.reconcile_draft(draft['number'], morning, restored)
        self.assertEqual(working[0]['allocations'][1]['lot'], 'L002')
        with self.assertRaisesRegex(DataError, '実ロット未確認 L002'):
            self.service.issue(morning, working, DAY, '朝担当者', True, True, draft_number=draft['number'])

    def test_new_product_is_not_automatically_added_to_draft(self):
        draft = self.draft()
        for table in ('products', 'policies', 'inventory', 'forecast', 'vehicles', 'dry_stock'):
            self.change(table, lambda rows: rows.extend([dict(row, product='P002') for row in rows]))
        data = self.data()
        with self.assertRaisesRegex(DataError, '商品=P002.*自動追加'):
            self.service.reconcile_draft(draft['number'], data)
        lines = calculate(data, DAY)
        with self.assertRaises(DataError):
            self.service.issue(data, lines, DAY, '', False, draft_number=draft['number'])

    def test_removed_product_is_not_silently_dropped(self):
        draft = self.draft()
        for table in ('products', 'policies', 'inventory', 'forecast', 'vehicles', 'dry_stock'):
            self.change(table, lambda rows: [row.update(product='P002') for row in rows])
        with self.assertRaisesRegex(DataError, '商品=P001.*自動削除せず照合停止'):
            self.service.reconcile_draft(draft['number'], self.data())

    def test_formal_and_superseded_drafts_cannot_be_resumed(self):
        first = self.draft()
        self.draft()
        with self.assertRaisesRegex(DataError, '旧版'):
            self.service.resume_draft(first['number'])
        data = self.data()
        lines = calculate(data, DAY)
        for row in lines:
            row.update(quantity_p=120, lot='L001')
        formal = self.service.issue(data, lines, DAY, '朝担当者', True, True)
        with self.assertRaisesRegex(DataError, '正式指示'):
            self.service.resume_draft(formal['number'])
        with self.assertRaisesRegex(DataError, '旧版'):
            self.service.issue(data, lines, DAY, '朝担当者', True, True, expected_revision=1, draft_number=first['number'])

    def test_source_dispatch_must_match(self):
        draft = self.draft()
        data = self.data()
        tomorrow = date(2026, 11, 10)
        with self.assertRaisesRegex(DataError, '出荷日が一致しません'):
            self.service.issue(data, calculate(data, tomorrow), tomorrow, '', False, draft_number=draft['number'])

    def test_changed_source_raw_is_rejected_on_resume(self):
        draft = self.draft()
        draft['raw']['products.csv'] = draft['raw']['products.csv'].replace('100,', '101,')
        self.service.db.execute('UPDATE issues SET payload=? WHERE number=?', (json.dumps(draft), draft['number']))
        self.service.db.commit()
        with self.assertRaisesRegex(DataError, '再取込'):
            self.service.resume_draft(draft['number'])

    def test_source_revision_checked_again_inside_issue_transaction(self):
        draft = self.draft()
        data = self.data()
        lines = self.service.reconcile_draft(draft['number'], data)
        for row in lines:
            row.update(quantity_p=120, lot='L001')
        second = Service(Path(self.temp.name) / 'history.db', lambda: NOW)
        original = self.service.draft
        def publish_after_read(number, dispatch=None):
            result = original(number, dispatch)
            second.issue(data, lines, DAY, '', False)
            return result
        try:
            with patch.object(self.service, 'draft', side_effect=publish_after_read):
                with self.assertRaisesRegex(DataError, '再開元の仮指示が更新'):
                    self.service.issue(data, lines, DAY, '朝担当者', True, True, draft_number=draft['number'])
            self.assertEqual(len(self.service.history()), 2)
            self.assertFalse(any(row[2] for row in self.service.history()))
        finally:
            second.close()

    def test_missing_new_forecast_stops_reconciliation(self):
        draft = self.draft()
        self.change('forecast', lambda r: r.pop(0))
        with self.assertRaisesRegex(DataError, '予測日=.*欠損'):
            self.service.reconcile_draft(draft['number'], self.data())
