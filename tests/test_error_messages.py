import tempfile,tkinter as tk,unittest
from pathlib import Path
from unittest.mock import patch
from bunsen.core import DataError,ValidationIssue
from bunsen.error_messages import describe_error
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
