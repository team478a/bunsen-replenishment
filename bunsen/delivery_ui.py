"""Small operational tab for saved delivery plans and reference expiry."""
import json
import tkinter as tk
from datetime import date
from tkinter import ttk, filedialog

from .core import DataError, LABELS, units
from .delivery import NOTICE
from .requirements_data import line_units, PROFILE
from .delivery_output import export_plan
from .ui_theme import section, table_scrollbars, text_panel, responsive_label, scroll_page


class DeliveryUI:
    def build_delivery_tab(self):
        self.delivery_report=None
        self.delivery_tab=ttk.Frame(self.tabs,padding=12);self.tabs.add(self.delivery_tab,text='14日配送計画')
        self.delivery_tab=scroll_page(self.delivery_tab)
        ttk.Label(self.delivery_tab,text='14日間の配送予定と概算期限',style='Title.TLabel',font=('Yu Gothic UI',18,'bold')).pack(anchor='w')
        responsive_label(self.delivery_tab,text=NOTICE,style='Muted.TLabel').pack(fill='x',pady=5)
        bar=section(self.delivery_tab,'01  計画の作成')
        ttk.Label(bar,text='計画開始日（日常操作の出荷予定日と共通）').pack(side='left')
        ttk.Entry(bar,textvariable=self.day,width=13).pack(side='left',padx=4)
        self.write_button(bar,text='14日計画を計算・保存',style='Primary.TButton',command=lambda:self.run(self.compute_delivery)).pack(side='left',padx=4)
        self.delivery_ack=tk.BooleanVar(value=False)
        row=section(self.delivery_tab,'02  確認・正式確定・出力')
        ttk.Label(row,text='確認担当者（日常操作と共通）').pack(side='left');ttk.Entry(row,textvariable=self.operator,width=16).pack(side='left')
        self.write_button(row,text='正式配送予定を確定',style='Primary.TButton',command=lambda:self.run(lambda:self.confirmed_action(self.delivery_ack,self.confirm_delivery))).pack(side='left',padx=5)
        ttk.Button(row,text='選択計画をExcel・PDF出力',command=lambda:self.run(self.export_delivery)).pack(side='left',padx=5)
        cols=('trip','warehouse','dispatch','arrival','product','quantity','pallets','expiry','remaining','units')
        listing=ttk.Frame(self.delivery_tab);listing.pack(fill='x',pady=8)
        self.delivery_tree=ttk.Treeview(listing,columns=cols,show='headings',height=6)
        for c,label,width in zip(cols,('便','倉庫','出荷日','入荷日','商品','数量P','PL枠','概算期限','入荷残日','PL＋甲'),(65,110,105,105,150,75,60,105,75,120)):
            self.delivery_tree.heading(c,text=label);self.delivery_tree.column(c,width=width)
        table_scrollbars(self.delivery_tree)
        details=section(self.delivery_tab,'計画の状態・概算期限リスク・計算根拠',expand=True)
        self.delivery_info=tk.Text(details,height=6,wrap='word');text_panel(self.delivery_info);self.delivery_info.pack(fill='both',expand=True)
        row=ttk.Frame(self.delivery_tab);row.pack(fill='x')
        self.delivery_history=tk.StringVar();self.delivery_picker=ttk.Combobox(row,textvariable=self.delivery_history,state='readonly',width=90)
        self.delivery_picker.pack(side='left',fill='x',expand=True);ttk.Button(row,text='保存計画を表示',command=lambda:self.run(self.load_delivery)).pack(side='left',padx=5)
        self.operator.trace_add('write',lambda *_:self.delivery_ack.set(False))
        self.refresh_delivery_history()

    def invalidate_delivery(self):
        if not hasattr(self,'delivery_ack'):return
        self.delivery_report=None;self.delivery_ack.set(False)
        self.delivery_tree.delete(*self.delivery_tree.get_children())
        self.delivery_info.delete('1.0','end');self.delivery_info.insert('1.0','データ・条件が変更されました。再計算または保存原本の履歴を選択してください。')

    def refresh_delivery_history(self):
        if hasattr(self,'delivery_picker'):
            plans=self.service.delivery_plans()
            self.delivery_picker.configure(values=[r['plan_id']+' | '+r['calculated_at'] for r in plans])
            self.delivery_history.set('')

    def compute_delivery(self):
        self.invalidate_delivery()
        if self.data is None:raise DataError(['日常操作でムロオCSVを取り込んでください'])
        self.delivery_report=self.service.calculate_delivery_plan(self.data,date.fromisoformat(self.day.get()),allow_provisional=self.allow_provisional.get())
        self.refresh_delivery_history();self.show_delivery()

    def show_delivery(self):
        r=self.delivery_report;self.delivery_ack.set(False);self.delivery_tree.delete(*self.delivery_tree.get_children())
        products={p['product']:p for p in r['products']}
        for trip in r['trips']:
            for item in trip['items']:
                self.delivery_tree.insert('','end',values=(trip['trip_id'],LABELS[trip['warehouse']],item['dispatch'],item['arrival'],item['product']+' '+item['name'],item['quantity_p'],item['pallets'],item['estimated_expiry'],item['remaining_arrival_days'],line_units(item,products[item['product']])))
        text=[('正式配送予定' if r['formal'] else '未確定・参考計画')+' / '+r['plan_id'],
              '計算実行日時 '+r['calculated_at']+' / 固定した仮製造日 '+r['assumed_manufacture_date'],NOTICE,
              ('16物理PL以下・1PL1JAN・PL未満出荷なし。重量・容積・休日・大域最適性は未確認。' if r.get('profile')==PROFILE else '16PL枠以下・1PL1商品。甲端数も1枠使用。重量・容積・休日・大域最適性は未確認。')]
        text+=r['warnings'] or ['参考警告なし（実期限の適合確認ではありません）']
        if r.get('product_master_revision') is not None:text.insert(2,f'登録商品マスタ版 {r["product_master_revision"]}（計算時の保存原本）')
        text += [f'{v["warehouse"]} {v["product"]}: 在庫基準 {v["as_of"]} ({v["basis"]})' for v in r['inventory_basis']]
        text+=['日別予測・安全在庫・計画入庫は出力JSON/Excelで確認できます。',json.dumps(r['expiry_risks'],ensure_ascii=False)]
        if r.get('trial_only'):text.insert(0,'架空試用・実出荷不可')
        if r.get('restored_view'):text.insert(0,'復元履歴・閲覧専用')
        self.delivery_info.delete('1.0','end');self.delivery_info.insert('1.0','\n'.join(text))

    def confirm_delivery(self):
        if not self.delivery_report:raise DataError(['配送計画を計算または履歴から選択してください'])
        try:
            self.delivery_report=self.service.confirm_delivery_plan(self.delivery_report['plan_id'],self.operator.get(),self.delivery_ack.get())
        finally:
            self.delivery_ack.set(False)
        self.show_delivery()

    def load_delivery(self):
        if not self.delivery_history.get():raise DataError(['保存配送計画を選択してください'])
        self.delivery_report=self.service.delivery_plan(self.delivery_history.get().split(' | ')[0]);self.show_delivery()

    def export_delivery(self):
        if not self.delivery_report:raise DataError(['保存配送計画を選択してください'])
        report=self.service.delivery_plan(self.delivery_report['plan_id'])
        folder=filedialog.askdirectory(title='配送計画の新規出力先')
        if folder:export_plan(report,folder)
