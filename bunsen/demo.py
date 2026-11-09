"""Fixed-date, fictional full workflow; never writes production history."""
import tempfile
import sys
from datetime import date, datetime
from pathlib import Path

from .core import DataError, JST, shortages
from .backup import create_backup, restore_backup
from .service import Service, write_reports


def main():
    # Windows CI and English Windows may otherwise use cp1252 for redirected logs.
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    root = Path(__file__).resolve().parent.parent
    dispatch = date(2026, 11, 9)
    with tempfile.TemporaryDirectory() as temp:
        service = Service(Path(temp) / 'demo.db', lambda: datetime(2026, 11, 9, 9, tzinfo=JST))
        try:
            data = service.import_folder(root / 'samples/before_morning')
            lines = service.calculate(data, dispatch)
            draft = service.issue(data, lines, dispatch, '架空担当者', False)
            write_reports(service.report_payload(draft['number']), root / 'demo-output')
            try:
                service.issue(data, lines, dispatch, '架空担当者', True, True)
            except DataError:
                print('PASS: 朝の現物在庫未確認では正式発行を停止')
            else:
                raise AssertionError('朝在庫ゲート失敗')
            service.close()
            service = Service(Path(temp) / 'demo.db', lambda: datetime(2026, 11, 9, 9, tzinfo=JST))
            _, working, _ = service.resume_draft(draft['number'])
            data = service.import_folder(root / 'samples/morning')
            lines = service.reconcile_draft(draft['number'], data, working)
            print('PASS: DBを開き直して仮指示再開→朝データで数量を保持して照合')
            print('\n'.join(shortages(data, lines)))
            try:
                for row in lines:
                    row['lot'] = 'L001'
                service.issue(data, lines, dispatch, '架空担当者', True, True)
            except DataError:
                print('PASS: 倉庫横断の二重割当を停止')
            else:
                raise AssertionError('二重割当ゲート失敗')
            for row in lines:
                before = dict(row)
                row.update(quantity_p=120, lot='L001')
                service.event('edit', {'before': before, 'after': row})
            final = service.issue(data, lines, dispatch, '架空担当者', True, True, draft_number=draft['number'])
            for path in write_reports(service.report_payload(final['number']), root / 'demo-output'):
                print(path)
            print('PASS: 取込→計算→仮指示→朝照合→不足修正→正式指示')
            write_reports(service.report_payload(final['number']), Path(temp) / 'reports')
            with tempfile.TemporaryDirectory(prefix='bunsen-demo-recovery-') as recovery:
                archive = Path(recovery) / 'sample.zip'
                manifest = create_backup(service.db, temp, archive, service.clock)
                restored = restore_backup(archive, Path(recovery) / 'restored', manifest['archive_sha256'], service.clock)
                view = Service(restored / 'history.db', service.clock)
                try:
                    assert view.get_issue(final['number']) == final
                    try:
                        view.issue(data, lines, dispatch, '架空担当者', True, True)
                    except DataError:
                        print('PASS: バックアップ→新規復元→履歴照合・閲覧専用の発行防止')
                    else:
                        raise AssertionError('復元履歴の発行防止失敗')
                finally:
                    view.close()
            multi = Service(Path(temp) / 'multi-lot.db', service.clock)
            try:
                data = multi.import_folder(root / 'samples/multi_lot')
                lines = multi.calculate(data, dispatch)
                for row in lines:
                    before = dict(row)
                    row.update(quantity_p=120, lot='', allocations=[{'lot': 'L001', 'quantity_p': 72}, {'lot': 'L002', 'quantity_p': 48}])
                    multi.event('edit', {'before': before, 'after': row})
                final = multi.issue(data, lines, dispatch, '架空担当者', True, True)
                for path in write_reports(multi.report_payload(final['number']), root / 'demo-output/multi-lot'):
                    print(path)
                print('PASS: 複数ロット分割（各倉庫L001=72P＋L002=48P）→正式指示')
            finally:
                multi.close()
        finally:
            service.close()


if __name__ == '__main__':
    main()
