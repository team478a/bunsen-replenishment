import tempfile,tkinter as tk,unittest
from pathlib import Path
from unittest.mock import patch
from bunsen.core import DataError,ValidationIssue
from bunsen.error_messages import describe_error,error_details
from bunsen.input_guard import InputGuard
from bunsen.ui import App

class ErrorMessageTests(unittest.TestCase):
    def test_input_error_identifies_file_reason_and_next_action(self):
        issue=ValidationIssue('inventory.csv','input_file','INPUT_RECHECK_FAILED','ファイルを読み込めませんでした')
        title,message=describe_error(DataError([issue]))
        self.assertEqual(title,'入力ファイルを読み込めませんでした')
        for part in ('inventory.csv','理由','対処方法','保存して閉じて','データを開いて計算','コピー'):self.assertIn(part,message)
        self.assertNotIn('商品=-',message);self.assertNotIn('input_file',message)
    def test_other_errors_are_preserved_including_mixed_errors(self):
        error=DataError(['在庫数量が未確認です'])
        self.assertEqual(describe_error(error)[1],str(error))
        mixed=DataError([ValidationIssue('inventory.csv','input_file','INPUT_CHANGED','内容が変わりました'),'在庫数量が未確認です'])
        self.assertIn('あわせて確認',describe_error(mixed)[1]);self.assertIn('在庫数量が未確認です',describe_error(mixed)[1])
    def test_ui_uses_friendly_message_and_clears_confirmation(self):
        with tempfile.TemporaryDirectory() as folder:
            root=tk.Tk();root.withdraw();app=App(root,Path(folder)/'home')
            try:
                app.ack.set(True)
                def fail():raise DataError([ValidationIssue('report.pdf','input_file','INPUT_CHANGED','内容が変わりました')])
                with patch('bunsen.legacy_ui.messagebox.showerror') as show:app.run(fail)
                self.assertFalse(app.ack.get());self.assertEqual(show.call_args.args[0],'入力ファイルを読み込めませんでした')
                self.assertIn('report.pdf',show.call_args.args[1]);self.assertIn('対処方法',app.detail.get('1.0','end'))
            finally:app.close()
    def test_file_guard_does_not_label_pdf_as_csv(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder);guard=InputGuard(path);guard.initial['source.pdf']=(0,);guard._inventory=lambda:{'source.pdf':(0,)}
            issues,_=guard.finish();self.assertEqual(issues[0].code,'INPUT_RECHECK_FAILED');self.assertNotIn('CSV',issues[0].message)

    def test_forecast_shortage_groups_products_by_exact_warehouse_and_gap(self):
        issues=[]
        for warehouse,end in [('KOBE','2026-12-04'),('SUGITO','2026-12-05')]:
            for p in range(27):
                issues.append(ValidationIssue('forecast.csv','quantity_p','FORECAST_MISSING',
                    f'2026-12-03から{end}の予測欠損',product=f'FAKE{p:03}',warehouse=warehouse,date='2026-12-03'))
        error=DataError(issues);original=str(error)
        title,message=describe_error(error)
        self.assertIn('需要予測が足りない',title)
        for value in ['ムロオ神戸','ムロオ杉戸','2026-12-03〜2026-12-04','2026-12-03〜2026-12-05','27商品','CSV','安全在庫']:
            self.assertIn(value,message)
        self.assertLess(len(message.splitlines()),16)
        for value in ['quantity_p','商品=-','FORECAST_MISSING','FAKE026']:self.assertNotIn(value,message)
        details=error_details(error)
        self.assertIn('FAKE026',details);self.assertIn(original,details)
        self.assertEqual(str(error),original)

    def test_distinct_gaps_and_mixed_failures_are_not_hidden(self):
        issues=[ValidationIssue('forecast.csv','quantity_p','FORECAST_MISSING',
            f'{start}から{end}の予測欠損',product='FAKE001',warehouse='KOBE',date=start)
            for start,end in [('2026-11-20','2026-11-21'),('2026-12-03','2026-12-14')]]
        error=DataError(issues+[ValidationIssue('inventory.csv','as_of','INVENTORY_AFTER_DISPATCH',
            '在庫基準が出荷日・到着日より未来です',product='FAKE002',warehouse='SUGITO')])
        message=describe_error(error)[1]
        self.assertIn('2026-11-20〜2026-11-21',message);self.assertIn('2026-12-03〜2026-12-14',message)
        self.assertIn('在庫の日付と出荷日',message);self.assertIn('FAKE002',message)

    def test_large_summary_is_bounded_but_full_detail_is_available(self):
        issues=[ValidationIssue('forecast.csv','quantity_p','FORECAST_MISSING',
            f'2026-12-03から2026-12-{n:02}の予測欠損',product=f'FAKE{n}',warehouse='KOBE') for n in range(4,25)]
        error=DataError(issues)
        self.assertIn('ほか15件',describe_error(error)[1])
        self.assertNotIn('FAKE24',describe_error(error)[1]);self.assertIn('FAKE24',error_details(error))

    def test_os_errors_use_japanese_actions_instead_of_system_text(self):
        for error,expected in [(PermissionError('WinError 5 Access denied'),'権限'),
                               (FileNotFoundError('No such file'),'選び直して'),
                               (OSError('disk full'),'空き容量'),(ValueError('Invalid isoformat string'),'日付や数量')]:
            title,message=describe_error(error)
            self.assertIn(expected,message);self.assertNotIn(str(error),message)
            self.assertIn(str(error),error_details(error))

    def test_forecast_error_opens_full_details_and_cancels_confirmation(self):
        with tempfile.TemporaryDirectory() as folder:
            root=tk.Tk();root.withdraw();app=App(root,Path(folder)/'home')
            try:
                app.ack.set(True)
                def fail():raise DataError([ValidationIssue('forecast.csv','quantity_p','FORECAST_MISSING',
                    '2026-12-03から2026-12-15の予測欠損',warehouse='SUGITO',product='FAKE001')])
                with patch('bunsen.legacy_ui.messagebox.showerror') as show:app.run(fail)
                self.assertFalse(app.ack.get());self.assertTrue(app.details_frame.winfo_manager())
                self.assertIn('ムロオ杉戸',show.call_args.args[1])
                self.assertIn('FAKE001',app.detail.get('1.0','end'))
            finally:app.close()
