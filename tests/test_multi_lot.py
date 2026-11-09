import unittest

import test_workflow as fixtures
from test_workflow import DAY, NOW
from bunsen.core import DataError, allocation_label, validate
from bunsen.service import report_html


class MultiLotTests(unittest.TestCase):
    tearDown = fixtures.WorkflowTests.tearDown
    change = fixtures.WorkflowTests.change
    data = fixtures.WorkflowTests.data

    def setUp(self):
        fixtures.WorkflowTests.setUp(self)
        def split_stock(rows):
            rows[0]['quantity_p'] = '144'
            rows.append(dict(rows[0], lot='L002', quantity_p='96', best_before='2027-07-31'))
        self.change('dry_stock', split_stock)

    def valid(self):
        data = self.data()
        lines = self.service.calculate(data, DAY)
        for row in lines:
            row.update(quantity_p=120, lot='', allocations=[{'lot': 'L001', 'quantity_p': 72}, {'lot': 'L002', 'quantity_p': 48}])
        return data, lines

    def test_split_issue_report_and_immutable_history(self):
        data, lines = self.valid()
        self.assertEqual(validate(data, lines, DAY, NOW), [])
        issued = self.service.issue(data, lines, DAY, '架空担当者', True, True)
        text = report_html(issued, 'KOBE')
        for value in ('L001', 'L002', '2027-06-30', '2027-07-31', '指示合計 120 P', 'ロット割当合計 120 P', '<td>72</td>', '<td>48</td>'):
            self.assertIn(value, text)
        self.assertNotIn('未割当／合計不一致', text)
        self.assertEqual(issued['lines'][0]['allocations'], lines[0]['allocations'])
        lines[0]['allocations'][0]['quantity_p'] = 999
        self.assertEqual(self.service.get_issue(issued['number']), issued)

    def test_total_mismatch_blocks_formal_and_remains_visible_in_draft(self):
        data, lines = self.valid()
        lines[0]['allocations'][1]['quantity_p'] = 24
        with self.assertRaisesRegex(DataError, '合計不一致'):
            self.service.issue(data, lines, DAY, '架空担当者', True, True)
        draft = self.service.issue(data, lines, DAY, '', False)
        text = report_html(draft, 'KOBE')
        self.assertIn('未割当 24 P', text)
        self.assertIn('正式指示は発行不可', text)
        self.assertIn('合計不一致', allocation_label(lines[0]))

    def test_overallocation_and_zero_total_with_allocations_blocked(self):
        data, lines = self.valid()
        for quantity in (96, 0):
            with self.subTest(quantity=quantity):
                lines[0]['quantity_p'] = quantity
                with self.assertRaisesRegex(DataError, '合計不一致'):
                    self.service.issue(data, lines, DAY, '架空担当者', True, True)

    def test_shared_lot_excess_even_when_product_total_fits(self):
        data, lines = self.valid()
        lines[0]['allocations'] = [{'lot': 'L001', 'quantity_p': 120}]
        with self.assertRaisesRegex(DataError, 'ロット=L001.*不足 48 P'):
            self.service.issue(data, lines, DAY, '架空担当者', True, True)

    def test_each_lot_must_be_case_multiple(self):
        data, lines = self.valid()
        lines[0]['allocations'] = [{'lot': 'L001', 'quantity_p': 12}, {'lot': 'L002', 'quantity_p': 108}]
        with self.assertRaisesRegex(DataError, 'ロット=L001.*端数積付'):
            self.service.issue(data, lines, DAY, '架空担当者', True, True)

    def test_one_expired_lot_cannot_be_hidden_by_other_lot(self):
        self.change('dry_stock', lambda r: r[1].update(best_before='2026-11-30'))
        data, lines = self.valid()
        with self.assertRaisesRegex(DataError, 'ロット=L002.*期限違反'):
            self.service.issue(data, lines, DAY, '架空担当者', True, True)

    def test_vehicle_capacity_is_sum_of_all_lots(self):
        self.change('vehicles', lambda r: r[0].update(capacity_g='10000'))
        data, lines = self.valid()
        with self.assertRaisesRegex(DataError, '積載超過 12000>10000'):
            self.service.issue(data, lines, DAY, '架空担当者', True, True)

    def test_duplicate_lot_in_same_instruction_rejected(self):
        data, lines = self.valid()
        lines[0]['allocations'] = [{'lot': 'L001', 'quantity_p': 72}, {'lot': 'L001', 'quantity_p': 48}]
        with self.assertRaisesRegex(DataError, 'ロット重複'):
            self.service.issue(data, lines, DAY, '架空担当者', True, True)

    def test_unknown_or_missing_lot_is_not_substituted(self):
        data, lines = self.valid()
        for lot in ('', 'UNKNOWN'):
            with self.subTest(lot=lot):
                lines[0]['allocations'][1]['lot'] = lot
                with self.assertRaisesRegex(DataError, '実ロット未確認'):
                    self.service.issue(data, lines, DAY, '架空担当者', True, True)

    def test_empty_allocations_do_not_mean_zero_instruction(self):
        data, lines = self.valid()
        lines[0]['allocations'] = []
        with self.assertRaisesRegex(DataError, '合計不一致'):
            self.service.issue(data, lines, DAY, '架空担当者', True, True)
        draft = self.service.issue(data, lines, DAY, '', False)
        self.assertIn('未割当／合計不一致', report_html(draft, 'KOBE'))
        lines[0]['quantity_p'] = 0
        self.assertEqual(validate(data, lines, DAY, NOW), [])

    def test_malformed_allocation_is_rejected_even_for_draft(self):
        data, lines = self.valid()
        invalid = [None, 'L001', [{}], [{'lot': 'L001', 'quantity_p': None}],
                   [{'lot': 'L001', 'quantity_p': 0}], [{'lot': 'L001', 'quantity_p': -1}],
                   [{'lot': 'L001', 'quantity_p': True}], [{'lot': 'L001', 'quantity_p': '72'}],
                   [{'lot': 1, 'quantity_p': 72}], [{'lot': 'L001', 'quantity_p': 72, 'extra': 'ignored?'}]]
        for assigned in invalid:
            with self.subTest(assigned=assigned):
                lines[0]['allocations'] = assigned
                with self.assertRaises(DataError):
                    self.service.issue(data, lines, DAY, '', False)

    def test_legacy_and_split_formats_share_same_stock_validation(self):
        data, lines = self.valid()
        lines[0]['allocations'] = [{'lot': 'L001', 'quantity_p': 24}, {'lot': 'L002', 'quantity_p': 96}]
        lines[1].pop('allocations')
        lines[1]['lot'] = 'L001'
        self.assertEqual(validate(data, lines, DAY, NOW), [])
        self.service.issue(data, lines, DAY, '架空担当者', True, True)
        lines[0]['lot'] = 'L001'
        with self.assertRaisesRegex(DataError, '併用'):
            self.service.issue(data, lines, DAY, '架空担当者', True, True, expected_revision=1)

    def test_edit_after_issue_is_revalidated(self):
        data, lines = self.valid()
        self.service.issue(data, lines, DAY, '架空担当者', True, True)
        lines[0]['allocations'] = [{'lot': 'L001', 'quantity_p': 120}]
        with self.assertRaisesRegex(DataError, '二重割当'):
            self.service.issue(data, lines, DAY, '架空担当者', True, True, expected_revision=1)
        self.assertEqual(len(self.service.history()), 1)

    def test_previous_day_stock_in_one_lot_blocks_issue(self):
        self.change('dry_stock', lambda r: r[1].update(checked_at='2026-11-08T07:00:00+09:00'))
        data, lines = self.valid()
        with self.assertRaisesRegex(DataError, 'ロット=L002.*当日朝'):
            self.service.issue(data, lines, DAY, '架空担当者', True, True)

    def test_same_lot_code_on_different_products_does_not_share_stock(self):
        for table in ('products', 'policies', 'inventory', 'forecast', 'vehicles', 'dry_stock'):
            self.change(table, lambda rows: rows.extend([dict(row, product='P002') for row in rows]))
        data, lines = self.valid()
        self.assertEqual(len(lines), 4)
        self.assertEqual(validate(data, lines, DAY, NOW), [])
        self.service.issue(data, lines, DAY, '架空担当者', True, True)
