"""Simple confirmed JAN/physical-PL registration; no business defaults."""
import tkinter as tk
from tkinter import ttk, filedialog
from pathlib import Path
from .core import DataError
from .jan_catalog import FIELDS, master_csvs
from .requirements_data import NOTICE
from .ui_theme import scroll_page, table_scrollbars, responsive_label


class JanCatalogUI:
    def build_jan_tab(self):
        page=ttk.Frame(self.tabs,padding=12);self.tabs.insert(2,page,text='JAN・倉庫別PLマスタ')
        self.jan_tab=scroll_page(page);self.jan_edit=None;self.jan_revision=0;self.jan_db=None
        ttk.Label(self.jan_tab,text='JAN・倉庫別の物理PLマスタ',font=('Yu Gothic UI',18,'bold')).pack(anchor='w')
        responsive_label(self.jan_tab,text=NOTICE+' 1PLあたりPは、確認済みの物理パレット1枚の数量を入力します。').pack(fill='x',pady=6)
        responsive_label(self.jan_tab,text='品名は全角英数・日本語を使用できます。JAN・期限日数・PLのP数は半角数字で入力してください。CSVは「CSV取込・ひな形」から登録できます。').pack(fill='x',pady=3)
        responsive_label(self.jan_tab,text='一覧のJANを選ぶと上書き編集できます。不要な商品は「対象外にする（削除）」で補充対象から外し、後から戻せます。過去の指示・登録履歴は消しません。').pack(fill='x',pady=3)
        bar=ttk.Frame(self.jan_tab);bar.pack(fill='x')
        ttk.Button(bar,text='新規JAN',command=self.new_jan).pack(side='left',padx=4)
        self.write_button(bar,text='CSV取込・ひな形',command=self.jan_csv_dialog).pack(side='left',padx=4)
        ttk.Button(bar,text='最新一覧',command=lambda:self.refresh_jan_master(True)).pack(side='left',padx=4)
        ttk.Button(bar,text='商品・PLマスタCSVを新規出力',command=lambda:self.run(self.export_jan)).pack(side='left',padx=4)
        listing=ttk.Frame(self.jan_tab);listing.pack(fill='x',pady=6)
        self.jan_tree=ttk.Treeview(listing,columns=('jan','name','kobe','sugito','expiry','active'),show='headings',height=4,selectmode='browse')
        for key,label,width in zip(self.jan_tree['columns'],('JAN','品名','神戸P/物理PL','杉戸P/物理PL','期限','補充対象'),(170,300,150,150,100,100)):
            self.jan_tree.heading(key,text=label);self.jan_tree.column(key,width=width)
        table_scrollbars(self.jan_tree);self.jan_tree.bind('<<TreeviewSelect>>',self.select_jan)
        self.jan_values={};form=ttk.Frame(self.jan_tab);form.pack(fill='x')
        labels=('JAN（13桁）','品名','期限種別','製造からの期限日数','神戸 1物理PLのP数','杉戸 1物理PLのP数')
        for index,(key,label) in enumerate(zip(FIELDS,labels)):
            var=tk.StringVar();self.jan_values[key]=var
            row,col=divmod(index,2)
            ttk.Label(form,text=label).grid(row=row,column=col*2,sticky='w',padx=4,pady=6)
            widget=(ttk.Combobox(form,textvariable=var,values=('賞味期限','消費期限'),state='readonly',width=20)
                    if key=='expiry_type' else ttk.Entry(form,textvariable=var,width=30))
            widget.grid(row=row,column=col*2+1,sticky='ew',padx=4,pady=6)
            if key=='product':self.jan_code=widget
            var.trace_add('write',lambda *_:self.jan_ack.set(False))
        form.columnconfigure(1,weight=1);form.columnconfigure(3,weight=1)
        author=ttk.Frame(self.jan_tab);author.pack(fill='x',pady=6)
        self.jan_operator=self.operator;self.jan_reason=tk.StringVar();self.jan_ack=tk.BooleanVar(value=False)
        for label,var,width in [('担当者',self.jan_operator,18),('登録・変更根拠',self.jan_reason,45)]:
            ttk.Label(author,text=label).pack(side='left');ttk.Entry(author,textvariable=var,width=width).pack(side='left',padx=4)
            var.trace_add('write',lambda *_:self.jan_ack.set(False))
        self.write_button(self.jan_tab,text='確認済みJAN・PLマスタを保存',style='Primary.TButton',command=lambda:self.run(lambda:self.confirmed_action(self.jan_ack,self.save_jan))).pack(anchor='w',pady=6)
        scope_bar=ttk.Frame(self.jan_tab);scope_bar.pack(fill='x',pady=5)
        self.write_button(scope_bar,text='選択JANを対象外にする（削除）',command=lambda:self.run(lambda:self.change_jan_scope(False))).pack(side='left',padx=4)
        self.write_button(scope_bar,text='選択JANを対象に戻す',command=lambda:self.run(lambda:self.change_jan_scope(True))).pack(side='left',padx=4)
        self.jan_status=tk.StringVar();responsive_label(self.jan_tab,textvariable=self.jan_status).pack(fill='x')
        self.use_registered_jan=tk.BooleanVar(value=False)
        mode=ttk.Frame(self.daily);self.jan_master_mode=mode;mode.pack(fill='x',before=self.tree.master)
        ttk.Checkbutton(mode,text='登録JAN・倉庫別PLマスタを使う（requirements.csv方式のみ）',variable=self.use_registered_jan).pack(anchor='w')
        self.use_registered_jan.trace_add('write',lambda *_:self.jan_source_changed())
        self.write_button(mode,text='報告PDFから在庫CSVひな形を作成',command=self.pdf_inventory_dialog).pack(anchor='w',pady=3)
        self.tabs.tab(self.master_tab.master.master,text='旧v2商品マスタ')
        self.page_viewports[str(page)]=self.jan_tab.master
        self.refresh_jan_master(True)

    def jan_source_changed(self):
        if self.use_registered_jan.get():self.use_registered_master.set(False)
        self.invalidate();self.invalidate_diagnostic();self.data=None
        self.info.set('JAN・PLマスタの使用元を変更しました。再取込・再計算してください。')

    def jan_csv_dialog(self):
        from .jan_csv_ui import JanCSVDialog
        return JanCSVDialog(self)

    def refresh_jan_master(self,force=False):
        if not hasattr(self,'jan_tree'):return
        state=self.service.jan_master()
        if not force and self.jan_revision==state['revision'] and self.jan_db==self.service.db_path:return
        if self.jan_db is not None and self.jan_db!=self.service.db_path:
            self.use_registered_jan.set(False);self.jan_operator.set('')
        self.jan_db=self.service.db_path;self.jan_revision=state['revision'];self.jan_products=state['all_products']
        self.jan_tree.delete(*self.jan_tree.get_children())
        for p in self.jan_products:
            self.jan_tree.insert('','end',iid=p['product'],values=(p['product'],p['name'],p['kobe_p_per_pl'],p['sugito_p_per_pl'],p['shelf_life_days'],'対象外' if p['product'] in state['excluded'] else '対象'))
        self.new_jan();self.jan_status.set(f"対象{len(state['products'])}件 / 対象外{len(state['excluded'])}件 / 登録版 {self.jan_revision}。対象外の登録情報と履歴も保持します。")

    def change_jan_scope(self,active):
        if self.jan_edit is None:raise DataError(['対象を変更するJANを一覧から選んでください'])
        self.service.set_jan_active([self.jan_edit],active,self.operator.get(),self.jan_reason.get(),True,self.jan_revision)
        self.invalidate();self.invalidate_diagnostic();self.data=None;self.refresh_jan_master(True)
        self.info.set('補充対象の設定を保存しました。在庫PDF・予測を再取込して計算してください。過去履歴は保持しています。')

    def new_jan(self):
        self.jan_edit=None;self.jan_code.configure(state='normal')
        for var in getattr(self,'jan_values',{}).values():var.set('')
        if hasattr(self,'jan_ack'):self.jan_ack.set(False);self.jan_reason.set('')

    def select_jan(self,_event=None):
        if not self.jan_tree.selection():return
        p=next(p for p in self.jan_products if p['product']==self.jan_tree.selection()[0]);self.jan_edit=p['product']
        for key,var in self.jan_values.items():var.set({'best_before':'賞味期限','use_by':'消費期限'}.get(p[key],p[key]) if key=='expiry_type' else str(p[key]))
        self.jan_code.configure(state='readonly');self.jan_reason.set('');self.jan_ack.set(False)

    def save_jan(self):
        values={key:var.get() for key,var in self.jan_values.items()}
        for key in FIELDS[3:]:
            if not values[key].isascii() or not values[key].isdecimal():raise DataError([{'shelf_life_days':'製造からの期限日数','kobe_p_per_pl':'神戸1PLのP数','sugito_p_per_pl':'杉戸1PLのP数'}[key]+': 半角の正整数を確認入力してください（品名は全角英数可）'])
            values[key]=int(values[key])
        values['expiry_type']={'賞味期限':'best_before','消費期限':'use_by'}.get(values['expiry_type'])
        self.service.save_jan_master(values,self.jan_operator.get(),self.jan_reason.get(),self.jan_ack.get(),self.jan_revision,self.jan_edit is None)
        linked=self.data is not None and self.data.jan_master_revision is not None
        self.invalidate();self.invalidate_diagnostic()
        if linked:self.data=None
        self.refresh_jan_master(True);self.info.set('JAN・PLマスタを保存しました。再取込・再計算・再確認してください。')

    def export_jan(self):
        state=self.service.jan_master()
        if not state['products']:raise DataError(['登録JANマスタがありません'])
        folder=filedialog.askdirectory(title='商品・PLマスタCSVの新規保存先')
        if not folder:return
        texts=master_csvs(state['products']);created=[]
        try:
            for name,text in texts.items():
                path=Path(folder)/name
                with path.open('x',encoding='utf-8-sig',newline='') as stream:created.append(path);stream.write(text)
        except Exception:
            for path in created:path.unlink(missing_ok=True)
            raise
        self.jan_status.set('products.csv / pallets.csv を新規保存しました。元の業務Excelは変更していません。')

    def pdf_inventory_dialog(self):
        from .pdf_reader_ui import PDFInventoryDialog
        if not self.service.jan_master()['products']:raise DataError(['先に確認済みJAN・PLマスタを登録してください'])
        return PDFInventoryDialog(self)
