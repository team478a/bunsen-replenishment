import json,os,tempfile,tkinter as tk,unittest
import sys
from pathlib import Path
from datetime import date,datetime
from unittest.mock import patch
from bunsen.core import DataError,JST
from bunsen.forecast_demo import MASTER
from bunsen.service import Service
from bunsen.ui import App
from bunsen.storage import default_home,legacy_home,new_home,verify_existing
from bunsen.storage_ui import StorageDialog,MasterResetDialog
from bunsen.backup import validate_database,file_digest,restore_backup,inspect_backup,create_backup
from bunsen.jan_demo import create_kit

NOW=datetime(2026,11,9,20,tzinfo=JST)
class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name).resolve();self.apps=[]
        self.env=patch.dict(os.environ,LOCALAPPDATA=str(self.path/'legacy'));self.env.start()
        service=Service(legacy_home()/'history.db',lambda:NOW);service.save_jan_master(MASTER,'旧架空担当','共有元',True,0,True);service.close()
        self.old_hash=file_digest(legacy_home()/'history.db')
    def tearDown(self):
        for root,app in self.apps:
            app.service.close()
            try:root.destroy()
            except tk.TclError:pass
        self.env.stop();self.temp.cleanup()
    def app(self,program='appA'):
        root=tk.Tk();root.withdraw()
        with patch('bunsen.storage.resource_root',return_value=self.path/program):app=App(root)
        app.service.clock=lambda:NOW
        self.apps.append((root,app))
        return root,app
    def test_different_distribution_directories_are_independent_and_reopen_own_data(self):
        root,a=self.app()
        a.service.save_jan_master(MASTER,'担当','Aだけ',True,0,True);self.assertEqual(a.home,self.path/'appA/data')
        a.close();root,b=self.app('appB')
        self.assertEqual(b.service.jan_master()['all_products'],[]);b.close()
        root,a=self.app();self.assertEqual(len(a.service.jan_master()['products']),1);a.close()
        self.assertEqual(file_digest(legacy_home()/'history.db'),self.old_hash)
    def test_frozen_default_follows_executable_and_explicit_home_is_respected(self):
        for name in ('first','second'):
            with patch.object(sys,'frozen',True,create=True),patch.object(sys,'executable',str(self.path/name/'BunsenReplenishment.exe')):
                self.assertEqual(default_home(),self.path/name/'data')
        root=tk.Tk();root.withdraw();app=App(root,legacy_home())
        try:self.assertEqual(len(app.service.jan_master()['all_products']),1);self.assertEqual(app.home,legacy_home())
        finally:app.close()
    def test_explicit_data_dir_and_blank_switch_refresh_all_ui(self):
        root,a=self.app()
        try:
            a.service.save_jan_master(MASTER,'担当','A',True,0,True)
            a.service.save_common_settings(dict(cycle_days=1,lead_days=0,safety_days=1,coverage_days=2),'担当','A',True,0)
            a.refresh_jan_master(True);a.display_settings();self.assertTrue(a.jan_tree.get_children())
            a.last_number='OLD';a.diagnostic_folder.set('old-folder');a.setting_reason.set('旧入力')
            dialog=StorageDialog(a)
            with patch('bunsen.storage_ui.filedialog.askdirectory',return_value=str(self.path)):dialog.create()
            self.assertFalse(a.jan_tree.get_children());self.assertTrue(all(v.get()=='' for v in a.setting_values.values()))
            self.assertEqual(a.service.forecast_mapping()['mapping'],{});self.assertEqual(a.service.history(),[])
            self.assertEqual(a.home,a.normal_home);self.assertIn(str(a.home),a.storage_info.get())
            self.assertIsNone(a.last_number);self.assertEqual(a.diagnostic_folder.get(),'');self.assertEqual(a.setting_reason.get(),'')
            self.assertEqual(file_digest(legacy_home()/'history.db'),self.old_hash)
        finally:a.close()
    def test_opening_legacy_is_explicit_and_foreign_folders_are_not_modified(self):
        invalid=self.path/'not-a-database';invalid.mkdir()
        with self.assertRaises(DataError):verify_existing(invalid)
        self.assertFalse((invalid/'history.db').exists())
        root,a=self.app()
        try:
            self.assertFalse(a.service.jan_master()['products'])
            dialog=StorageDialog(a);dialog.switch(verify_existing(legacy_home()))
            self.assertEqual(len(a.jan_tree.get_children()),1);self.assertEqual(a.home,legacy_home())
        finally:a.close()
    def test_reset_is_backed_up_empty_current_master_preserves_history_and_allows_reregistration(self):
        root,a=self.app()
        try:
            a.service.save_jan_master(MASTER,'担当','確認',True,0,True)
            kit=create_kit(self.path/'kit',date(2026,11,9));data=a.service.import_folder(kit/'csv',workflow=2)
            final=a.service.issue(data,a.service.calculate(data,date(2026,11,9)),date(2026,11,9),'担当',True,True)
            revision=a.service.jan_master()['revision'];backup=self.path/'before-reset.zip'
            a.service.reset_jan_master('担当','最初から検証',True,revision,backup)
            self.assertFalse(a.service.jan_master()['all_products']);self.assertEqual(a.service.get_issue(final['number']),final)
            validate_database(a.service.db)
            manifest=inspect_backup(backup);restored=restore_backup(backup,self.path/'restore',manifest['archive_sha256'],a.service.clock)
            view=Service(restored/'history.db',lambda:NOW)
            try:self.assertEqual(len(view.jan_master()['all_products']),1)
            finally:view.close()
            a.service.save_jan_master(MASTER,'担当','再登録',True,a.service.jan_master()['revision'],True);validate_database(a.service.db)
        finally:a.close()
    def test_reset_cancel_backup_error_and_revision_conflict_do_not_clear(self):
        root,a=self.app()
        try:
            a.service.save_jan_master(MASTER,'担当','確認',True,0,True);revision=a.service.jan_master()['revision']
            dialog=MasterResetDialog(a);a.operator.set('担当');dialog.reason.set('検証')
            with patch('bunsen.storage_ui.filedialog.asksaveasfilename',return_value=''):dialog.save()
            self.assertEqual(a.service.jan_master()['revision'],revision);dialog.destroy()
            with patch('bunsen.backup.create_backup',side_effect=OSError('容量不足')):
                with self.assertRaises(OSError):a.service.reset_jan_master('担当','検証',True,revision,self.path/'failed.zip')
            with self.assertRaises(DataError):a.service.reset_jan_master('担当','検証',True,0,self.path/'stale.zip')
            self.assertFalse((self.path/'stale.zip').exists());self.assertEqual(a.service.jan_master()['revision'],revision)
            a.service.read_only=True
            with self.assertRaises(DataError):a.service.reset_jan_master('担当','検証',True,revision,self.path/'ro.zip')
        finally:a.close()
    def test_new_directory_is_empty_and_failed_default_never_falls_back_to_legacy(self):
        target=new_home(self.path);self.assertEqual(list(target.iterdir()),[])
        bad=self.path/'not-directory';bad.write_text('occupied')
        root=tk.Tk();root.withdraw()
        try:
            with patch('bunsen.storage.resource_root',return_value=bad):
                with self.assertRaises((OSError,ValueError)):App(root)
            self.assertEqual(file_digest(legacy_home()/'history.db'),self.old_hash)
        finally:root.destroy()
    def test_change_during_backup_prevents_reset(self):
        root,a=self.app()
        try:
            a.service.save_jan_master(MASTER,'担当','初回',True,0,True);revision=a.service.jan_master()['revision']
            def concurrent(*args):
                result=create_backup(*args)
                a.service.save_jan_master(dict(MASTER,name='変更済み架空商品'),'担当','並行編集',True,revision,False)
                return result
            with patch('bunsen.backup.create_backup',side_effect=concurrent):
                with self.assertRaises(DataError):a.service.reset_jan_master('担当','検証',True,revision,self.path/'concurrent.zip')
            self.assertEqual(a.service.jan_master()['products'][0]['name'],'変更済み架空商品')
            validate_database(a.service.db)
        finally:a.close()
    def test_post_reset_backup_and_tampered_reset_are_validated(self):
        root,a=self.app()
        try:
            a.service.save_jan_master(MASTER,'担当','初回',True,0,True)
            state=a.service.reset_jan_master('担当','検証',True,a.service.jan_master()['revision'],self.path/'before.zip')
            after=self.path/'after.zip';manifest=create_backup(a.service.db,a.home,after,a.service.clock)
            restored=restore_backup(after,self.path/'after-restore',manifest['archive_sha256'],a.service.clock)
            view=Service(restored/'history.db',lambda:NOW)
            try:self.assertEqual(view.jan_master()['all_products'],[])
            finally:view.close()
            payload=json.loads(a.service.db.execute('SELECT payload FROM events WHERE id=?',(state['revision'],)).fetchone()[0]);payload['before']=[]
            a.service.db.execute('UPDATE events SET payload=? WHERE id=?',(json.dumps(payload),state['revision']));a.service.db.commit()
            with self.assertRaises(DataError):validate_database(a.service.db)
        finally:a.close()
