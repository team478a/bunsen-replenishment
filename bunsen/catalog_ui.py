"""Confirmed product registration, separated from daily warehouse operations."""
import tkinter as tk
from tkinter import ttk, filedialog

from .catalog import catalog_csv
from .core import DataError, case_p
from .ui_theme import table_scrollbars, section, responsive_label, scroll_page

LABELS={'product':'商品コード','name':'商品名','p_per_ball':'1ボールのP数','balls_per_case':'1甲のボール数',
        'cases_per_pl':'1PLの甲数','ship_unit':'出荷単位','expiry_type':'期限の種別','shelf_life_days':'製造からの期限日数'}


class CatalogUI:
    def build_product_master_tab(self):
        self.master_revision=None;self.master_service_path=None;self.master_edit_code=None
        self.master_tab=ttk.Frame(self.tabs,padding=12);self.tabs.add(self.master_tab,text='商品マスタ')
        self.master_tab=scroll_page(self.master_tab)
        ttk.Label(self.master_tab,text='商品マスタの登録・編集',style='Title.TLabel',font=('Yu Gothic UI',18,'bold')).pack(anchor='w')
        responsive_label(self.master_tab,text='確認済みの換算・期限を登録します。在庫・需要予測の登録は行いません。',style='Muted.TLabel').pack(fill='x',pady=5)
        bar=ttk.Frame(self.master_tab);bar.pack(fill='x',pady=5)
        ttk.Button(bar,text='新規商品',command=self.new_master).pack(side='left',padx=4)
        ttk.Button(bar,text='最新の登録一覧を再表示',command=lambda:self.refresh_product_master(force=True)).pack(side='left',padx=4)
        ttk.Button(bar,text='登録マスタを新規CSVへ出力',command=lambda:self.run(self.export_master)).pack(side='left',padx=4)
        listing=ttk.Frame(self.master_tab);listing.pack(fill='x')
        self.master_tree=ttk.Treeview(listing,columns=('product','name','case','pl','unit','expiry'),show='headings',height=5,selectmode='browse')
        for key,label,width in zip(self.master_tree['columns'],('商品コード','商品名','1甲のP','1PLのP','出荷単位','概算期限マスタ'),(125,360,105,105,100,230)):
            self.master_tree.heading(key,text=label);self.master_tree.column(key,width=width)
        table_scrollbars(self.master_tree);self.master_tree.bind('<<TreeviewSelect>>',self.select_master)
        source=ttk.Frame(self.master_tab);source.pack(fill='x',pady=6)
        ttk.Label(source,text='取込データから転記（未保存）').pack(side='left')
        self.master_source=tk.StringVar();self.master_source_picker=ttk.Combobox(source,textvariable=self.master_source,state='readonly',width=18)
        self.master_source_picker.pack(side='left',padx=4)
        ttk.Button(source,text='選択商品を入力欄へ転記',command=lambda:self.run(self.copy_imported_master)).pack(side='left')
        form=section(self.master_tab,'商品情報・単位換算・概算期限')
        form.columnconfigure(1,weight=1);form.columnconfigure(3,weight=1)
        self.master_values={};self.master_code_entry=None
        for index,key in enumerate(('product','name','p_per_ball','balls_per_case','cases_per_pl','ship_unit','expiry_type','shelf_life_days')):
            row,col=divmod(index,2);var=tk.StringVar();self.master_values[key]=var
            ttk.Label(form,text=LABELS[key]).grid(row=row,column=col*2,sticky='w',padx=5,pady=5)
            if key in ('ship_unit','expiry_type'):
                choices=('PL','甲') if key=='ship_unit' else ('賞味期限','消費期限')
                widget=ttk.Combobox(form,textvariable=var,values=choices,state='readonly',width=20)
            else:widget=ttk.Entry(form,textvariable=var,width=26 if key=='name' else 20)
            widget.grid(row=row,column=col*2+1,sticky='ew',padx=5,pady=5)
            if key=='product':self.master_code_entry=widget
            var.trace_add('write',self.master_form_changed)
        self.master_units=tk.StringVar(value='換算未確認');ttk.Label(self.master_tab,textvariable=self.master_units).pack(anchor='w',pady=5)
        author=ttk.Frame(self.master_tab);author.pack(fill='x',pady=5)
        self.master_operator=tk.StringVar();self.master_reason=tk.StringVar();self.master_ack=tk.BooleanVar(value=False)
        ttk.Label(author,text='確認担当者').pack(side='left');ttk.Entry(author,textvariable=self.master_operator,width=16).pack(side='left',padx=4)
        ttk.Label(author,text='登録・変更理由').pack(side='left');ttk.Entry(author,textvariable=self.master_reason,width=52).pack(side='left',padx=4)
        self.master_operator.trace_add('write',self.master_form_changed);self.master_reason.trace_add('write',self.master_form_changed)
        ttk.Checkbutton(self.master_tab,text='商品コード・名称・換算・出荷単位・期限の値を確認した',variable=self.master_ack).pack(anchor='w')
        self.write_button(self.master_tab,text='確認済み商品マスタを保存',style='Primary.TButton',command=lambda:self.run(self.save_master)).pack(anchor='w',pady=5)
        self.master_status=tk.StringVar();ttk.Label(self.master_tab,textvariable=self.master_status,wraplength=1080).pack(anchor='w')
        ttk.Label(self.master_tab,text='コードは編集で変更しません。別コードは新商品で登録。削除や旧履歴の書換えは行いません。新商品には両倉庫の在庫・予測・補充条件等の整備が必要です。',wraplength=1080).pack(anchor='w',pady=5)
        self.use_registered_master=tk.BooleanVar(value=False)
        mode=ttk.Frame(self.daily);self.legacy_master_mode=mode;mode.pack(fill='x',before=self.tree.master,pady=4)
        ttk.Checkbutton(mode,text='旧v2登録商品マスタを使う（従来の入庫反映・甲換算方式のみ）',variable=self.use_registered_master).pack(anchor='w')
        self.use_registered_master.trace_add('write',self.master_mode_changed)
        self.refresh_product_master(force=True)

    def master_form_changed(self,*_):
        if hasattr(self,'master_ack'):self.master_ack.set(False)
        if not hasattr(self,'master_units'):return
        values=[self.master_values[k].get() for k in ('p_per_ball','balls_per_case','cases_per_pl')]
        if all(v.isascii() and v.isdecimal() and int(v)>0 for v in values):
            p,b,pl=map(int,values);self.master_units.set(f'確認対象の換算: 1甲 = {p*b}P / 1PL = {p*b*pl}P')
        else:self.master_units.set('換算未確認（3つの換算数を確認して入力してください）')

    def master_mode_changed(self,*_):
        if self.use_registered_master.get() and hasattr(self,'use_registered_jan'):self.use_registered_jan.set(False)
        self.invalidate();self.invalidate_diagnostic();self.data=None
        self.info.set('商品マスタの使用元が変わりました。CSVを再取込・再計算してください。')

    def diagnostic_product_master(self):
        if getattr(self,'use_registered_jan',None) is not None and self.use_registered_jan.get():
            from .jan_catalog import master_csvs
            state=self.service.jan_master()
            if not state['products']:raise DataError(['登録JANマスタがありません'])
            return dict(master_csvs=master_csvs(state['products']),jan_master_revision=state['revision'])
        if not self.use_registered_master.get():return {}
        state=self.service.product_master()
        if not state['products']:raise DataError(['登録商品マスタがありません。商品マスタ画面で確認登録してください'])
        return dict(product_csv=catalog_csv(state['products']),product_master_revision=state['revision'])

    def refresh_product_master(self,force=False):
        if not hasattr(self,'master_tree'):return
        self.master_source_picker.configure(values=[p['product'] for p in self.data.tables['products']] if self.data else ())
        self.master_source.set('')
        state=self.service.product_master()
        if not force and self.master_revision==state['revision'] and self.master_service_path==self.service.db_path:return
        if self.master_service_path is not None and self.master_service_path!=self.service.db_path:
            self.use_registered_master.set(False);self.master_operator.set('')
        self.master_revision=state['revision'];self.master_service_path=self.service.db_path;self.master_products=state['products']
        self.master_tree.delete(*self.master_tree.get_children())
        for i,p in enumerate(self.master_products):
            expiry=('賞味期限' if p['expiry_type']=='best_before' else '消費期限')+f' / 製造から{p["shelf_life_days"]}日'
            self.master_tree.insert('','end',iid=str(i),values=(p['product'],p['name'],case_p(p),case_p(p)*p['cases_per_pl'],'PL' if p['ship_unit']=='PL' else '甲',expiry))
        self.new_master();self.master_status.set(f'登録{len(self.master_products)}商品 / マスタ版 {self.master_revision}。再取込時の使用元は日常操作で選択してください。')

    def new_master(self):
        self.master_edit_code=None;self.master_code_entry.configure(state='normal')
        for var in self.master_values.values():var.set('')
        self.master_reason.set('');self.master_ack.set(False)

    def fill_master(self,product):
        self.master_edit_code=product['product'] if any(p['product']==product['product'] for p in self.master_products) else None
        self.master_code_entry.configure(state='readonly' if self.master_edit_code else 'normal')
        for key,var in self.master_values.items():
            value=product.get(key,'')
            if key=='ship_unit':value={'PL':'PL','CASE':'甲'}.get(value,'')
            if key=='expiry_type':value={'best_before':'賞味期限','use_by':'消費期限'}.get(value,'')
            var.set(str(value))
        self.master_reason.set('');self.master_ack.set(False)

    def select_master(self,_event=None):
        selection=self.master_tree.selection()
        if selection:self.fill_master(self.master_products[int(selection[0])])

    def copy_imported_master(self):
        if not self.data or not self.master_source.get():raise DataError(['取込データと転記する商品を選択してください'])
        product=next((p for p in self.data.tables['products'] if p['product']==self.master_source.get()),None)
        if product is None:raise DataError(['転記対象の商品がありません'])
        self.fill_master(product);self.master_status.set('取込データから転記した未保存の値です。全項目と理由を確認して保存してください。')

    def save_master(self):
        values={key:var.get() for key,var in self.master_values.items()}
        if self.master_edit_code and values['product']!=self.master_edit_code:raise DataError(['編集で商品コードは変更できません。新商品として登録してください'])
        for key in ('p_per_ball','balls_per_case','cases_per_pl','shelf_life_days'):
            value=values[key]
            if value.isascii() and value.isdecimal():values[key]=int(value)
        values['ship_unit']={'PL':'PL','甲':'CASE'}.get(values['ship_unit'])
        values['expiry_type']={'賞味期限':'best_before','消費期限':'use_by'}.get(values['expiry_type'])
        try:
            state=self.service.save_product_master(values,self.master_reason.get(),self.master_operator.get(),self.master_ack.get(),
                                                   expected_revision=self.master_revision,create=self.master_edit_code is None)
        finally:self.master_ack.set(False)
        self.invalidate()
        self.invalidate_diagnostic()
        if self.data and self.data.product_master_revision is not None:self.data=None
        self.refresh_product_master(force=True)
        self.master_status.set(f'{values["product"]} を確認保存しました（版{state["revision"]}）。登録マスタを使う場合は日常操作で再取込してください。')
        self.info.set('商品マスタを保存しました。計算と確認を解除しました。登録マスタ利用時は再取込が必要です。')

    def export_master(self):
        path=filedialog.asksaveasfilename(title='登録商品マスタを新規CSVへ保存（上書き不可）',initialfile='products.csv',defaultextension='.csv',confirmoverwrite=False)
        if path:
            self.service.export_product_master(path);self.master_status.set('登録商品マスタを新規出力しました: '+path)
