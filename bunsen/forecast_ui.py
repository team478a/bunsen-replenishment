"""One review action, with unknown warehouse mappings left unset."""
import tkinter as tk
from tkinter import ttk, filedialog
from .core import DataError, LABELS
from .forecast_csv import read_forecast, validate_mapping, QUANTITY
from .ui_theme import scroll_page, responsive_label, table_scrollbars, text_panel


class ForecastDialog(tk.Toplevel):
    def __init__(self,app):
        super().__init__(app.root);self.app=app;self.service=app.service
        self.report=None;self.mapping_vars={};self.master_revision=self.service.jan_master()['revision']
        previous=self.service.forecast_mapping();self.mapping_revision=previous['revision'];self.previous_mapping=previous['mapping']
        self.title('予測CSVの取込・確認');self.geometry('1050x710');self.minsize(850,600);self.transient(app.root)
        page=ttk.Frame(self,padding=12);page.pack(fill='both',expand=True);body=scroll_page(page)
        responsive_label(body,text='「'+QUANTITY+'」だけを使用します。大口・特売・製造・入庫・予測在庫は加算しません。小数Pは保持し、補充量の最終PL換算で切り上げます。').pack(fill='x',pady=5)
        ttk.Button(body,text='予測CSVを選択',command=lambda:app.run(self.choose)).pack(anchor='w',pady=5)
        self.status=tk.StringVar(value='未取込。倉庫コードは確認済みの対応を指定するまで使用できません。')
        responsive_label(body,textvariable=self.status).pack(fill='x',pady=5)
        self.mapping_frame=ttk.LabelFrame(body,text='倉庫コードの対応（業務確認済みの内容を指定）',padding=8);self.mapping_frame.pack(fill='x',pady=5)
        listing=ttk.Frame(body);listing.pack(fill='x',pady=6)
        self.tree=ttk.Treeview(listing,columns=('source','jan','day','quantity','kind'),show='headings',height=7)
        for key,label,width in zip(self.tree['columns'],('元倉庫コード','JAN','予測日','需要予測 P（小数保持）','予測区分'),(110,180,130,300,170)):
            self.tree.heading(key,text=label);self.tree.column(key,width=width)
        table_scrollbars(self.tree)
        self.detail=tk.Text(body,height=5,wrap='word',state='disabled');text_panel(self.detail);self.detail.pack(fill='x',pady=6)
        actions=ttk.Frame(body);actions.pack(fill='x',pady=8)
        ttk.Label(actions,text='担当者').pack(side='left');ttk.Entry(actions,textvariable=app.operator,width=18).pack(side='left',padx=5)
        self.save_button=ttk.Button(actions,text='この予測・倉庫対応を使用して保存',style='Primary.TButton',command=lambda:app.run(self.save),state='disabled');self.save_button.pack(side='left',padx=5)
        self.grab_set()

    def show_details(self,text):
        self.detail.configure(state='normal');self.detail.delete('1.0','end');self.detail.insert('1.0',text);self.detail.configure(state='disabled')

    def choose(self):
        path=filedialog.askopenfilename(parent=self,title='予測結果CSV',filetypes=[('CSV','*.csv')])
        if path:self.load(path)

    def load(self,path):
        self.report=None;self.save_button.configure(state='disabled');self.tree.delete(*self.tree.get_children())
        for widget in self.mapping_frame.winfo_children():widget.destroy()
        self.mapping_vars={};self.status.set('読取中…');self.show_details('')
        try:report=read_forecast(path)
        except Exception:self.status.set('読取できませんでした。CSVを選び直してください');raise
        self.report=report
        for code in report['source_codes']:
            row=ttk.Frame(self.mapping_frame);row.pack(fill='x',pady=3)
            ttk.Label(row,text=code+' → ',width=18).pack(side='left')
            var=tk.StringVar(value=LABELS.get(self.previous_mapping.get(code),''));self.mapping_vars[code]=var
            picker=ttk.Combobox(row,textvariable=var,values=[LABELS[w] for w in ('KOBE','SUGITO')],state='readonly',width=18);picker.pack(side='left')
            var.trace_add('write',lambda *_:self.update_ready())
        for r in report['rows']:self.tree.insert('','end',values=(r['source_warehouse'],r['product'],r['date'],r['quantity_p'],{'confirmed':'通常予測・採用候補','provisional':'暫定・正式不可','imputed':'補完・正式不可'}[r['status']]))
        days=[r['date'] for r in report['rows']]
        self.status.set(f"{len(report['rows'])}行 / {len({r['product'] for r in report['rows']})}JAN / {min(days) if days else '?'}〜{max(days) if days else '?'} / 不備{len(report['errors'])}件")
        self.show_details('\n'.join(report['errors']) if report['errors'] else '数量列: '+QUANTITY+'\n大口・特売・注文・製造・入庫は別加算しません。現在庫は在庫PDFを使用します。\n前回確認した倉庫対応がある場合だけ表示します。初回の対応は未設定です。未確定予測は保存しても正式指示に使用できません。')
        self.update_ready()

    def mapping(self):
        reverse={LABELS[w]:w for w in ('KOBE','SUGITO')}
        return {code:reverse.get(var.get()) for code,var in self.mapping_vars.items()}

    def update_ready(self):
        ready=self.report is not None and not self.report['errors'] and not self.service.read_only
        if ready:
            try:validate_mapping(self.report,self.mapping())
            except DataError:ready=False
        self.save_button.configure(state='normal' if ready else 'disabled')

    def save(self):
        if self.report is None:raise DataError(['予測CSVを選んでください'])
        if self.report['errors']:raise DataError(self.report['errors'])
        validate_mapping(self.report,self.mapping())
        if not self.app.operator.get().strip():raise DataError(['担当者を入力してください'])
        folder=filedialog.askdirectory(parent=self,title='在庫CSV・補充条件と合わせる保存先（既存ファイルは上書きしません）')
        if not folder:return
        self.service.save_forecast_csv(self.report,self.mapping(),self.app.operator.get(),True,self.master_revision,self.mapping_revision,folder)
        self.app.invalidate();self.app.data=None
        self.app.info.set('大口を含めない需要予測と原本・倉庫対応の確認証跡を保存しました。在庫・商品PLマスタ・補充条件と合わせて「データを開いて計算」へ進んでください。')
        self.app.result_status.set('予測CSVの準備が完了しました。在庫・補充条件と合わせてデータを開いてください。')
        self.destroy()
