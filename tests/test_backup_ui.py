import os
from pathlib import Path
import tempfile
import tkinter as tk
import unittest
from unittest.mock import patch

from bunsen.backup import file_digest
from bunsen.core import DataError, calculate
from bunsen.ui import App
from test_workflow import DAY, NOW, ROOT


class BackupUITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root_path = Path(self.temp.name)
        self.environment = patch.dict(os.environ, {'LOCALAPPDATA': self.temp.name})
        self.environment.start()
        self.root = tk.Tk()
        self.root.withdraw()
        self.app = App(self.root)
        self.app.service.clock = lambda: NOW
        data = self.app.service.import_folder(ROOT / 'samples/before_morning')
        self.draft = self.app.service.issue(data, calculate(data, DAY), DAY, '', False)
        self.app.history()
        self.archive = self.root_path / 'backup.zip'

    def tearDown(self):
        self.app.close()
        self.environment.stop()
        self.temp.cleanup()

    def save_and_select(self):
        with patch('bunsen.ui.filedialog.asksaveasfilename', return_value=str(self.archive)):
            self.app.save_backup()
        with patch('bunsen.ui.filedialog.askopenfilename', return_value=str(self.archive)):
            self.app.select_backup()

    def test_create_inspect_restore_view_reprint_and_return(self):
        home = self.app.home
        self.save_and_select()
        self.assertIn('検証済み', self.app.backup_result.get('1.0', 'end'))
        with patch('bunsen.ui.filedialog.askdirectory', return_value=str(self.root_path / 'recovery')):
            self.app.restore_selected()
        expected_hash = file_digest(home / 'history.db')
        self.app.open_restored()
        self.assertTrue(self.app.service.read_only)
        self.assertIsNone(self.app.data)
        self.assertFalse(self.app.ack.get())
        self.assertIn('閲覧専用', self.app.info.get())
        self.assertTrue(all(str(button.cget('state')) == 'disabled' for button in self.app.write_buttons))
        self.app.history_list.selection_set(0)
        with patch('bunsen.ui.webbrowser.open') as browser:
            self.app.reprint()
            self.assertEqual(browser.call_count, 2)
        text = (self.app.home / f'view-output/{self.draft["number"]}-KOBE.html').read_text(encoding='utf-8')
        self.assertIn('実出荷に使用しない', text)
        self.assertEqual(file_digest(home / 'history.db'), expected_hash)
        self.app.return_normal()
        self.assertEqual(self.app.home, home)
        self.assertFalse(self.app.service.read_only)
        self.assertTrue(all(str(button.cget('state')) == 'normal' for button in self.app.write_buttons))
        self.assertEqual(self.app.service.get_issue(self.draft['number']), self.draft)

    def test_invalid_archive_clears_previous_selection_and_leaves_data_usable(self):
        self.save_and_select()
        bad = self.root_path / 'bad.zip'
        bad.write_bytes(b'not zip')
        before = self.app.service.history()
        with patch('bunsen.ui.filedialog.askopenfilename', return_value=str(bad)), patch('bunsen.ui.messagebox.showerror'):
            self.app.run(self.app.select_backup)
        self.assertIsNone(self.app.backup_path)
        self.assertIsNone(self.app.backup_manifest)
        self.assertEqual(str(self.app.restore_button.cget('state')), 'disabled')
        self.assertIn('検証失敗', self.app.backup_result.get('1.0', 'end'))
        self.assertEqual(self.app.service.history(), before)
        self.assertFalse(self.app.service.read_only)

    def test_changed_archive_does_not_switch_or_overwrite_active_data(self):
        self.save_and_select()
        home = self.app.home
        digest = file_digest(home / 'history.db')
        self.archive.write_bytes(b'changed after preview')
        with patch('bunsen.ui.filedialog.askdirectory', return_value=str(self.root_path / 'recovery')), patch('bunsen.ui.messagebox.showerror'):
            self.app.run(self.app.restore_selected)
        self.assertEqual(self.app.home, home)
        self.assertFalse(self.app.service.read_only)
        self.assertEqual(file_digest(home / 'history.db'), digest)
        self.assertEqual(str(self.app.restore_button.cget('state')), 'disabled')
        self.assertIn('復元失敗', self.app.backup_result.get('1.0', 'end'))

    def test_missing_marker_does_not_promote_restored_data_to_writable(self):
        self.save_and_select()
        with patch('bunsen.ui.filedialog.askdirectory', return_value=str(self.root_path / 'recovery')):
            self.app.restore_selected()
        (self.app.restored_home / 'restore-info.json').unlink()
        home = self.app.home
        with self.assertRaisesRegex(DataError, 'マーカー'):
            self.app.open_restored()
        self.assertEqual(self.app.home, home)
        self.assertFalse(self.app.service.read_only)

    def test_corrupt_restored_database_does_not_close_current_service(self):
        self.save_and_select()
        with patch('bunsen.ui.filedialog.askdirectory', return_value=str(self.root_path / 'recovery')):
            self.app.restore_selected()
        (self.app.restored_home / 'history.db').write_bytes(b'broken database')
        home = self.app.home
        with self.assertRaises(DataError):
            self.app.open_restored()
        self.assertEqual(self.app.home, home)
        self.assertEqual(self.app.service.get_issue(self.draft['number']), self.draft)

    def test_fresh_pc_read_only_view_does_not_create_empty_normal_database(self):
        self.save_and_select()
        with patch('bunsen.ui.filedialog.askdirectory', return_value=str(self.root_path / 'recovery')):
            self.app.restore_selected()
        restored = self.app.restored_home
        self.app.close()
        new_pc = self.root_path / 'new-pc'
        with patch.dict(os.environ, {'LOCALAPPDATA': str(new_pc)}):
            self.root = tk.Tk()
            self.root.withdraw()
            self.app = App(self.root, restored)
        self.assertTrue(self.app.service.read_only)
        self.assertEqual(str(self.app.normal_button.cget('state')), 'disabled')
        self.assertFalse(new_pc.exists())
        with self.assertRaisesRegex(DataError, '出荷履歴の照合'):
            self.app.return_normal()
