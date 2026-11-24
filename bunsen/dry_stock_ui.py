"""Staging stock entry stays separate from demand and replenishment settings."""
import tkinter as tk
from tkinter import ttk,filedialog
from datetime import datetime
from pathlib import Path
from .core import DataError,JST
from .dry_stock_intake import read_stock,available_p,template
from .ui_theme import responsive_label,scroll_page,table_scrollbars

class DryStockDialog(tk.Toplevel):
    def __init__(self,app):
        super().__init__(app.root);self.app=app;self.report=None;state=app.service.dry_stock();self.revision=state['revision'] if state else 0
        self.title('乾麺倉庫の実在庫取込（Excel・CSV）');self.geometry('1080x720');self.transient(app.root)
        outer=ttk.Frame(self);outer.pack(fill='both',expand=True);body=scroll_page(outer)
        responsive_label(body,text='出荷前の仮置き倉庫の実数を保存します。ムロオへの必要補充量は変更しません。',style='Title.TLabel').pack(fill='x',pady=8)
        responsive_label(body,text='Excelは「実在庫」シートの数量を出荷可能パック数としてJAN別に合算します。CSVは従来の実在庫P−他出荷向け取置Pです。').pack(fill='x',pady=5)
        bar=ttk.Frame(body);bar.pack(fill='x',pady=6)
        ttk.Button(bar,text='CSVひな形を保存',command=lambda:app.run(self.save_template)).pack(side='left',padx=5)
        ttk.Button(bar,text='Excel・CSVを選択',state='disabled' if app.service.read_only else 'normal',command=lambda:app.run(self.choose)).pack(side='left',padx=5)
        self.details_report=state['report'] if state else None
        ttk.Button(bar,text='品番別の読取明細',command=lambda:app.run(self.show_details)).pack(side='left',padx=5)
        self.info=tk.StringVar(value=('保存済み: '+state['as_of']+' / 確認者 '+state['operator']+' / 版'+str(self.revision)) if state else '実在庫は未取込です。')
        responsive_label(body,textvariable=self.info,style='Status.TLabel').pack(fill='x',pady=6)
        frame=ttk.Frame(body);frame.pack(fill='x',pady=8)
        self.tree=ttk.Treeview(frame,columns=('jan','stock','reserved','usable','status'),show='headings',height=7)
        for c,label,width in [('jan','JAN',220),('stock','実在庫P',170),('reserved','他出荷向け取置P',190),('usable','出荷可能P',170),('status','状態',220)]:self.tree.heading(c,text=label);self.tree.column(c,width=width)
        table_scrollbars(self.tree)
        if state:self.show_rows(state['report'])
        ttk.Label(body,text='在庫の基準日時（日本時間、例: 2026-11-24 09:00）').pack(anchor='w',pady=(8,0))
        self.as_of=tk.StringVar();ttk.Entry(body,textvariable=self.as_of,width=28,state='disabled' if app.service.read_only else 'normal').pack(anchor='w',pady=5)
        ttk.Label(body,text='実在庫を確認した担当者').pack(anchor='w');ttk.Entry(body,textvariable=app.operator,width=24,state='disabled' if app.service.read_only else 'normal').pack(anchor='w',pady=5)
        ttk.Button(body,text='読取内容を確認して保存',state='disabled' if app.service.read_only else 'normal',command=lambda:app.run(self.save)).pack(anchor='w',pady=10)
        responsive_label(body,text='Excelの未入力は0にしません。同じJANに未入力行が残れば合計は未確認です。基準日時は担当者が確認入力してください。保存は最新一覧の置換で、以前の原本・履歴は保持します。').pack(fill='x',pady=6)
        self.grab_set()
    def show_rows(self,report):
        self.tree.delete(*self.tree.get_children());excel=report.get('quantity_basis')=='available_p'
        self.tree.heading('stock',text='入力済み小計P' if excel else '実在庫P')
        for r in report['rows']:
            quantity=r.get('entered_p') if excel else r['stock_p']
            usable=available_p(r)
            values=(r['product'],'未入力' if quantity is None else quantity,'差引済み' if excel else r['reserved_p'],'未確認' if usable is None else usable,'未入力あり・合計未確認' if usable is None else '入力済み・JAN合算' if excel else 'CSV読取済み')
            self.tree.insert('','end',values=values)
    def show_details(self):
        report=self.details_report
        if not report or not report.get('details'):raise DataError(['Excelを選択すると品番別の明細を表示できます'])
        popup=tk.Toplevel(self);popup.title('乾麺在庫Excel・品番別の読取明細');popup.geometry('1050x600');popup.transient(self)
        frame=ttk.Frame(popup,padding=12);frame.pack(fill='both',expand=True)
        responsive_label(frame,text='品番は原本のまま保持します。現在の照合はJAN単位です。未入力は0にせず、入力時刻だけで在庫基準日時を確定しません。').pack(fill='x',pady=5)
        listing=ttk.Frame(frame);listing.pack(fill='both',expand=True)
        tree=ttk.Treeview(listing,columns=('row','code','jan','name','quantity','status','time'),show='headings')
        for c,label,width in zip(tree['columns'],('Excel行','品番コード','JAN','品番名','出荷可能P','状態','入力時刻'),(70,130,160,250,110,90,90)):
            tree.heading(c,text=label);tree.column(c,width=width)
        table_scrollbars(tree)
        for r in report['details']:tree.insert('','end',values=(r['row'],r['product_code'],r['product'],r['name'],'未入力' if r['quantity_p'] is None else r['quantity_p'],r['status'],r['input_time']))
        popup.grab_set();popup.protocol('WM_DELETE_WINDOW',lambda:(popup.destroy(),self.grab_set()))
    def save_template(self):
        path=filedialog.asksaveasfilename(parent=self,title='実在庫CSVひな形の新規保存先',initialfile='乾麺倉庫実在庫ひな形.csv',defaultextension='.csv')
        if path:
            with Path(path).open('x',encoding='utf-8-sig',newline='') as f:f.write(template())
    def choose(self):
        path=filedialog.askopenfilename(parent=self,title='乾麺倉庫の在庫Excel・CSV',filetypes=[('在庫Excel・CSV','*.xlsx *.csv'),('Excel','*.xlsx'),('CSV','*.csv')])
        if path:self.load(path)
    def load(self,path):
        self.report=None;self.details_report=None;self.tree.delete(*self.tree.get_children());self.info.set('読取中・未保存')
        report=read_stock(path);self.show_rows(report);self.details_report=report
        known={p['product'] for p in self.app.service.jan_master()['all_products']}
        if self.app.data:known.update(p['product'] for p in self.app.data.tables['products'])
        unknown=sorted(r['product'] for r in report['rows'] if r['product'] not in known)
        warnings=len(report.get('warnings',[]))
        self.info.set('\n'.join(report['errors'][:10]) if report['errors'] else f"{len(report['rows'])}JAN / 未入力を含むJAN {warnings}件 / 未保存"+(' / マスタ未登録 '+str(len(unknown))+'JAN（警告のみ）' if unknown else ''))
        self.report=report
    def save(self):
        if self.report is None:raise DataError(['先に在庫ExcelまたはCSVを選択してください'])
        try:as_of=datetime.strptime(self.as_of.get().strip(),'%Y-%m-%d %H:%M').replace(tzinfo=JST).isoformat()
        except ValueError as exc:raise DataError(['基準日時を「年-月-日 時:分」の日本時間で入力してください']) from exc
        self.app.service.save_dry_stock(self.report,as_of,self.app.operator.get(),True,self.revision)
        self.app.info.set('乾麺倉庫の確認済み実在庫を保存しました。「実在庫と指示数量を照合」で最新の指示数量と比較できます。');self.destroy()

