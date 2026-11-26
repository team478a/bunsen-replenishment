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
    def test_portable_user_manual_has_fourteen_pages_and_current_workflow(self):
        import pdfplumber
        with tempfile.TemporaryDirectory() as temp:
            target=Path(temp)/'manual.pdf'
            result=subprocess.run([sys.executable,str(ROOT/'scripts/create_user_manual.py'),'--portable-fonts','--output',str(target)],capture_output=True,timeout=30)
            self.assertEqual(result.returncode,0,result.stderr.decode('utf-8',errors='replace'))
            with pdfplumber.open(target) as pdf:
                self.assertEqual(len(pdf.pages),14)
                text=''.join((page.extract_text() or '').replace('\n','').replace(' ','') for page in pdf.pages)
                self.assertIn('CSVひな形を保存',text);self.assertIn('始業・終業の選択はありません',text)
                self.assertIn('全JANマスタを初期化',text);self.assertIn('展開先',text)
                self.assertIn('乾麺倉庫',text);self.assertIn('他出荷向け取置P',text)
                self.assertIn('必要末日12/15',text);self.assertIn('商品別の安全在庫を保存',text)

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
            self.assertIn('base_forecast_decimal_import', payload['checks'])
            self.assertIn('master_csv_forecast_first_pdf_time', payload['checks'])
            self.assertIn('common_conditions_without_csv', payload['checks'])
            self.assertIn('jan_scope_pdf_absent_and_csv_replace', payload['checks'])
            self.assertIn('isolated_storage_and_backed_up_master_reset', payload['checks'])
            self.assertIn('warehouse_specific_conditions_arrival_and_reports', payload['checks'])
            self.assertIn('dry_stock_csv_and_joint_quantity_comparison', payload['checks'])
            self.assertIn('product_safety_and_two_shipping_lists', payload['checks'])
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

    def test_distribution_keeps_named_files_under_modern_license_directories(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);target=root/'bundle';target.mkdir()
            for name in ['LICENSE.txt','tcl/tk8.6/license.terms','distribution/licenses/Tcl.txt',
                         'fixture.dist-info/licenses/BUILD_LICENSES/pdfium.txt',
                         'fixture.dist-info/licenses/LICENSES/BSD-3-Clause.txt','fixture/module.py']:
                path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text('fixture')
            dist=Mock();dist.files=['fixture.dist-info/licenses/BUILD_LICENSES/pdfium.txt',
                                  'fixture.dist-info/licenses/LICENSES/BSD-3-Clause.txt','fixture/module.py']
            dist.locate_file.side_effect=lambda name:root/name
            with patch.object(build_windows,'ROOT',root),patch.object(build_windows.sys,'base_prefix',str(root)),patch.object(build_windows,'distribution',return_value=dist):
                build_windows.copy_licenses(target,['fixture'])
            self.assertTrue((target/'licenses/build-tools/fixture/fixture.dist-info/licenses/BUILD_LICENSES/pdfium.txt').exists())
            self.assertTrue((target/'licenses/build-tools/fixture/fixture.dist-info/licenses/LICENSES/BSD-3-Clause.txt').exists())
            self.assertFalse((target/'licenses/build-tools/fixture/fixture/module.py').exists())
