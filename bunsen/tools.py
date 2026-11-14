"""Console entry point shared by source and Windows distribution."""
import argparse
from datetime import date
import json
from pathlib import Path
import sys

from . import __version__


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8')
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] == 'backup':
        from .backup import main as backup_main
        return backup_main(args[1:])
    parser = argparse.ArgumentParser(description='ブンセン 復旧・試用診断ツール')
    parser.add_argument('--version', action='version', version=__version__)
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('backup', help='バックアップのcreate / inspect / restore')
    test = sub.add_parser('self-test', help='架空データ・一時DBによる起動診断')
    test.add_argument('--output', type=Path, required=True)
    demo = sub.add_parser('demo', help='固定日付の架空データデモ')
    demo.add_argument('--output', type=Path, required=True)
    diagnose = sub.add_parser('diagnose', help='仮CSV契約に対する読取専用の取込前診断')
    diagnose.add_argument('folder', type=Path)
    diagnose.add_argument('--dispatch', type=date.fromisoformat, required=True)
    diagnose.add_argument('--output', type=Path, required=True, help='新規JSON保存先（HTMLも同時保存）')
    mapping = sub.add_parser('mapping-template', help='実CSV項目対応表の未確認ひな形')
    mapping.add_argument('--output', type=Path, required=True)
    kit = sub.add_parser('acceptance-kit', help='指定試験日の架空CSV・隔離起動用受入キットを新規作成')
    kit.add_argument('--dispatch', type=date.fromisoformat, required=True)
    kit.add_argument('--output', type=Path, required=True)
    outlook = sub.add_parser('outlook', help='補充前の14日参考見通し（出荷指示ではありません）')
    outlook.add_argument('folder', type=Path)
    outlook.add_argument('--start', type=date.fromisoformat, required=True)
    outlook.add_argument('--output', type=Path, required=True)
    parsed = parser.parse_args(args)
    if parsed.command == 'self-test':
        from .smoke import run_smoke
        print(json.dumps(run_smoke(parsed.output), ensure_ascii=True))
    elif parsed.command == 'demo':
        from .demo import main as demo_main
        demo_main(parsed.output)
    elif parsed.command == 'acceptance-kit':
        from .acceptance import create_kit
        from .core import DataError
        try:
            result = create_kit(parsed.output, parsed.dispatch, workflow=2)
            print(json.dumps(dict(output=str(parsed.output.resolve()), dispatch=result['dispatch'],
                                  trial_only=True, acceptance_status='not_performed'), ensure_ascii=True))
        except (DataError, OSError) as exc:
            parser.exit(1, str(exc) + '\n')
    elif parsed.command == 'outlook':
        from .core import DataError, load
        from .outlook import build_outlook, export_outlook
        try:
            report = build_outlook(load(parsed.folder), parsed.start)
            paths = export_outlook(report, parsed.output, parsed.folder)
            print(json.dumps(dict(status=report['status'], issues=len(report['issues']), formal_issue_allowed=False,
                                  outputs=[str(p) for p in paths]), ensure_ascii=True))
            if report['issues']:
                raise SystemExit(2)
        except (DataError, OSError) as exc:
            parser.exit(1, str(exc) + '\n')
    elif parsed.command in ('diagnose', 'mapping-template'):
        from .core import DataError
        from .diagnostics import diagnose, export_report, export_template
        try:
            if parsed.command == 'mapping-template':
                path = export_template(parsed.output,workflow=2)
                print(json.dumps(dict(path=str(path), status='unconfirmed'), ensure_ascii=True))
            else:
                report = diagnose(parsed.folder, parsed.dispatch,workflow=2)
                paths = export_report(report, parsed.output)
                print(json.dumps(dict(status=report['status'], issues=len(report['issues']),
                                      formal_issue_allowed=False, outputs=[str(path) for path in paths]), ensure_ascii=True))
                if report['issues']:
                    raise SystemExit(2)
        except (DataError, OSError) as exc:
            parser.exit(1, str(exc) + '\n')


if __name__ == '__main__':
    main()