class DryStockComparison(tk.Toplevel):
    def __init__(self,app):
        app.ensure_no_pending_edit()
        if app.data is None or not app.lines:raise DataError(['先にムロオのデータを開いて計算してください'])
        report=app.service.dry_stock_comparison(app.data,app.lines,app.dispatch)
        super().__init__(app.root);self.app=app;self.report=report;self.title('乾麺倉庫の実在庫と指示数量の照合');self.geometry('1150x650');self.transient(app.root)
        outer=ttk.Frame(self);outer.pack(fill='both',expand=True);body=scroll_page(outer)
        stock=app.service.dry_stock()
        responsive_label(body,text='神戸・杉戸の指示数量をJAN別に合算して照合',style='Title.TLabel').pack(fill='x',pady=8)
        responsive_label(body,text=f"出荷日 {report['dispatch']} / 実在庫基準 "+(stock['as_of']+' / 確認者 '+stock['operator']+' / 版'+str(stock['revision']) if stock else '未取込')).pack(fill='x',pady=6)
        excel=stock is not None and stock['report'].get('quantity_basis')=='available_p'
        notice='Excelの出荷可能数は取置差引済みです。実在庫総数・取置内訳は未確認で、取置を二重に引きません。' if excel else 'CSVの実在庫から他出荷向け取置を差し引いた数量です。取置は自動更新しません。'
        responsive_label(body,text=notice+' 出荷予約・実出荷登録や実期限の保証ではありません。',style='Muted.TLabel').pack(fill='x',pady=5)
        missing=sum(r['shortage_p'] is None for r in report['rows']);short=sum(r['shortage_p'] is not None and r['shortage_p']>0 for r in report['rows'])
        responsive_label(body,text=f"不足 {short}商品 / 確認不能 {missing}商品 / 数量上は充足 {len(report['rows'])-missing-short}商品",style='Status.TLabel').pack(fill='x',pady=5)
        frame=ttk.Frame(body);frame.pack(fill='x',pady=8)
        cols=('product','name','status','shortage','total','usable','kobe','sugito','required','stock','reserved')
        self.tree=ttk.Treeview(frame,columns=cols,show='headings',height=10)
        for c,label,width in zip(cols,('JAN','品名','判定','不足P','今回指示P合計','使用可能P','神戸指示P','杉戸指示P','必要P合計','現物総数P','取置内訳P'),(160,180,210,90,120,100,100,100,100,100,80)):
            self.tree.heading(c,text=label);self.tree.column(c,width=width)
        table_scrollbars(self.tree);names={p['product']:p['name'] for p in app.data.tables['products']}
        for r in report['rows']:
            values=(r['product'],names.get(r['product'],'マスタ未確認'),r['status'],r['shortage_p'],r['quantity_p'],r['usable_p'],r['KOBE'],r['SUGITO'],r['required_p'],r['stock_p'],r['reserved_p'])
            self.tree.insert('','end',tags=('attention',) if r['status']!='数量上は充足' else (),values=tuple('未確認' if v is None else v for v in values))
        responsive_label(body,text='不足時は自動配分しません。この画面を閉じ、通常画面で指示PLと理由を修正してから照合し直してください。必要補充量と正式必要量指示の発行条件は変更しません。').pack(fill='x',pady=6)
        ttk.Label(body,text='照合担当者').pack(anchor='w');ttk.Entry(body,textvariable=app.operator,width=24).pack(anchor='w',pady=4)
        ttk.Button(body,text='この照合結果を履歴に保存',state='disabled' if app.service.read_only else 'normal',command=lambda:app.run(self.save)).pack(anchor='w',pady=8)
        self.grab_set()
    def save(self):
        current=self.app.service.dry_stock_comparison(self.app.data,self.app.lines,self.app.dispatch)
        if any(current[k]!=self.report[k] for k in ('data_digest','dispatch','planned','stock_revision','rows')):raise DataError(['数量・条件・実在庫が変わっています。照合画面を開き直してください'])
        self.app.service.save_dry_stock_comparison(self.report,self.app.operator.get());self.app.info.set('乾麺倉庫の数量照合を履歴に保存しました。実在庫の引当・予約はしていません。');self.destroy()
