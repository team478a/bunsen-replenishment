"""Console entry point shared by source and Windows distribution."""
import argparse
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
    parsed = parser.parse_args(args)
    if parsed.command == 'self-test':
        from .smoke import run_smoke
        print(json.dumps(run_smoke(parsed.output), ensure_ascii=True))
    elif parsed.command == 'demo':
        from .demo import main as demo_main
        demo_main(parsed.output)


if __name__ == '__main__':
    main()
