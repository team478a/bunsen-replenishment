"""Fixed-date, fictional full workflow; never writes production history."""
import tempfile
from datetime import date, datetime
from pathlib import Path

from .core import DataError, JST, shortages
from .service import Service, write_reports


def main():
    root = Path(__file__).resolve().parent.parent
    dispatch = date(2026, 11, 9)
    with tempfile.TemporaryDirectory() as temp:
        service = Service(Path(temp) / 'demo.db', lambda: datetime(2026, 11, 9, 9, tzinfo=JST))
        try:
            data = service.import_folder(root / 'samples/before_morning')
            lines = service.calculate(data, dispatch)
            draft = service.issue(data, lines, dispatch, '架空担当者', False)
            write_reports(draft, root / 'demo-output')
            try:
                service.issue(data, lines, dispatch, '架空担当者', True, True)
            except DataError:
                print('PASS: 朝の現物在庫未確認では正式発行を停止')
            else:
                raise AssertionError('朝在庫ゲート失敗')
            data = service.import_folder(root / 'samples/morning')
            lines = service.calculate(data, dispatch)
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
            final = service.issue(data, lines, dispatch, '架空担当者', True, True)
            for path in write_reports(final, root / 'demo-output'):
                print(path)
            print('PASS: 取込→計算→仮指示→朝照合→不足修正→正式指示')
        finally:
            service.close()


if __name__ == '__main__':
    main()
