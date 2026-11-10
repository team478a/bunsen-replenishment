"""Frozen-runtime checks using fictional resources and an isolated temporary home."""
import json
import os
from pathlib import Path
import sys
import tempfile
import tkinter as tk

from . import __version__
from .backup import create_backup, restore_backup
from .acceptance import create_kit, prepare_trial_home
from .core import DataError, JST
from .resources import resource_root
from .diagnostics import diagnose, export_report, mapping_template
from .service import write_reports
from .ui import App
from datetime import datetime


def run_smoke(output):
    checks = []
    output = Path(output).resolve()
    if output.exists():
        raise DataError(['診断結果ファイルは上書きしません'])
    with tempfile.TemporaryDirectory(prefix='bunsen-runtime-test-') as temp:
        home = Path(temp)
        previous = os.environ.get('LOCALAPPDATA')
        os.environ['LOCALAPPDATA'] = str(home / 'local')
        root = None
        app = None
        try:
            clock = lambda: datetime(2026, 11, 9, 9, tzinfo=JST)
            diagnostic = diagnose(resource_root() / 'samples/morning', datetime(2026, 11, 9).date(), clock())
            assert diagnostic['contract_compatible'] and diagnostic['calculation_possible']
            assert diagnostic['formal_issue_allowed'] is False
            export_report(diagnostic, home / 'diagnostic.json')
            assert all(field['source_field'] is None for table in mapping_template()['tables'] for field in table['fields'])
            checks.append('read_only_csv_diagnostics')
            root = tk.Tk()
            root.withdraw()
            kit = home / '架空 受入キット'
            create_kit(kit, clock().date(), clock())
            trial_home, _ = prepare_trial_home(kit)
            app = App(root, trial_home)
            assert app.service.trial_only and app.normal_home == trial_home
            assert not (home / 'local' / 'BunsenReplenishment').exists()
            checks.append('isolated_acceptance_kit')
            app.service.clock = clock
            root.update()
            checks.append('tkinter_startup')
            app.data = app.service.import_folder(resource_root() / 'samples/before_morning')
            app.day.set('2026-11-09')
            app.compute()
            draft = app.service.issue(app.data, app.lines, app.dispatch, '', False)
            try:
                app.service.issue(app.data, app.lines, app.dispatch, '架空担当者', True, True)
            except DataError:
                checks.append('morning_gate')
            else:
                raise AssertionError('Morning stock gate failed')
            app.history()
            app.history_list.selection_set(0)
            app.resume_draft()
            app.data = app.service.import_folder(resource_root() / 'samples/multi_lot')
            app.compute()
            for i in range(len(app.lines)):
                app.apply_allocations(i, 120, [{'lot': 'L001', 'quantity_p': 72}, {'lot': 'L002', 'quantity_p': 48}])
            root.update()
            final = app.service.issue(app.data, app.lines, app.dispatch, '架空担当者', True, True,
                                      expected_revision=app.expected_revision, draft_number=draft['number'])
            reports = write_reports(app.service.report_payload(final['number']), app.home / 'reports')
            assert final['trial_only'] and '架空データの試用帳票' in reports[0].read_text(encoding='utf-8')
            from . import ui
            browser = ui.webbrowser.open
            opened = []
            def observe_browser(url):
                if sys.platform == 'win32' and getattr(sys, 'frozen', False):
                    import ctypes
                    buffer = ctypes.create_unicode_buffer(32768)
                    assert ctypes.windll.kernel32.GetDllDirectoryW(len(buffer), buffer) == 0
                    assert buffer.value == ''
                opened.append(url)
                return True
            try:
                ui.webbrowser.open = observe_browser
                App.open_report(reports[0])
                assert opened == [reports[0].as_uri()]
            finally:
                ui.webbrowser.open = browser
            checks.append('external_browser_environment')
            checks.extend(['draft_resume', 'multi_lot_formal_report'])
            archive = home / 'backup.zip'
            manifest = create_backup(app.service.db, app.home, archive, clock)
            restored = restore_backup(archive, home / 'recovery', manifest['archive_sha256'], clock)
            app.restored_home = restored
            app.open_restored()
            assert app.service.read_only and app.service.trial_only
            assert app.service.get_issue(final['number']) == final
            try:
                app.service.issue(None, None, None, '', True, True)
            except DataError:
                checks.append('backup_restore_read_only')
            else:
                raise AssertionError('Read-only gate failed')
            app.return_normal()
            assert not app.service.read_only and app.service.trial_only and app.data is None
            checks.append('return_to_normal')
        finally:
            if app is not None:
                app.close()
            elif root is not None:
                root.destroy()
            if previous is None:
                os.environ.pop('LOCALAPPDATA', None)
            else:
                os.environ['LOCALAPPDATA'] = previous
    result = dict(version=__version__, frozen=bool(getattr(sys, 'frozen', False)),
                  executable=Path(sys.executable).name, checks=checks, success=True)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
    return result
