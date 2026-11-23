"""Fictional fixed-date check of storage isolation and backed-up master reset."""
import os,tkinter as tk
from pathlib import Path
from datetime import datetime
from unittest.mock import patch
from .core import JST
from .forecast_demo import MASTER
from .service import Service
from .ui import App
from .storage import legacy_home,new_home,verify_existing
from .storage_ui import StorageDialog,MasterResetDialog
from .backup import file_digest,validate_database,inspect_backup,restore_backup

def main(output):
    home=Path(output).resolve();home.mkdir(parents=True,exist_ok=False)
    clock=lambda:datetime(2026,11,9,20,tzinfo=JST)
    with patch.dict(os.environ,LOCALAPPDATA=str(home/'legacy')):
        service=Service(legacy_home()/'history.db',clock)
        service.save_jan_master(MASTER,'架空担当','旧共有データ',True,0,True);service.close()
        original=file_digest(legacy_home()/'history.db')
        def open_app(folder):
            root=tk.Tk();root.withdraw()
            with patch('bunsen.storage.resource_root',return_value=home/folder):app=App(root)
            app.service.clock=clock
            return app
        a=open_app('A')
        try:
            assert a.service.jan_master()['all_products']==[]
            a.service.save_jan_master(MASTER,'架空担当','Aのデータ',True,0,True)
        finally:a.close()
        b=open_app('B')
        try:assert b.service.jan_master()['all_products']==[]
        finally:b.close()
        a=open_app('A')
        try:
            state=a.service.jan_master();assert len(state['all_products'])==1
            a.service.set_jan_active([MASTER['product']],False,'架空担当','初期化前の対象外',True,state['revision'])
            a.operator.set('架空担当');dialog=MasterResetDialog(a);dialog.reason.set('架空検証のやり直し')
            backup=home/'before-reset.zip'
            with patch('bunsen.storage_ui.filedialog.asksaveasfilename',return_value=str(backup)):dialog.save()
            assert a.service.jan_master()['all_products']==[] and not a.jan_tree.get_children()
            validate_database(a.service.db)
            manifest=inspect_backup(backup);restored=restore_backup(backup,home/'restore',manifest['archive_sha256'],clock)
            view=Service(restored/'history.db',clock)
            try:assert len(view.jan_master()['all_products'])==1 and len(view.jan_master()['excluded'])==1
            finally:view.close()
            a.service.save_jan_master(MASTER,'架空担当','同じJANを再登録',True,a.service.jan_master()['revision'],True)
            a.refresh_jan_master(True);assert a.jan_tree.get_children()
            StorageDialog(a).switch(new_home(home))
            assert not a.jan_tree.get_children() and a.service.history()==[]
            StorageDialog(a).switch(verify_existing(legacy_home()))
            assert len(a.jan_tree.get_children())==1
        finally:a.close()
        assert file_digest(legacy_home()/'history.db')==original
    print('PASS: separate app homes, explicit legacy open, backed-up master reset, re-registration, empty workspace.')
    return home
