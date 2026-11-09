"""Locate read-only distribution resources, independently of the working folder."""
from pathlib import Path
import sys


def resource_root():
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent
