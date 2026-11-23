"""Visible data location and non-destructive isolated workspaces."""
import tkinter as tk
from tkinter import ttk,filedialog
from .core import DataError
from .resources import resource_root
from .storage import default_home,legacy_home,new_home,verify_existing
from .ui_theme import responsive_label,scroll_page

class StorageDialog(tk.Toplevel):
    def __init__(self,app):
        super().__init__(app.root);self.app=app
        self.title('データ保存先・空のデータで検証');self.geometry('950x570');self.transient(app.root)
        container=ttk.Frame(self);container.pack(fill='both',expand=True);body=scroll_page(container)
        ttk.Label(body,text='このアプリが開いているデータ',style='Title.TLabel').pack(anchor='w',pady=8)
        for title,path in [('アプリの場所',resource_root()),('現在のデータ保存先',app.home),('標準保存先（展開先ごとに独立）',default_home()),('旧版の共有保存先（自動では開きません）',legacy_home())]:
            ttk.Label(body,text=title).pack(anchor='w',pady=(8,2))
            value=tk.StringVar(value=str(path));entry=ttk.Entry(body,textvariable=value,state='readonly');entry.pack(fill='x')
        responsive_label(body,text='空のデータを新規作成すると、マスタ・補充条件・予測対応・発行履歴を引き継がずに検証できます。今のデータは変更・削除しません。').pack(fill='x',pady=12)
        buttons=ttk.Frame(body);buttons.pack(fill='x',pady=6)
        ttk.Button(buttons,text='空のデータフォルダーを新規作成して開く',command=lambda:app.run(self.create)).pack(side='left',padx=4)
        ttk.Button(buttons,text='既存データフォルダーを選んで開く',command=lambda:app.run(self.open_existing)).pack(side='left',padx=4)
        ttk.Button(body,text='旧版の共有データを明示的に開く',command=lambda:app.run(lambda:self.switch(verify_existing(legacy_home())))).pack(anchor='w',pady=8)
        responsive_label(body,text='保存先の切替では画面上の未保存入力を引き継ぎません。次回起動時は標準のdataフォルダーを開きます。別の保存先は、この画面で再選択するか --data-dir で指定してください。',style='Muted.TLabel').pack(fill='x',pady=8)
        self.grab_set()
    def switch(self,target):
        self.app.switch_home(target)
        if self.app.service.read_only:self.app.restored_home=target
        else:self.app.normal_home=target
        self.app.update_mode();self.app.info.set('データ保存先を切り替えました: '+str(target));self.destroy()
    def create(self):
        parent=filedialog.askdirectory(parent=self,title='空の検証データを作る親フォルダー（既存データは残します）')
        if parent:self.switch(new_home(parent))
    def open_existing(self):
        folder=filedialog.askdirectory(parent=self,title='開くデータフォルダー（history.dbがある場所）')
        if folder:self.switch(verify_existing(folder))

class MasterResetDialog(tk.Toplevel):
    def __init__(self,app):
        super().__init__(app.root);self.app=app;state=app.service.jan_master();self.revision=state['revision']
        self.title('全JANマスタを初期化');self.geometry('850x410');self.transient(app.root)
        container=ttk.Frame(self);container.pack(fill='both',expand=True);body=scroll_page(container)
        ttk.Label(body,text='バックアップ後に、現在のJANマスタを空にします',style='Status.TLabel').pack(anchor='w',pady=8)
        responsive_label(body,text=f"登録中{len(state['all_products'])}件（対象外{len(state['excluded'])}件を含む）を初期化します。過去の出荷指示・取込原本・共通補充条件は削除しません。初期化前にバックアップZIPを保存します。").pack(fill='x',pady=8)
        responsive_label(body,text='対象の保存先: '+str(app.home)).pack(fill='x',pady=8)
        ttk.Label(body,text='担当者').pack(anchor='w');ttk.Entry(body,textvariable=app.operator,width=24).pack(anchor='w',pady=4)
        ttk.Label(body,text='初期化する理由').pack(anchor='w');self.reason=tk.StringVar();ttk.Entry(body,textvariable=self.reason).pack(fill='x',pady=4)
        ttk.Button(body,text='バックアップ保存先を選び、全JANマスタを初期化',state='disabled' if app.service.read_only or not state['all_products'] else 'normal',command=lambda:app.run(self.save)).pack(anchor='w',pady=12)
        self.grab_set()
    def save(self):
        if not self.app.operator.get().strip() or not self.reason.get().strip():raise DataError(['初期化の担当者と理由を入力してください'])
        path=filedialog.asksaveasfilename(parent=self,title='初期化前バックアップの新規保存先（現在のデータフォルダー外）',defaultextension='.zip',filetypes=[('ZIP','*.zip')])
        if not path:return
        self.app.service.reset_jan_master(self.app.operator.get(),self.reason.get(),True,self.revision,path)
        self.app.invalidate();self.app.invalidate_diagnostic();self.app.data=None;self.app.refresh_jan_master(True)
        self.app.info.set('JANマスタを初期化しました。過去の指示は保持しています。初期化前バックアップ: '+path);self.destroy()
