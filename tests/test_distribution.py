import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from bunsen import __version__
from bunsen.core import DataError
from bunsen.resources import resource_root
from bunsen.ui import LegacyApp as App
from scripts import build_windows

ROOT = Path(__file__).resolve().parent.parent


class DistributionTests(unittest.TestCase):
    def test_source_and_frozen_resource_paths_ignore_working_directory(self):
        self.assertEqual(resource_root(), ROOT)
        with patch.object(sys, 'frozen', True, create=True), patch.object(sys, 'executable', str(ROOT / '日本語 空白/BunsenTools.exe')):
            self.assertEqual(resource_root(), ROOT / '日本語 空白')

    def test_console_version_backup_help_and_isolated_self_test(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            env = dict(os.environ, LOCALAPPDATA=str(root / 'normal'), PYTHONIOENCODING='cp1252')
            version = subprocess.run([sys.executable, '-m', 'bunsen.tools', '--version'], cwd=ROOT, env=env, capture_output=True, encoding='utf-8', timeout=30)
            self.assertEqual(version.returncode, 0, version.stderr)
            self.assertEqual(version.stdout.strip(), __version__)
            help_result = subprocess.run([sys.executable, '-m', 'bunsen.tools', 'backup', '--help'], cwd=ROOT, env=env, capture_output=True, encoding='utf-8', timeout=30)
            self.assertEqual(help_result.returncode, 0, help_result.stderr)
            result = root / 'smoke.json'
            run = subprocess.run([sys.executable, '-m', 'bunsen.tools', 'self-test', '--output', str(result)], cwd=ROOT, env=env, capture_output=True, encoding='utf-8', timeout=60)
            self.assertEqual(run.returncode, 0, run.stderr)
            payload = json.loads(result.read_text(encoding='utf-8'))
            self.assertTrue(payload['success'])
            self.assertFalse(payload['frozen'])
            self.assertIn('backup_restore_read_only', payload['checks'])
            self.assertFalse((root / 'normal').exists())

    def test_build_resources_copy_only_allowlisted_tracked_files(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source, target = root / 'source', root / 'target'
            (source / 'samples').mkdir(parents=True)
            (source / 'samples/fake.csv').write_text('fictional', encoding='utf-8')
            (source / 'samples/untracked.csv').write_text('do not include', encoding='utf-8')
            (source / 'data').mkdir()
            (source / 'data/history.db').write_bytes(b'not a real database')
            with patch.object(build_windows, 'ROOT', source), patch.object(build_windows, 'git', return_value='samples/fake.csv'):
                build_windows.copy_resources(target)
            self.assertEqual([p.relative_to(target).as_posix() for p in target.rglob('*') if p.is_file()], ['samples/fake.csv'])

    def test_frozen_browser_path_is_cleared_and_restored_on_failure(self):
        if sys.platform != 'win32':
            self.skipTest('Windows DLL API')
        kernel = Mock()
        def get_directory(_size, buffer):
            buffer.value = r'C:\bundle\_internal'
            return len(buffer.value)
        kernel.GetDllDirectoryW.side_effect = get_directory
        kernel.SetDllDirectoryW.return_value = 1
        with patch.object(sys, 'frozen', True, create=True), patch('ctypes.WinDLL', return_value=kernel), patch('bunsen.ui.webbrowser.open', side_effect=OSError('test browser failure')):
            with self.assertRaises(OSError):
                App.open_report(ROOT / 'report.html')
        self.assertEqual(kernel.SetDllDirectoryW.call_args_list[0].args, (None,))
        self.assertEqual(kernel.SetDllDirectoryW.call_args_list[1].args, (r'C:\bundle\_internal',))
