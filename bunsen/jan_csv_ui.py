"""Review a master CSV, then atomically merge confirmed rows."""
import tkinter as tk
from tkinter import ttk,filedialog
from pathlib import Path
from .core import DataError
from .jan_csv import read_csv,export_csv
from .ui_theme import responsive_label,table_scrollbars,scroll_page

class JanCSVDialog(tk.Toplevel):
    def __init__(self,app):
        super().__init__(app.root);self.app=app;self.report=None
        self.title('JAN・倉庫別PLマスタ CSV取込');self.geometry('1050x700');self.transient(app.root)
        state=app.service.jan_master();self.revision=state['revision'];self.existing={p['product']:p for p in state['all_products']};self.active_ids={p['product'] for p in state['products']}
        body=scroll_page(ttk.Frame(self));body.master.master.pack(fill='both',expand=True)
        responsive_label(body,text='全角英数の品名をそのまま登録できます。CSVの全行を確認して保存します。同じJANは上書き更新します。追加・更新モードは既存対象を保持し、置換モードはCSVにない商品を補充対象外にします。不備が1行でもあれば登録しません。').pack(fill='x',padx=12,pady=12)
        bar=ttk.Frame(body);bar.pack(fill='x',padx=12)
        ttk.Button(bar,text='CSVを選択',command=lambda:app.run(self.choose)).pack(side='left',padx=4)
        ttk.Button(bar,text='CSVひな形を保存',command=lambda:app.run(lambda:self.export(False))).pack(side='left',padx=4)
        ttk.Button(bar,text='登録済みを同じCSV形式で出力',command=lambda:app.run(lambda:self.export(True))).pack(side='left',padx=4)
        mode_row=ttk.Frame(body);mode_row.pack(fill='x',padx=12,pady=8)
        self.mode=tk.StringVar(value='merge')
        ttk.Radiobutton(mode_row,text='追加・更新（既存の対象を保持）',variable=self.mode,value='merge').pack(side='left',padx=4)
        ttk.Radiobutton(mode_row,text='CSVの一覧で置換（CSVにないJANは対象外）',variable=self.mode,value='replace').pack(side='left',padx=4)
        self.mode.trace_add('write',lambda *_:self.render_preview() if self.report is not None else None)
        self.status=tk.StringVar(value='未取込。UTF-8 / CP932、ひな形の6列を使用してください。')
        responsive_label(body,textvariable=self.status).pack(fill='x',padx=12,pady=8)
        listing=ttk.Frame(body);listing.pack(fill='x',padx=12)
        self.tree=ttk.Treeview(listing,columns=('jan','name','expiry','days','kobe','sugito','action'),show='headings',height=9)
        for key,label,width in zip(self.tree['columns'],('JAN','品名','期限種別','期限日数','神戸P/PL','杉戸P/PL','処理'),(160,280,100,85,100,100,80)):
            self.tree.heading(key,text=label);self.tree.column(key,width=width)
        table_scrollbars(self.tree)
        self.errors=tk.Text(body,height=7,wrap='word',state='disabled');self.errors.pack(fill='x',padx=12,pady=8)
        row=ttk.Frame(body);row.pack(fill='x',padx=12,pady=8)
        ttk.Label(row,text='担当者').pack(side='left');ttk.Entry(row,textvariable=app.operator,width=16).pack(side='left',padx=5)
        ttk.Label(row,text='登録・変更根拠').pack(side='left');self.reason=tk.StringVar();ttk.Entry(row,textvariable=self.reason,width=35).pack(side='left',padx=5)
        self.save_button=ttk.Button(body,text='内容を確認して一括登録',style='Primary.TButton',state='disabled',command=lambda:app.run(self.save));self.save_button.pack(anchor='w',padx=12,pady=8)
        self.grab_set()
    def choose(self):
        path=filedialog.askopenfilename(parent=self,title='JANマスタCSV',filetypes=[('CSV','*.csv')])
        if path:self.load(path)
    def load(self,path):
        self.report=None;self.save_button.configure(state='disabled');self.tree.delete(*self.tree.get_children());self.status.set('読取中…')
        state=self.app.service.jan_master();self.revision=state['revision'];self.existing={p['product']:p for p in state['all_products']};self.active_ids={p['product'] for p in state['products']}
        self.report=read_csv(path);self.render_preview()

    def render_preview(self):
        r=self.report;self.tree.delete(*self.tree.get_children())
        self.save_button.configure(state='disabled')
        for p in r['products']:
            self.tree.insert('','end',values=(p['product'],p['name'],{'best_before':'賞味期限','use_by':'消費期限'}[p['expiry_type']],p['shelf_life_days'],p['kobe_p_per_pl'],p['sugito_p_per_pl'],'更新' if p['product'] in self.existing else '新規'))
        new=sum(p['product'] not in self.existing for p in r['products'])
        omitted=sorted(self.active_ids-{p['product'] for p in r['products']}) if self.mode.get()=='replace' else []
        self.status.set(f"{r['encoding']} / 新規{new}件 / 更新{len(r['products'])-new}件 / 対象外へ変更{len(omitted)}件 / 不備{len(r['errors'])}件")
        self.errors.configure(state='normal');self.errors.delete('1.0','end');self.errors.insert('1.0','\n'.join(r['errors']) if r['errors'] else '不備なし。CSVに含まれるJANは補充対象になります。登録情報・旧履歴は保持します。'+('\n補充対象外へ変更するJAN:\n'+'\n'.join(p+' '+self.existing[p]['name'] for p in omitted) if omitted else ''));self.errors.configure(state='disabled')
        self.save_button.configure(text='CSVの一覧で補充対象を置換' if self.mode.get()=='replace' else '内容を確認して追加・更新')
        if not r['errors'] and not self.app.service.read_only:self.save_button.configure(state='normal')
    def export(self,existing):
        products=self.app.service.jan_master()['products'] if existing else []
        path=filedialog.asksaveasfilename(parent=self,title='JANマスタCSVの新規保存先',defaultextension='.csv',filetypes=[('CSV','*.csv')])
        if path:
            with Path(path).open('x',encoding='utf-8-sig',newline='') as f:f.write(export_csv(products))
    def save(self):
        if self.report is None:raise DataError(['CSVを選んでください'])
        self.app.service.import_jan_csv(self.report,self.app.operator.get(),self.reason.get(),True,self.revision,mode=self.mode.get())
        self.app.invalidate();self.app.invalidate_diagnostic();self.app.data=None;self.app.refresh_jan_master(True)
        self.app.info.set('JANマスタCSVを一括登録しました。在庫・予測・補充条件を再取込・再計算してください。')
        self.destroy()
