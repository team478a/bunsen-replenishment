"""Native Tk smoke tests with isolated history; no real business data."""
import os
import tempfile
import tkinter as tk
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from bunsen.core import load
from bunsen.ui import App

ROOT = Path(__file__).resolve().parent.parent


class UITests(unittest.TestCase):
    def test_edit_invalidation_and_failed_import(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {'LOCALAPPDATA': folder}):
            root = tk.Tk()
            root.withdraw()
            app = App(root)
            try:
                app.data = load(ROOT / 'samples/morning')
                app.day.set('2026-11-09')
                app.compute()
                root.update()
                self.assertEqual(len(app.tree.get_children()), 2)
                app.tree.selection_set('0')
                app.selected(None)
                app.qty.set('120')
                app.lot.set('L001')
                app.edit()
                self.assertEqual(app.lines[0]['quantity_p'], 120)
                app.ack.set(True)
                app.qty.set('144')
                self.assertFalse(app.ack.get())
                app.day.set('2026-11-10')
                self.assertIsNone(app.lines)
                self.assertEqual(len(app.tree.get_children()), 0)
                # A failed reimport must make the previously imported data unusable.
                with patch('bunsen.ui.filedialog.askdirectory', return_value=folder), patch('bunsen.ui.messagebox.showerror') as error:
                    app.run(app.import_data)
                    self.assertIsNone(app.data)
                    self.assertIsNone(app.lines)
                    error.assert_called_once()
            finally:
                app.close()
