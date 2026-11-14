"""Windows entry point for the destination-replenishment workflow."""
import argparse
import sys
from pathlib import Path
from . import __version__
from .legacy_ui import LegacyApp, tk, filedialog, messagebox, webbrowser
from .replenishment_ui import App


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description='ブンセン 補充出荷指示')
    parser.add_argument('--version', action='version', version=__version__)
    source = parser.add_mutually_exclusive_group()
    source.add_argument('--data-dir', type=Path, help='データフォルダー（復元履歴は自動的に閲覧専用）')
    source.add_argument('--trial-kit', type=Path, help='架空データの受入キット（通常履歴から隔離）')
    args = parser.parse_args()
    trial_day = None
    if args.trial_kit:
        from .acceptance import prepare_trial_home
        args.data_dir, trial_day = prepare_trial_home(args.trial_kit)
    root = tk.Tk()
    app = App(root, args.data_dir)
    if trial_day:
        app.day.set(trial_day.isoformat())
        app.info.set(f'架空データ受入試用。試験日 {trial_day}。実時計で検証します。GUIDE.mdの手順を実施してください。')
    root.mainloop()
