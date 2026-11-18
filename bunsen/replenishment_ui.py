"""Simplified operational UI; legacy maintenance/history infrastructure is reused."""
from datetime import date
import json
import tkinter as tk
from tkinter import ttk, filedialog

from . import __version__
from .core import DataError, LABELS, units, validate
from .service import write_reports
from .shipping_output import write_documents
from .legacy_ui import LegacyApp
from .delivery_ui import DeliveryUI
from .catalog_ui import CatalogUI
from .jan_catalog_ui import JanCatalogUI
from .requirements_data import active, line_units, NOTICE
from .ui_theme import apply_theme, brand, section, table_scrollbars, text_panel, responsive_label, scroll_page


class App(JanCatalogUI, CatalogUI, DeliveryUI, LegacyApp):
    diagnostic_workflow=2
    def __init__(self, root, data_home=None):
        super().__init__(root,data_home)
        apply_theme(root)
        for parent in (self.daily,self.settings.master):
            for widget in parent.winfo_children():widget.destroy()
        self.write_buttons=[w for w in self.write_buttons if w.winfo_exists()]
        self.tabs.forget(self.outlook_tab)
        self.daily=scroll_page(self.daily)
        self.last_number=None
        self.finalized=False
        self.reason=tk.StringVar()
        self.allow_provisional=tk.BooleanVar(value=False)
        ttk.Label(self.daily,text='今日・明日の補充出荷',style='Title.TLabel',font=('Yu Gothic UI',18,'bold')).pack(anchor='w',pady=(0,3))
        ttk.Label(self.daily,text='01 取り込み    →    02 分析・数量修正    →    03 確認・確定    →    04 出力',style='Muted.TLabel').pack(anchor='w',pady=(0,4))
        bar=section(self.daily,'01  データ取り込み・分析')
        ttk.Label(bar,text='出荷予定日').pack(side='left')
        ttk.Entry(bar,textvariable=self.day,width=14).pack(side='left',padx=5)
        self.write_button(bar,text='1 データ取込',command=lambda:self.run(self.import_data)).pack(side='left',padx=4)
        self.write_button(bar,text='2 分析・補充計算',style='Primary.TButton',command=lambda:self.run(self.compute)).pack(side='left',padx=4)
        ttk.Checkbutton(self.daily,text='暫定・補完予測を参考計算に使用する（正式確定はできません）',variable=self.allow_provisional).pack(anchor='w')
        responsive_label(self.daily,textvariable=self.info,style='Muted.TLabel').pack(fill='x')
        self.result_status=tk.StringVar(value='未計算  /  データを取り込んで分析してください')
        ttk.Label(self.daily,textvariable=self.result_status,style='Status.TLabel').pack(anchor='w',pady=(5,0))
        frame=ttk.Frame(self.daily);frame.pack(fill='x',pady=8)
        cols=('warehouse','product','inventory','demand','safety','required','suggested','quantity','final','units','arrival')
        self.tree=ttk.Treeview(frame,columns=cols,show='headings',height=4,selectmode='browse')
        for col,label,width in zip(cols,('倉庫','商品・品名','現在庫P','対象予測P','安全P最大','必要P','換算後P','修正/指示P','確定P','PL＋甲','到着日'),(115,180,80,85,85,75,80,95,75,125,110)):
            self.tree.heading(col,text=label);self.tree.column(col,width=width)
        table_scrollbars(self.tree);self.tree.bind('<<TreeviewSelect>>',self.selected)
        editor=section(self.daily,'02  選択した商品の数量修正')
        ttk.Label(editor,text='選択商品の指示P').pack(side='left')
        ttk.Entry(editor,textvariable=self.qty,width=10).pack(side='left',padx=4)
        ttk.Label(editor,text='修正理由').pack(side='left')
        ttk.Entry(editor,textvariable=self.reason,width=35).pack(side='left',padx=4)
        self.write_button(editor,text='数量・理由を反映',command=lambda:self.run(self.edit)).pack(side='left',padx=4)
        author=section(self.daily,'03  担当者による確認')
        ttk.Label(author,text='担当者').pack(side='left');ttk.Entry(author,textvariable=self.operator,width=16).pack(side='left',padx=5)
        ttk.Checkbutton(author,text='数量・補充条件・予測区分・修正理由を確認した（実出荷実績とは別）',variable=self.ack).pack(side='left')
        actions=ttk.Frame(self.daily);actions.pack(fill='x',pady=6)
        self.write_button(actions,text='3 参考指示を保存',command=lambda:self.run(lambda:self.issue(False))).pack(side='left',padx=5)
        self.write_button(actions,text='4 判定・正式確定',style='Primary.TButton',command=lambda:self.run(lambda:self.issue(True))).pack(side='left',padx=5)
        ttk.Button(actions,text='5 保存済指示をExcel・PDF出力',command=lambda:self.run(self.export_saved)).pack(side='left',padx=5)
        details=section(self.daily,'計算根拠・不足・未確認事項',expand=True)
        self.detail=tk.Text(details,height=4,wrap='word');text_panel(self.detail);self.detail.pack(fill='both',expand=True)
        self._build_management(scroll_page(self.settings.master))
        self.build_delivery_tab()
        self.build_product_master_tab()
        brand(self)
        self.build_jan_tab()
        self.reason.trace_add('write',lambda *_:self.ack.set(False))
        self.allow_provisional.trace_add('write',lambda *_:self.invalidate())
        self.history();self.update_mode()
        self.info.set('JAN・PL方式はrequirements.csvを含む一式を取り込んでください。入庫・輸送中は対象外。旧v2は従来計算として保持します。')

    def update_mode(self):
        super().update_mode()
        if hasattr(self,'mode_badge'):
            mode=('復元履歴・閲覧専用' if self.service.read_only else 'ローカル保存')
            if self.service.trial_only:mode='架空データ試用・実出荷不可  /  '+mode
            self.mode_badge.configure(text=mode)

    def _build_management(self,parent):
        ttk.Label(parent,text='補充条件の管理。数値は実業務の確認後に変更してください。変更は履歴に保存し、元CSVは上書きしません。',wraplength=1080).pack(anchor='w',pady=5)
        row=ttk.Frame(parent);row.pack(fill='x',pady=5)
        self.setting_key=tk.StringVar();self.setting_picker=ttk.Combobox(row,textvariable=self.setting_key,state='readonly',width=24)
        self.setting_picker.pack(side='left',padx=4);self.setting_picker.bind('<<ComboboxSelected>>',self.select_settings)
        self.setting_values={}
        for key,label in [('cycle_days','サイクル日'),('lead_days','配送日'),('safety_days','安全日数'),('coverage_days','対象日数')]:
            ttk.Label(row,text=label).pack(side='left');var=tk.StringVar();self.setting_values[key]=var;ttk.Entry(row,textvariable=var,width=5).pack(side='left',padx=3)
        row2=ttk.Frame(parent);row2.pack(fill='x',pady=5)
        self.setting_reason=tk.StringVar();self.setting_checked=tk.BooleanVar(value=False)
        ttk.Label(row2,text='変更根拠').pack(side='left');ttk.Entry(row2,textvariable=self.setting_reason,width=40).pack(side='left',padx=4)
        ttk.Checkbutton(row2,text='担当者として値と根拠を確認した',variable=self.setting_checked).pack(side='left')
        self.write_button(row2,text='設定を履歴保存・再分析へ',command=lambda:self.run(self.apply_settings)).pack(side='left',padx=4)
        expiry=ttk.Frame(parent);expiry.pack(fill='x',pady=5)
        self.expiry_product=tk.StringVar();self.expiry_picker=ttk.Combobox(expiry,textvariable=self.expiry_product,state='readonly',width=15)
        self.expiry_picker.pack(side='left');self.expiry_picker.bind('<<ComboboxSelected>>',self.select_expiry)
        self.expiry_type=tk.StringVar();ttk.Combobox(expiry,textvariable=self.expiry_type,values=('賞味期限','消費期限'),state='readonly',width=10).pack(side='left',padx=4)
        ttk.Label(expiry,text='製造からの期限日数').pack(side='left');self.expiry_days=tk.StringVar();ttk.Entry(expiry,textvariable=self.expiry_days,width=7).pack(side='left',padx=4)
        self.write_button(expiry,text='期限マスタを確認保存（変更根拠・担当者必須）',command=lambda:self.run(self.apply_expiry)).pack(side='left',padx=4)
        self.settings=tk.Text(parent,height=7,wrap='word',state='disabled');self.settings.pack(fill='both',expand=True,pady=5)
        ttk.Label(parent,text='履歴は保持します。旧方式の指示は閲覧・従来HTML再出力のみで、新規業務へ引き継ぎません。',wraplength=1080).pack(anchor='w')
        self.history_list=tk.Listbox(parent,height=6);self.history_list.pack(fill='x',pady=5)
        ttk.Button(parent,text='選択履歴の出力（旧版/復元には注意表示）',command=lambda:self.run(self.export_saved)).pack(anchor='w')

    def import_data(self):
        folder=filedialog.askdirectory(title='ムロオ新方式CSV一式を選択')
        if not folder:return
        self.invalidate();self.data=None
        self.info.set('データ未取込（取込中、または不備による停止）。以前のデータでは確定できません。')
        self.settings.configure(state='normal');self.settings.delete('1.0','end');self.settings.configure(state='disabled')
        self.setting_picker.configure(values=());self.setting_key.set('');self.setting_checked.set(False)
        self.expiry_picker.configure(values=());self.expiry_product.set('');self.expiry_type.set('');self.expiry_days.set('')
        self.master_source_picker.configure(values=());self.master_source.set('')
        for var in self.setting_values.values():var.set('')
        data=(self.service.import_folder(folder,workflow=2,use_registered_jan=True) if self.use_registered_jan.get() else
              self.service.import_folder(folder,workflow=2,use_registered_products=True) if self.use_registered_master.get()
              else self.service.import_folder(folder,workflow=2))
        if data.workflow != 2:raise DataError(['旧形式CSVは履歴用です。新方式のreplenishment.csvを含む一式を指定してください'])
        self.data=data;self.folder=folder
        source=(f'登録JAN・PLマスタ版{data.jan_master_revision}' if data.jan_master_revision is not None else
                f'登録商品マスタ版{data.product_master_revision}' if data.product_master_revision is not None else 'CSV商品マスタ')
        self.tree.heading('units',text='物理PL' if active(data) else 'PL＋甲')
        self.info.set(f'{"JAN・物理PL／入庫対象外" if active(data) else "旧v2／確定入庫反映"} 取込済み: {folder} / {source} / データ識別 {data.digest[:16]}。ブンセン在庫は使用しません。')
        self.display_settings();self.show(('JAN・PL方式: '+NOTICE+'\n' if active(data) else '旧v2: 確定入庫を反映する計算です。\n')+'取込完了。分析・補充計算を実行してください。')

    def invalidate(self,keep_draft=False):
        self.invalidate_delivery()
        self.finalized=False
        if hasattr(self,'result_status'):self.result_status.set('未計算  /  データ・条件の変更後は再分析してください')
        super().invalidate(keep_draft=keep_draft)

    def display_settings(self):
        if not hasattr(self,'setting_picker') or self.data is None:return
        self.refresh_product_master()
        self.refresh_jan_master()
        options=[r['warehouse']+' | '+r['product'] for r in self.data.tables['replenishment']]
        self.expiry_picker.configure(values=[r['product'] for r in self.data.tables['products']])
        self.expiry_product.set(self.data.tables['products'][0]['product']);self.select_expiry()
        self.setting_picker.configure(values=options)
        if options:self.setting_key.set(options[0]);self.select_settings()
        self.settings.configure(state='normal');self.settings.delete('1.0','end')
        self.settings.insert('1.0',json.dumps(self.data.tables['replenishment'],ensure_ascii=False,indent=2));self.settings.configure(state='disabled')

    def select_settings(self,_event=None):
        if not self.data:return
        w,p=self.setting_key.get().split(' | ')
        row=next(r for r in self.data.tables['replenishment'] if (r['warehouse'],r['product'])==(w,p))
        for key,var in self.setting_values.items():var.set(str(row[key]))
        self.setting_checked.set(False)

    def apply_settings(self):
        if not self.data or not self.setting_checked.get():raise DataError(['データ取込と設定確認が必要です'])
        values={}
        for key,var in self.setting_values.items():
            value=var.get()
            if not value.isascii() or not value.isdecimal():raise DataError(['設定は0以上の整数で入力してください'])
            values[key]=int(value)
        key=tuple(self.setting_key.get().split(' | '))
        fresh=self.service.update_settings(self.data,key,values,self.setting_reason.get(),self.operator.get())
        self.invalidate();self.data=fresh;self.display_settings();self.info.set('設定変更を履歴保存しました。全明細を再分析・再確認してください。')

    def compute(self):
        if self.data is None or self.data.workflow != 2:raise DataError(['新方式データの取込が必要です'])
        if not self.finalized:self.ensure_no_pending_edit()
        self.invalidate()
        self.dispatch=date.fromisoformat(self.day.get());self.expected_revision=self.service.latest_revision(self.dispatch)
        self.lines=self.service.calculate(self.data,self.dispatch,allow_provisional=self.allow_provisional.get());self.refresh()

    def select_expiry(self,_event=None):
        if self.data is None:return
        row=next(r for r in self.data.tables['products'] if r['product']==self.expiry_product.get())
        self.expiry_type.set({'best_before':'賞味期限','use_by':'消費期限'}.get(row.get('expiry_type'),''))
        self.expiry_days.set(str(row.get('shelf_life_days','')))

    def apply_expiry(self):
        if self.data is None or not self.setting_checked.get():raise DataError(['データ取込と管理設定の確認チェックが必要です'])
        text=self.expiry_days.get()
        if not text.isascii() or not text.isdecimal():raise DataError(['期限日数は正整数です'])
        self.data=self.service.update_product_expiry(self.data,self.expiry_product.get(),{'賞味期限':'best_before','消費期限':'use_by'}.get(self.expiry_type.get()),int(text),self.setting_reason.get(),self.operator.get())
        self.invalidate();self.display_settings();self.setting_checked.set(False)
        self.info.set('概算期限マスタを履歴保存しました。計画を再計算してください。入力CSVは変更していません。')

    def refresh(self):
        self.tree.delete(*self.tree.get_children())
        products={r['product']:r for r in self.data.tables['products']}
        for i,r in enumerate(self.lines):
            self.tree.insert('','end',iid=str(i),tags=('attention' if r['warning'] or r['quantity_p']<r['required_p'] else ('alternate' if i%2 else ''),),values=(LABELS[r['warehouse']],r['product']+' '+products[r['product']]['name'],r['inventory_p'],r['demand_p'],r['safety_p'],r['required_p'],r['suggested_p'],r['quantity_p'],r['quantity_p'] if self.finalized else '未確定',line_units(r,products[r['product']]),r['arrival']))
        errors=validate(self.data,self.lines,self.dispatch,self.service.clock())
        state='正式確定済み' if self.finalized else '未確定'
        warnings=sum(bool(r['warning']) or r['quantity_p']<r['required_p'] for r in self.lines)
        self.result_status.set(f'{state}  /  {len(self.lines)}明細  /  検証不備 {len(errors)}件  /  注意明細 {warnings}件（根拠欄を確認）')
        evidence=[]
        for row in self.lines:
            if row.get('source_pdf'):evidence.append(f'在庫PDF {row["source_pdf"]} / SHA-256 {row["source_sha256"]} / 転記確認者 {row["inventory_checker"]}')
            evidence.append(f'{LABELS[row["warehouse"]]} {row["product"]}: 在庫基準 {row["inventory_as_of"]} ({row["inventory_basis"]}) / 入庫網羅 {row['transit_checked_at'] if row['transit_checked_at'] is not None else '対象外'} / 予測基準 {row["forecast_as_of"]}\n{row["warning"]}')
            evidence.extend(f'{r["date"]}: 始業予測 {r["opening_p"]}P + 確定入庫 {str(r['transit_p'])+'P' if r['transit_p'] is not None else '対象外'} - 需要予測 {r["forecast_p"]}P = 補充前予測 {r["projected_closing_p"]}P / 安全在庫 {r["safety_p"]}P' for r in row['timeline'])
            if row['quantity_p'] < row['required_p']:evidence.append('修正後も安全在庫未達のリスクがあります。理由と数量を確認してください。')
        self.show('\n'.join([NOTICE if active(self.data) else '必要補充の指示であり実出荷実績ではありません。']+evidence+['確定前検証']+(errors or ['入力検証に不備なし。担当者の確認操作が必要です。'])))

    def selected(self,_event=None):
        if self.lines and self.tree.selection():
            row=self.lines[int(self.tree.selection()[0])];self.qty.set(str(row['quantity_p']));self.reason.set(row['edit_reason'])

    def edit(self):
        if self.finalized:raise DataError(['確定済みです。修正は再分析から新しい改訂として実施してください'])
        if not self.lines or not self.tree.selection():raise DataError(['修正する明細を選択してください'])
        if not self.qty.get().isascii() or not self.qty.get().isdecimal():raise DataError(['数量は0以上の整数です'])
        row=self.lines[int(self.tree.selection()[0])]
        self.service.edit_quantity(self.data,self.lines,(row['warehouse'],row['product']),int(self.qty.get()),self.reason.get(),self.operator.get())
        self.ack.set(False);self.refresh()

    def ensure_no_pending_edit(self):
        if self.lines and self.tree.selection():
            row=self.lines[int(self.tree.selection()[0])]
            if self.qty.get()!=str(row['quantity_p']) or self.reason.get()!=row['edit_reason']:raise DataError(['数量と修正理由を反映してください'])

    def issue(self,formal):
        if self.finalized:raise DataError(['確定済みです。新しい指示は再分析・再確認してください'])
        if not self.lines:raise DataError(['再分析が必要です'])
        self.ensure_no_pending_edit()
        payload=self.service.issue(self.data,self.lines,self.dispatch,self.operator.get(),formal,self.ack.get(),expected_revision=self.expected_revision)
        self.last_number=payload['number'];self.ack.set(False)
        if formal:
            self.lines=payload['lines'];self.finalized=True;self.expected_revision=None;self.refresh()
        self.history();self.info.set(f'{payload["number"]} を保存しました。出力ボタンで倉庫別Excel・PDFを作成できます。')

    def export_saved(self):
        selection=self.history_list.curselection()
        number=self.records[selection[0]][0] if selection else self.last_number
        if not number:raise DataError(['保存済み指示を履歴から選択してください'])
        folder=filedialog.askdirectory(title='帳票の保存先（既存帳票は上書きしません）')
        if not folder:return
        payload=self.service.report_payload(number)
        paths=write_documents(payload,folder) if payload.get('workflow')==2 else write_reports(payload,folder)
        kind='Excel/PDF/HTML' if payload.get('workflow')==2 else '旧方式の履歴HTML（新方式へ読み替えません）'
        self.info.set(f'{number}: {kind} {len(paths)}ファイル出力。保存先 {folder}。必要補充指示であり実出荷実績ではありません。')

    def show_outlook(self):
        raise DataError(['新方式の分析結果と日別根拠を使用してください。旧14日参考見通しは旧形式用です'])

    def history(self):
        super().history()
        self.refresh_delivery_history()
        self.refresh_product_master()
        self.refresh_jan_master()

    def save_mapping_template(self):
        from .diagnostics import export_template
        path=filedialog.asksaveasfilename(title='新方式の実CSV項目対応表',initialfile='csv-mapping-review.json',defaultextension='.json',confirmoverwrite=False)
        if path:export_template(path,self.diagnostic_folder.get() or None,workflow=2)
