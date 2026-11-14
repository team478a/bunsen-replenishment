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


class App(LegacyApp):
    diagnostic_workflow=2
    def __init__(self, root, data_home=None):
        super().__init__(root,data_home)
        for parent in (self.daily,self.settings.master):
            for widget in parent.winfo_children():widget.destroy()
        self.write_buttons=[w for w in self.write_buttons if w.winfo_exists()]
        self.tabs.forget(self.outlook_tab)
        self.last_number=None
        self.finalized=False
        self.reason=tk.StringVar()
        self.allow_provisional=tk.BooleanVar(value=False)
        ttk.Label(self.daily,text='取り込み → 分析 → 結果 → 判定・確定 → 出力',font=('Yu Gothic UI',18,'bold')).pack(anchor='w',pady=8)
        ttk.Label(self.daily,text='ムロオ必要補充方式。ブンセン在庫・朝照合は不要。必要数量の指示と実出荷実績は別です。',wraplength=1080).pack(anchor='w')
        bar=ttk.Frame(self.daily);bar.pack(fill='x',pady=8)
        ttk.Label(bar,text='出荷予定日').pack(side='left')
        ttk.Entry(bar,textvariable=self.day,width=14).pack(side='left',padx=5)
        self.write_button(bar,text='1 データ取込',command=lambda:self.run(self.import_data)).pack(side='left',padx=4)
        self.write_button(bar,text='2 分析・補充計算',command=lambda:self.run(self.compute)).pack(side='left',padx=4)
        ttk.Checkbutton(bar,text='暫定・補完予測を参考計算に使用（正式不可）',variable=self.allow_provisional).pack(side='left',padx=6)
        ttk.Label(self.daily,textvariable=self.info,wraplength=1080).pack(anchor='w')
        frame=ttk.Frame(self.daily);frame.pack(fill='x',pady=8)
        cols=('warehouse','product','inventory','demand','safety','required','suggested','quantity','final','units','arrival')
        self.tree=ttk.Treeview(frame,columns=cols,show='headings',height=5,selectmode='browse')
        for col,label,width in zip(cols,('倉庫','商品・品名','現在庫P','対象予測P','安全P最大','必要P','換算後P','修正/指示P','確定P','PL＋甲','到着日'),(115,180,80,85,85,75,80,95,75,125,110)):
            self.tree.heading(col,text=label);self.tree.column(col,width=width)
        self.tree.pack(fill='x');self.tree.bind('<<TreeviewSelect>>',self.selected)
        editor=ttk.Frame(self.daily);editor.pack(fill='x',pady=6)
        ttk.Label(editor,text='選択商品の指示P').pack(side='left')
        ttk.Entry(editor,textvariable=self.qty,width=10).pack(side='left',padx=4)
        ttk.Label(editor,text='修正理由').pack(side='left')
        ttk.Entry(editor,textvariable=self.reason,width=35).pack(side='left',padx=4)
        self.write_button(editor,text='数量・理由を反映',command=lambda:self.run(self.edit)).pack(side='left',padx=4)
        author=ttk.Frame(self.daily);author.pack(fill='x',pady=6)
        ttk.Label(author,text='担当者').pack(side='left');ttk.Entry(author,textvariable=self.operator,width=16).pack(side='left',padx=5)
        ttk.Checkbutton(author,text='数量・補充条件・予測区分・修正理由を確認した（実出荷の登録ではありません）',variable=self.ack).pack(side='left')
        actions=ttk.Frame(self.daily);actions.pack(fill='x',pady=6)
        self.write_button(actions,text='3 参考指示を保存',command=lambda:self.run(lambda:self.issue(False))).pack(side='left',padx=5)
        self.write_button(actions,text='4 判定・正式確定',command=lambda:self.run(lambda:self.issue(True))).pack(side='left',padx=5)
        ttk.Button(actions,text='5 保存済指示をExcel・PDF出力',command=lambda:self.run(self.export_saved)).pack(side='left',padx=5)
        self.detail=tk.Text(self.daily,height=10,wrap='word');self.detail.pack(fill='both',expand=True,pady=8)
        self._build_management(self.settings.master if self.settings.winfo_exists() else self.tabs.nametowidget(self.tabs.tabs()[1]))
        self.reason.trace_add('write',lambda *_:self.ack.set(False))
        self.allow_provisional.trace_add('write',lambda *_:self.invalidate())
        self.history();self.update_mode()
        self.info.set('新方式のCSV一式（replenishment.csvを含む）を取り込んでください。サンプル値は業務承認済み設定ではありません。')

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
        for var in self.setting_values.values():var.set('')
        data=self.service.import_folder(folder,workflow=2)
        if data.workflow != 2:raise DataError(['旧形式CSVは履歴用です。新方式のreplenishment.csvを含む一式を指定してください'])
        self.data=data;self.folder=folder
        self.info.set(f'取込済み: {folder} / データ識別 {data.digest[:16]}。ブンセン在庫は使用しません。')
        self.display_settings();self.show('取込完了。分析・補充計算を実行してください。')

    def invalidate(self,keep_draft=False):
        self.finalized=False
        super().invalidate(keep_draft=keep_draft)

    def display_settings(self):
        if not hasattr(self,'setting_picker') or self.data is None:return
        options=[r['warehouse']+' | '+r['product'] for r in self.data.tables['replenishment']]
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

    def refresh(self):
        self.tree.delete(*self.tree.get_children())
        products={r['product']:r for r in self.data.tables['products']}
        for i,r in enumerate(self.lines):
            self.tree.insert('','end',iid=str(i),values=(LABELS[r['warehouse']],r['product']+' '+products[r['product']]['name'],r['inventory_p'],r['demand_p'],r['safety_p'],r['required_p'],r['suggested_p'],r['quantity_p'],r['quantity_p'] if self.finalized else '未確定',units(r['quantity_p'],products[r['product']]),r['arrival']))
        errors=validate(self.data,self.lines,self.dispatch,self.service.clock())
        evidence=[]
        for row in self.lines:
            evidence.append(f'{LABELS[row["warehouse"]]} {row["product"]}: 在庫基準 {row["inventory_as_of"]} ({row["inventory_basis"]}) / 入庫網羅 {row["transit_checked_at"]} / 予測基準 {row["forecast_as_of"]}\n{row["warning"]}')
            evidence.extend(f'{r["date"]}: 始業予測 {r["opening_p"]}P + 確定入庫 {r["transit_p"]}P - 需要予測 {r["forecast_p"]}P = 補充前予測 {r["projected_closing_p"]}P / 安全在庫 {r["safety_p"]}P' for r in row['timeline'])
            if row['quantity_p'] < row['required_p']:evidence.append('修正後も安全在庫未達のリスクがあります。理由と数量を確認してください。')
        self.show('\n'.join(['必要補充の指示であり実出荷実績ではありません。']+evidence+['確定前検証']+(errors or ['入力検証に不備なし。担当者の確認操作が必要です。'])))

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

    def save_mapping_template(self):
        from .diagnostics import export_template
        path=filedialog.asksaveasfilename(title='新方式の実CSV項目対応表',initialfile='csv-mapping-review.json',defaultextension='.json',confirmoverwrite=False)
        if path:export_template(path,self.diagnostic_folder.get() or None,workflow=2)
