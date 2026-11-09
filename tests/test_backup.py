from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import warnings
import zipfile

from bunsen.backup import create_backup, inspect_backup, restore_backup, file_digest, safe_member
from bunsen.core import DataError, calculate
from bunsen.service import Service, report_html, write_reports
from test_workflow import DAY, NOW, ROOT


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.home = self.root / 'active'
        self.service = Service(self.home / 'history.db', lambda: NOW)
        before = self.service.import_folder(ROOT / 'samples/before_morning')
        self.draft = self.service.issue(before, calculate(before, DAY), DAY, '前日担当者', False)
        self.data = self.service.import_folder(ROOT / 'samples/morning')
        self.lines = self.service.reconcile_draft(self.draft['number'], self.data)
        for row in self.lines:
            row.update(quantity_p=120, lot='L001')
        self.final = self.service.issue(self.data, self.lines, DAY, '朝担当者', True, True, draft_number=self.draft['number'])
        for payload in (self.draft, self.final):
            write_reports(payload, self.home / 'reports')
        (self.home / 'reports/日本語メモ.txt').write_text('架空の保管メモ', encoding='utf-8')
        self.archive = self.root / 'backup.zip'

    def tearDown(self):
        self.service.close()
        self.temp.cleanup()

    def create(self):
        return create_backup(self.service.db, self.home, self.archive, lambda: NOW)

    def rewrite(self, transform):
        with zipfile.ZipFile(self.archive) as archive:
            files = {name: archive.read(name) for name in archive.namelist()}
        transform(files)
        with zipfile.ZipFile(self.archive, 'w', zipfile.ZIP_DEFLATED) as archive:
            for name, content in files.items():
                archive.writestr(name, content)

    def alter_database(self, change):
        def modify(files):
            path = self.root / 'edited.db'
            path.write_bytes(files['history.db'])
            with closing(sqlite3.connect(path)) as db:
                change(db)
                db.commit()
            content = path.read_bytes()
            files['history.db'] = content
            manifest = json.loads(files['manifest.json'])
            manifest['files']['history.db'] = dict(size=len(content), sha256=hashlib.sha256(content).hexdigest())
            files['manifest.json'] = json.dumps(manifest).encode()
        self.rewrite(modify)

    def test_roundtrip_preserves_history_originals_reports_and_current_data(self):
        original_hash = file_digest(self.home / 'history.db')
        manifest = self.create()
        self.assertEqual(manifest, inspect_backup(self.archive))
        self.assertEqual(manifest['counts']['issues'], 2)
        self.assertEqual(manifest['counts']['last_issue'], self.final['number'])
        restored = restore_backup(self.archive, self.root / 'recovery', manifest['archive_sha256'], lambda: NOW)
        self.assertNotEqual(restored, self.home)
        self.assertEqual(file_digest(self.home / 'history.db'), original_hash)
        marker = json.loads((restored / 'restore-info.json').read_text(encoding='utf-8'))
        self.assertIs(marker['read_only'], True)
        self.assertEqual((restored / 'reports/日本語メモ.txt').read_bytes(), (self.home / 'reports/日本語メモ.txt').read_bytes())
        view = Service(restored / 'history.db', lambda: NOW)
        try:
            self.assertTrue(view.read_only)
            self.assertEqual(view.get_issue(self.draft['number']), self.draft)
            self.assertEqual(view.get_issue(self.final['number']), self.final)
            self.assertEqual(view.history(), self.service.history())
            self.assertIn('復元履歴の参考帳票', report_html(view.report_payload(self.final['number']), 'KOBE'))
            self.assertEqual(view.get_issue(self.final['number'])['raw'], self.data.raw)
        finally:
            view.close()

    def test_restored_service_rejects_all_writes_and_sqlite_is_read_only(self):
        self.create()
        restored = restore_backup(self.archive, self.root / 'recovery')
        view = Service(restored / 'history.db', lambda: NOW)
        try:
            actions = [lambda: view.event('edit', {}), lambda: view.import_folder(ROOT / 'samples/morning'),
                       lambda: view.calculate(self.data, DAY), lambda: view.resume_draft(self.draft['number']),
                       lambda: view.reconcile_draft(self.draft['number'], self.data),
                       lambda: view.issue(self.data, self.lines, DAY, '担当者', True, True),
                       lambda: view.issue(self.data, self.lines, DAY, '', False)]
            for action in actions:
                with self.assertRaisesRegex(DataError, '閲覧専用'):
                    action()
            with self.assertRaises(sqlite3.OperationalError):
                view.db.execute('DELETE FROM issues')
            self.assertEqual(len(view.history()), 2)
        finally:
            view.close()

    def test_existing_output_is_not_overwritten(self):
        self.archive.write_bytes(b'keep existing')
        with self.assertRaisesRegex(DataError, '上書きしません'):
            self.create()
        self.assertEqual(self.archive.read_bytes(), b'keep existing')

    def test_output_inside_active_data_is_rejected(self):
        with self.assertRaisesRegex(DataError, 'データフォルダー外'):
            create_backup(self.service.db, self.home, self.home / 'reports/backup.zip')
        self.assertFalse((self.home / 'reports/backup.zip').exists())

    def test_archive_change_after_preview_is_rejected(self):
        manifest = self.create()
        self.rewrite(lambda files: files.update({'reports/日本語メモ.txt': '変更'.encode()}))
        with self.assertRaisesRegex(DataError, '再検証'):
            restore_backup(self.archive, self.root / 'recovery', manifest['archive_sha256'])
        self.assertFalse((self.root / 'recovery').exists())

    def test_checksum_mismatch_is_rejected_before_restore(self):
        self.create()
        self.rewrite(lambda files: files.update({'reports/日本語メモ.txt': b'changed'}))
        with self.assertRaises(DataError):
            restore_backup(self.archive, self.root / 'recovery')
        self.assertFalse((self.root / 'recovery').exists())

    def test_bad_paths_are_rejected_without_extraction_outside_staging(self):
        self.create()
        original = self.archive.read_bytes()
        for name in ('../escape.txt', 'reports/../../escape.txt', 'reports/C:/escape', 'reports\\escape', 'reports/CON.txt', 'reports/a.', '/history.db', 'history.db:stream'):
            with self.subTest(name=name):
                with self.assertRaises(DataError):
                    safe_member(name)
                self.archive.write_bytes(original)
                with zipfile.ZipFile(self.archive, 'a') as archive:
                    archive.writestr(name, b'x')
                with self.assertRaises(DataError):
                    restore_backup(self.archive, self.root / 'recovery')
                self.assertFalse((self.root / 'escape.txt').exists())

    def test_duplicate_and_symlink_members_rejected(self):
        self.create()
        original = self.archive.read_bytes()
        with warnings.catch_warnings():
            warnings.simplefilter('ignore', UserWarning)
            with zipfile.ZipFile(self.archive, 'a') as archive:
                archive.writestr('history.db', b'duplicate')
        with self.assertRaisesRegex(DataError, '重複'):
            inspect_backup(self.archive)
        self.archive.write_bytes(original)
        entry = zipfile.ZipInfo('reports/link')
        entry.create_system = 3
        entry.external_attr = (stat.S_IFLNK | 0o777) << 16
        with zipfile.ZipFile(self.archive, 'a') as archive:
            archive.writestr(entry, '../outside')
        with self.assertRaisesRegex(DataError, 'リンク'):
            inspect_backup(self.archive)

    def test_unsupported_format_or_missing_manifest_is_rejected(self):
        self.create()
        original = self.archive.read_bytes()
        for version in (2, True):
            self.archive.write_bytes(original)
            def mutate(files):
                manifest = json.loads(files['manifest.json'])
                manifest['version'] = version
                files['manifest.json'] = json.dumps(manifest).encode()
            self.rewrite(mutate)
            with self.assertRaisesRegex(DataError, '形式・バージョン'):
                inspect_backup(self.archive)
        self.archive.write_bytes(original)
        self.rewrite(lambda files: files.pop('manifest.json'))
        with self.assertRaisesRegex(DataError, '必須ファイル欠損'):
            inspect_backup(self.archive)

    def test_capacity_limits_apply_to_inspection_and_creation(self):
        self.create()
        with patch('bunsen.backup.MAX_FILE', 100):
            with self.assertRaisesRegex(DataError, '上限'):
                inspect_backup(self.archive)
        self.archive.unlink()
        with patch('bunsen.backup.MAX_FILE', 100):
            with self.assertRaisesRegex(DataError, '上限'):
                self.create()

    def test_invalid_sqlite_is_rejected_even_with_matching_archive_hash(self):
        self.create()
        def mutate(files):
            files['history.db'] = b'not sqlite'
            manifest = json.loads(files['manifest.json'])
            manifest['files']['history.db'] = dict(size=10, sha256=hashlib.sha256(files['history.db']).hexdigest())
            files['manifest.json'] = json.dumps(manifest).encode()
        self.rewrite(mutate)
        with self.assertRaisesRegex(DataError, '履歴DB'):
            inspect_backup(self.archive)

    def test_unsupported_database_schema_is_rejected(self):
        self.create()
        self.alter_database(lambda db: db.execute('CREATE TABLE unexpected (value TEXT)'))
        with self.assertRaisesRegex(DataError, 'スキーマ'):
            inspect_backup(self.archive)

    def test_corrupt_snapshot_raw_hash_is_rejected(self):
        self.create()
        def mutate(db):
            payload = self.final.copy()
            payload['digest'] = '0' * 64
            db.execute('UPDATE issues SET payload=? WHERE number=?', (json.dumps(payload), self.final['number']))
        self.alter_database(mutate)
        with self.assertRaisesRegex(DataError, '原本ハッシュ'):
            inspect_backup(self.archive)

    def test_missing_issue_event_is_rejected(self):
        self.create()
        self.alter_database(lambda db: db.execute("DELETE FROM events WHERE kind='issue'"))
        with self.assertRaisesRegex(DataError, '発行イベントに欠落'):
            inspect_backup(self.archive)

    def test_bad_event_json_is_rejected(self):
        self.create()
        self.alter_database(lambda db: db.execute("UPDATE events SET payload='broken json' WHERE id=1"))
        with self.assertRaises(DataError):
            inspect_backup(self.archive)

    def test_count_mismatch_is_rejected(self):
        self.create()
        def mutate(files):
            manifest = json.loads(files['manifest.json'])
            manifest['counts']['issues'] += 1
            files['manifest.json'] = json.dumps(manifest).encode()
        self.rewrite(mutate)
        with self.assertRaisesRegex(DataError, '件数'):
            inspect_backup(self.archive)

    def test_restore_copy_failure_leaves_current_data_and_no_completed_folder(self):
        self.create()
        before = file_digest(self.home / 'history.db')
        with patch('bunsen.backup.shutil.copytree', side_effect=OSError('disk full')):
            with self.assertRaises(DataError):
                restore_backup(self.archive, self.root / 'recovery')
        self.assertEqual(file_digest(self.home / 'history.db'), before)
        self.assertEqual(list((self.root / 'recovery').iterdir()), [])

    def test_failed_archive_publish_removes_only_its_new_file(self):
        with patch('bunsen.backup.os.fsync', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                self.create()
        self.assertFalse(self.archive.exists())
        self.assertTrue((self.home / 'history.db').exists())

    def test_pending_transaction_is_rejected_without_waiting(self):
        self.service.db.execute("INSERT INTO events(at,kind,payload) VALUES(?,?,?)", (NOW.isoformat(), 'test', '{}'))
        try:
            with self.assertRaisesRegex(DataError, '未完了のDB更新'):
                self.create()
        finally:
            self.service.db.rollback()

    def test_sqlite_backup_includes_committed_wal_updates(self):
        self.service.db.execute('PRAGMA journal_mode=WAL')
        self.service.event('test_wal', {'value': 'コミット済みWALデータ'})
        expected = self.service.db.execute('SELECT COUNT(*) FROM events').fetchone()[0]
        manifest = self.create()
        self.assertEqual(manifest['counts']['events'], expected)
        restored = restore_backup(self.archive, self.root / 'wal-recovery')
        view = Service(restored / 'history.db', lambda: NOW)
        try:
            content = view.db.execute("SELECT payload FROM events WHERE kind='test_wal'").fetchone()[0]
            self.assertEqual(json.loads(content)['value'], 'コミット済みWALデータ')
            self.assertEqual(view.db.execute('PRAGMA journal_mode').fetchone()[0], 'delete')
        finally:
            view.close()

    def test_exclusive_database_lock_times_out_without_partial_backup(self):
        locked = sqlite3.connect(self.home / 'history.db')
        try:
            locked.execute('BEGIN EXCLUSIVE')
            with patch('bunsen.backup.BACKUP_TIMEOUT_SECONDS', 0.01):
                with self.assertRaisesRegex(DataError, '使用中'):
                    self.create()
            self.assertFalse(self.archive.exists())
        finally:
            locked.rollback()
            locked.close()

    def test_cli_create_inspect_restore_and_app_help(self):
        commands = [[sys.executable, '-m', 'bunsen.backup', 'create', str(self.home), str(self.archive)],
                    [sys.executable, '-m', 'bunsen.backup', 'inspect', str(self.archive)],
                    [sys.executable, '-m', 'bunsen.backup', 'restore', str(self.archive), str(self.root / 'cli-recovery')]]
        for command in commands:
            result = subprocess.run(command, cwd=ROOT, capture_output=True, encoding='utf-8', timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIsInstance(json.loads(result.stdout), dict)
        help_result = subprocess.run([sys.executable, '-m', 'bunsen', '--help'], cwd=ROOT, capture_output=True, timeout=30)
        self.assertEqual(help_result.returncode, 0)

    def test_empty_database_and_legacy_snapshots_remain_supported(self):
        empty_home = self.root / 'empty'
        empty = Service(empty_home / 'history.db', lambda: NOW)
        try:
            manifest = create_backup(empty.db, empty_home, self.root / 'empty.zip', lambda: NOW)
            self.assertEqual(manifest['counts'], dict(events=0, issues=0, last_issue=None))
        finally:
            empty.close()
        legacy = self.final.copy()
        legacy.pop('source_draft')
        self.service.db.execute('UPDATE issues SET payload=? WHERE number=?', (json.dumps(legacy), legacy['number']))
        self.service.db.execute("UPDATE events SET payload=? WHERE kind='issue' AND json_extract(payload,'$.number')=?", (json.dumps(legacy), legacy['number']))
        self.service.db.commit()
        self.create()
        restored = restore_backup(self.archive, self.root / 'legacy')
        view = Service(restored / 'history.db', lambda: NOW)
        try:
            self.assertEqual(view.get_issue(legacy['number']), legacy)
        finally:
            view.close()
