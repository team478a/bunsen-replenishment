"""Daily actions with advanced tools kept outside the working path."""
import tkinter as tk
from tkinter import ttk, filedialog

from .core import DataError, validate
from .shipping_output import write_documents
from .requirements_data import active, pallet_p
from .ui_theme import section, table_scrollbars, text_panel, responsive_label


class SimpleUI:
    def build_daily(self):
        ttk.Label(self.daily,text='補充する数量を確認して出力',style='Title.TLabel').pack(anchor='w',pady=5)
        bar=section(self.daily,'1  データを開く')
        ttk.Label(bar,text='出荷日').pack(side='left')
        ttk.Entry(bar,textvariable=self.day,width=13).pack(side='left',padx=6)
        self.write_button(bar,text='データを開いて計算',style='Primary.TButton',command=lambda:self.run(self.open_and_compute)).pack(side='left',padx=5)
        self.write_button(bar,text='再計算',command=lambda:self.run(self.compute)).pack(side='left')
        self.result_status=tk.StringVar(value='データのフォルダーを選んでください')
        responsive_label(self.daily,textvariable=self.result_status,style='Status.TLabel').pack(fill='x',pady=5)
        frame=ttk.Frame(self.daily);frame.pack(fill='x',pady=5)
        cols=('warehouse','product','inventory','demand','safety','required','suggested','quantity','final','units','arrival')
        self.tree=ttk.Treeview(frame,columns=cols,displaycolumns=('warehouse','product','inventory','required','units','arrival'),show='headings',height=5,selectmode='browse')
        for col,label,width in zip(cols,('倉庫','商品','現在庫 P','需要予測 P','安全在庫 P','必要量 P','換算後 P','指示量 P','確定 P','出荷数量','到着日'),(90,330,100,100,100,100,100,100,100,150,110)):
            self.tree.heading(col,text=label);self.tree.column(col,width=width)
        table_scrollbars(self.tree);self.tree.bind('<<TreeviewSelect>>',self.selected)
        self.editor=section(self.daily,'選択した商品の数量を修正');self.editor.pack_forget()
        self.edit_amount=tk.StringVar()
        self.edit_unit=ttk.Label(self.editor,text='指示量 PL');self.edit_unit.pack(side='left')
        ttk.Entry(self.editor,textvariable=self.edit_amount,width=10).pack(side='left',padx=4)
        ttk.Label(self.editor,text='理由').pack(side='left')
        ttk.Entry(self.editor,textvariable=self.reason,width=40).pack(side='left',padx=4)
        self.write_button(self.editor,text='修正を反映',command=lambda:self.run(self.edit_display_quantity)).pack(side='left',padx=4)
        self.output_bar=section(self.daily,'2  数量を確認して出力')
        ttk.Label(self.output_bar,text='担当者').pack(side='left')
        ttk.Entry(self.output_bar,textvariable=self.operator,width=18).pack(side='left',padx=6)
        self.write_button(self.output_bar,text='確定してExcel・PDF出力',style='Primary.TButton',command=lambda:self.run(self.confirm_and_export)).pack(side='left',padx=5)
        ttk.Label(self.daily,text='商品を選ぶと数量を修正できます。出力ボタンで担当者の確認・確定を行います。',style='Muted.TLabel').pack(anchor='w',pady=4)
        self.detail_toggle=ttk.Button(self.daily,text='計算根拠・取込情報を表示',command=self.toggle_details);self.detail_toggle.pack(anchor='w')
        self.details_frame=section(self.daily,'計算根拠・不足・未確認事項');self.details_frame.pack_forget()
        responsive_label(self.details_frame,textvariable=self.info,style='Muted.TLabel').pack(fill='x')
        self.detail=tk.Text(self.details_frame,height=8,wrap='word');text_panel(self.detail);self.detail.pack(fill='both',expand=True)

    def toggle_details(self):
        if self.details_frame.winfo_manager():
            self.details_frame.pack_forget();self.detail_toggle.configure(text='計算根拠・取込情報を表示')
        else:
            self.details_frame.pack(fill='both',expand=True,pady=5);self.detail_toggle.configure(text='計算根拠・取込情報を閉じる')

    def open_and_compute(self):
        if self.import_data(automatic_sources=True):self.compute()

    def edit_display_quantity(self):
        if not self.tree.selection():raise DataError(['修正する商品を選択してください'])
        text=self.edit_amount.get()
        if not text.isascii() or not text.isdecimal():raise DataError(['数量は0以上の整数で入力してください'])
        row=self.lines[int(self.tree.selection()[0])]
        factor=pallet_p(self.data,row['warehouse'],row['product']) if active(self.data) else 1
        self.qty.set(str(int(text)*factor));self.edit()

    def confirm_and_export(self):
        if not self.lines:raise DataError(['データを開いて計算してください'])
        if not self.finalized:
            self.ensure_no_pending_edit()
            if not self.operator.get().strip():raise DataError(['担当者を入力してください'])
            errors=validate(self.data,self.lines,self.dispatch,self.service.clock())
            if errors:raise DataError(errors)
        folder=filedialog.askdirectory(title='Excel・PDFの保存先（キャンセルすると確定しません）')
        if not folder:return
        if not self.finalized:
            self.ack.set(True)
            try:self.issue(True)
            finally:self.ack.set(False)
        # Export retries reuse the saved instruction, without creating another revision.
        paths=write_documents(self.service.report_payload(self.last_number),folder)
        self.result_status.set(f'出力完了  {self.last_number}  /  {len(paths)}ファイル  /  {folder}')
        self.info.set(self.result_status.get())

    def finish_simple_navigation(self):
        self.jan_master_mode.pack_forget();self.legacy_master_mode.pack_forget()
        self.primary_page=str(self.daily.master.master)
        self.history_page=ttk.Frame(self.tabs,padding=16);self.tabs.add(self.history_page,text='履歴')
        ttk.Label(self.history_page,text='保存した出荷指示',style='Title.TLabel').pack(anchor='w',pady=8)
        self.simple_history=tk.Listbox(self.history_page,height=15);self.simple_history.pack(fill='both',expand=True)
        ttk.Button(self.history_page,text='選択した指示をExcel・PDF出力',command=lambda:self.run(self.export_history)).pack(anchor='w',pady=8)
        self.admin_pages=[p for p in self.tabs.tabs() if p not in (self.primary_page,str(self.history_page))]
        menu=tk.Menu(self.root);admin=tk.Menu(menu,tearoff=False);menu.add_cascade(label='管理・その他',menu=admin)
        for page in self.admin_pages:
            label=self.tabs.tab(page,'text')
            admin.add_command(label=label,command=lambda p=page:self.open_admin(p))
            self.tabs.hide(page)
        admin.add_separator();admin.add_command(label='報告PDFから在庫CSVひな形を作成',command=self.pdf_inventory_dialog)
        admin.add_checkbutton(label='暫定予測で参考計算（正式出力不可）',variable=self.allow_provisional)
        self.root.configure(menu=menu)
        self.tabs.bind('<<NotebookTabChanged>>',self.hide_admin_pages,add='+')
        self.tabs.select(self.primary_page)

    def open_admin(self,page):
        for p in self.admin_pages:self.tabs.hide(p)
        self.tabs.add(page);self.tabs.select(page)

    def hide_admin_pages(self,_event=None):
        if self.tabs.select() in (self.primary_page,str(self.history_page)):
            for p in self.admin_pages:self.tabs.hide(p)

    def export_history(self):
        selection=self.simple_history.curselection()
        if not selection:raise DataError(['履歴を選んでください'])
        self.history_list.selection_clear(0,'end');self.history_list.selection_set(selection[0])
        self.export_saved()

    def confirmed_action(self,flag,action):
        """The explicit save button is the confirmation; never retain acknowledgement."""
        flag.set(True)
        try:return action()
        finally:flag.set(False)
