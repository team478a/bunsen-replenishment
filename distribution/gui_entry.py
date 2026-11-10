"""Windowed executable, with an isolated automated startup check."""
import argparse
import os
import json
from pathlib import Path
import sys

for name in ('stdout', 'stderr'):
    if getattr(sys, name) is None:
        setattr(sys, name, open(os.devnull, 'w', encoding='utf-8'))

if len(sys.argv) > 1 and sys.argv[1] == '--self-test-output':
    from bunsen.smoke import run_smoke
    parser = argparse.ArgumentParser()
    parser.add_argument('--self-test-output', type=Path, required=True)
    output = parser.parse_args().self_test_output
    try:
        run_smoke(output)
    except Exception:
        import traceback
        detail = traceback.format_exc()
        try:
            output.parent.mkdir(parents=True, exist_ok=True)
            with output.open('x', encoding='utf-8') as stream:
                json.dump(dict(success=False, error=detail), stream, ensure_ascii=False)
        except OSError:
            pass  # Exit rather than opening an unattended exception dialog.
        raise SystemExit(1)
else:
    from bunsen.ui import main
    try:
        main()
    except Exception as exc:
        from tkinter import messagebox
        messagebox.showerror('ブンセン — 起動できません', str(exc))
        raise SystemExit(1)
