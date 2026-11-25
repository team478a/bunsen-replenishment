"""Optional product exceptions, separated from daily replenishment work."""
import tkinter as tk
from tkinter import ttk
from .core import DataError,WAREHOUSES,LABELS
from .product_safety import known_products
from .ui_theme import section,responsive_label

def build_product_safety(app,parent):
    frame=section(parent,'商品別の安全在庫（必要な商品だけ設定）')
    responsive_label(frame,text='空欄はその倉庫の共通日数を使います。0は安全在庫なしの明示設定です。配送日数・補充間隔は倉庫別条件を使います。').pack(fill='x',pady=4)
    ttk.Label(frame,text='対象商品（JAN・品名）').pack(anchor='w')
    app.safety_product=tk.StringVar();app.safety_picker=ttk.Combobox(frame,textvariable=app.safety_product,state='readonly',width=60)
    app.safety_picker.pack(anchor='w',pady=5);app.safety_picker.bind('<<ComboboxSelected>>',lambda _:app.select_product_safety())
    line=ttk.Frame(frame);line.pack(fill='x');app.safety_days={}
    for w in WAREHOUSES:
        ttk.Label(line,text=LABELS[w]+' 安全在庫日数').pack(side='left',padx=3)
        var=tk.StringVar();app.safety_days[w]=var;entry=ttk.Entry(line,textvariable=var,width=8);entry.pack(side='left',padx=4);app.setting_entries.append(entry)
    app.safety_info=tk.StringVar();responsive_label(frame,textvariable=app.safety_info,style='Muted.TLabel').pack(fill='x',pady=4)
    app.safety_reason=tk.StringVar();ttk.Label(frame,text='設定・変更理由').pack(anchor='w')
    entry=ttk.Entry(frame,textvariable=app.safety_reason);entry.pack(fill='x',pady=3);app.setting_entries.append(entry)
    line=ttk.Frame(frame);line.pack(fill='x',pady=5)
    ttk.Label(line,text='担当者').pack(side='left');ttk.Entry(line,textvariable=app.operator,width=18).pack(side='left',padx=5)
    app.safety_save=app.write_button(line,text='商品別の安全在庫を保存',command=lambda:app.run(app.save_product_safety));app.safety_save.pack(side='left',padx=5)
    app.safety_reset=app.write_button(line,text='この商品は共通日数へ戻す',command=lambda:app.run(lambda:app.save_product_safety(reset=True)));app.safety_reset.pack(side='left',padx=5)
    responsive_label(frame,text='保存後は在庫・予測を再取込して再計算してください。変更前の指示や履歴の安全在庫は変えません。').pack(fill='x',pady=4)

class ProductSafetyUI:
    def refresh_product_safety(self):
        if not hasattr(self,'safety_picker'):return
        self.safety_products=known_products(self.service,self.data)
        values=[p+' '+name for p,name in sorted(self.safety_products.items())]
        self.safety_picker.configure(values=values);self.safety_revision=self.service.product_safety()['revision']
        if self.safety_product.get() not in values:self.safety_product.set('')
        self.safety_save.configure(state='disabled' if self.service.read_only else 'normal');self.safety_reset.configure(state='disabled' if self.service.read_only else 'normal')
        self.select_product_safety()
        if not values:self.safety_info.set('商品がありません。JAN・倉庫別PLマスタを登録するか、在庫・予測データを取り込んでください。')

    def select_product_safety(self):
        product=self.safety_product.get().split(' ',1)[0];state=self.service.product_safety();self.safety_revision=state['revision']
        for w,var in self.safety_days.items():var.set(str(state['values'][w][product]) if product in state['values'][w] else '')
        from .common_settings import warehouse_specific
        common=self.service.common_settings()['values']
        self.safety_info.set(' / '.join(LABELS[w]+' 共通 '+(str(common[w]['safety_days'])+'日' if warehouse_specific(common) else '未設定') for w in WAREHOUSES)+f" / 商品別設定版 {state['revision']}")
        self.safety_reason.set('')

    def save_product_safety(self,reset=False):
        product=self.safety_product.get().split(' ',1)[0]
        if product not in self.safety_products:raise DataError(['安全在庫を設定する商品を選択してください'])
        days={}
        for w,var in self.safety_days.items():
            text=var.get()
            if reset or not text:days[w]=None
            elif text.isascii() and text.isdecimal():days[w]=int(text)
            else:raise DataError([LABELS[w]+': 安全在庫は0以上の整数日数です。共通を使う場合は空欄にしてください'])
        self.service.save_product_safety(product,days,self.operator.get(),self.safety_reason.get(),True,self.safety_revision)
        self.invalidate();self.data=None;self.refresh_product_safety()
        self.info.set('商品別安全在庫を保存しました。在庫・予測を再取込して再計算してください。');self.safety_info.set(self.info.get())
