"""PDF -> unconfirmed candidates -> explicit review -> inventory CSV."""
import base64
import tkinter as tk
from tkinter import ttk, filedialog
from pathlib import Path
import zipfile
import re

from .core import DataError, LABELS, WAREHOUSES
from .pdf_reader import read_report, suggested_quantity
from .ui_theme import table_scrollbars, responsive_label, scroll_page

POLICIES={'空欄は未確認':'unknown','バラの空欄は0（確認済み）':'loose_zero','ケース・バラの空欄は0（確認済み）':'all_zero'}


class PDFInventoryDialog(tk.Toplevel):
    def __init__(self,app):
        super().__init__(app.root)
        self.app=app;self.service=app.service;state=self.service.jan_master()
        self.products=state['products'];self.revision=state['revision'];self.reports={};self.items={};self.selection=None
        self.title('在庫PDFの読み取り・確認');self.geometry('1100x760');self.minsize(950,650);self.transient(app.root)
        page=ttk.Frame(self,padding=14);page.pack(fill='both',expand=True)
        body=scroll_page(page)
        responsive_label(body,text='PDFを読み取り → 数量を確認・修正 → 保存。未確認の空欄は0にしません。CSVの倉庫コードは使わず、帳票内の営業所名で判定します。').pack(fill='x',pady=5)
        self.meta={};self.status={}
        for warehouse in WAREHOUSES:
            frame=ttk.LabelFrame(body,text=LABELS[warehouse],padding=7);frame.pack(fill='x',pady=4)
            ttk.Button(frame,text='PDF / ZIPを選択',command=lambda w=warehouse:self.app.run(lambda:self.choose(w))).grid(row=0,column=0,padx=3)
            self.status[warehouse]=tk.StringVar(value='未読取')
            ttk.Label(frame,textvariable=self.status[warehouse]).grid(row=0,column=1,columnspan=6,sticky='w')
            ttk.Button(frame,text='原本を見る',command=lambda w=warehouse:self.app.run(lambda:self.preview(w))).grid(row=0,column=7,padx=3)
            values={k:tk.StringVar() for k in ('as_of','policy')};values['policy'].set('空欄は未確認');self.meta[warehouse]=values
            ttk.Label(frame,text='基準時刻（PDF左下を初期表示）').grid(row=1,column=0,sticky='w')
            ttk.Entry(frame,textvariable=values['as_of'],width=12).grid(row=1,column=1,padx=4)
            ttk.Label(frame,text='集計日まで反映済み・翌日から需要計算').grid(row=2,column=0,columnspan=8,sticky='w')
            ttk.Label(frame,text='空欄の意味').grid(row=1,column=3)
            picker=ttk.Combobox(frame,textvariable=values['policy'],values=list(POLICIES),state='readonly',width=32);picker.grid(row=1,column=4,columnspan=4,padx=3)
            picker.bind('<<ComboboxSelected>>',lambda _,w=warehouse:self.app.run(lambda:self.change_policy(w)))
        listing=ttk.Frame(body);listing.pack(fill='both',expand=True,pady=8)
        self.tree=ttk.Treeview(listing,columns=('warehouse','jan','name','case','loose','factor','quantity','state'),show='headings',height=9,selectmode='browse')
        for key,label,width in zip(self.tree['columns'],('倉庫','JAN','商品','ケース','バラ','C入','確認数量 P','状態'),(90,160,250,75,65,60,110,180)):
            self.tree.heading(key,text=label);self.tree.column(key,width=width)
        table_scrollbars(self.tree);self.tree.bind('<<TreeviewSelect>>',self.selected)
        self.quantity=tk.StringVar();self.reason=tk.StringVar()
        editor=ttk.Frame(body);editor.pack(fill='x',pady=5)
        ttk.Label(editor,text='選択商品の数量 P').pack(side='left');ttk.Entry(editor,textvariable=self.quantity,width=12).pack(side='left',padx=5)
        ttk.Label(editor,text='変更・確認根拠').pack(side='left');ttk.Entry(editor,textvariable=self.reason,width=36).pack(side='left',padx=5)
        ttk.Button(editor,text='反映',command=lambda:self.app.run(self.apply_edit)).pack(side='left',padx=4)
        self.note=tk.StringVar(value='原本の作成日時と在庫基準日時は区別してください。集計日と一致する確認済み基準日時を指定します。')
        responsive_label(body,textvariable=self.note).pack(fill='x',pady=4)
        actions=ttk.Frame(body);actions.pack(fill='x',pady=6)
        ttk.Label(actions,text='担当者').pack(side='left');ttk.Entry(actions,textvariable=app.operator,width=18).pack(side='left',padx=5)
        ttk.Button(actions,text='数量を確認して在庫CSVを保存',style='Primary.TButton',state='disabled' if self.service.read_only else 'normal',command=lambda:self.app.run(self.save)).pack(side='left',padx=5)
        self.grab_set()

    def choose(self,warehouse):
        path=filedialog.askopenfilename(parent=self,title=LABELS[warehouse]+'の在庫PDF/ZIP',filetypes=[('在庫PDF / ZIP','*.pdf *.zip')])
        if not path:return
        if Path(path).suffix.lower()=='.zip':
            try:
                with zipfile.ZipFile(path) as archive:
                    names=[i.filename for i in archive.infolist() if i.filename.lower().endswith('.pdf') and not i.is_dir()]
                    if not names or len(names)>500:raise DataError(['ZIP内のPDFは1〜500件にしてください'])
                    if len(names)!=len(set(names)):raise DataError(['ZIP内に同じ名前のPDFがあります'])
            except zipfile.BadZipFile as exc:raise DataError(['ZIPを読み取れません: '+str(exc)]) from exc
            dialog=tk.Toplevel(self);dialog.title('読み取る1日分のPDFを選択');dialog.transient(self)
            ttk.Label(dialog,text='異なる日付の在庫は合算しません。対象日のPDFを1つ選んでください。').pack(padx=12,pady=8)
            listing=tk.Listbox(dialog,width=90,height=16);listing.pack(fill='both',expand=True,padx=12)
            for name in names:listing.insert('end',name)
            def select():
                selection=listing.curselection()
                if not selection:raise DataError(['PDFを選んでください'])
                name=names[selection[0]];dialog.destroy();self.grab_set();self.load(warehouse,path,name)
            ttk.Button(dialog,text='選択して読み取り',command=lambda:self.app.run(select)).pack(pady=8)
            dialog.grab_set();dialog.protocol('WM_DELETE_WINDOW',lambda:(dialog.destroy(),self.grab_set()))
        else:self.load(warehouse,path)

    def load(self,warehouse,path,member=None):
        self.reports.pop(warehouse,None)
        self.items={k:v for k,v in self.items.items() if k[0]!=warehouse};self.selection=None;self.refresh()
        self.status[warehouse].set('読取中…');self.update_idletasks()
        try:
            report=read_report(path,member)
            if report['warehouse']!=warehouse:raise DataError([LABELS[warehouse]+'の欄ですが、PDFは'+LABELS[report['warehouse']]+'です'])
            self.reports[warehouse]=report
            self.status[warehouse].set(f"集計日 {report['stock_date']} / {report['pages']}ページ / 作成 {report['printed_at']}")
            self.meta[warehouse]['as_of'].set(report['printed_at'].split()[-1]);self.meta[warehouse]['policy'].set('空欄は未確認')
            self.populate(warehouse)
        except Exception:
            self.status[warehouse].set('読取できませんでした。PDFを選び直してください');raise

    def populate(self,warehouse):
        report=self.reports[warehouse];source={r['product']:r for r in report['rows']};known={r['product']:r for r in self.products}
        for product in sorted(source.keys()|known.keys()):
            row=source.get(product);value=suggested_quantity(row,POLICIES[self.meta[warehouse]['policy'].get()]) if row else None
            self.items[(warehouse,product)]=dict(source=row,target=product in known,name=known[product]['name'] if product in known else row['name'],quantity=value,suggested=value,reason='',edited=False)
        self.refresh()

    def refresh(self):
        self.selection=None;self.quantity.set('') if hasattr(self,'quantity') else None
        self.reason.set('') if hasattr(self,'reason') else None
        self.tree.delete(*self.tree.get_children());self.keys=list(self.items)
        for index,key in enumerate(self.keys):
            item=self.items[key];row=item['source'];state=('対象外・マスタ未登録' if not item['target'] else 'PDFに商品なし・要確認' if not row else '要確認: '+' / '.join(row['issues']) if row['issues'] else '要確認・数量空欄' if item['quantity'] is None else '手修正' if item['edited'] else '読取済み・未確定')
            self.tree.insert('','end',iid=str(index),values=(LABELS[key[0]],key[1],item['name'],row['cases'] if row and row['cases'] is not None else '空欄',row['loose'] if row and row['loose'] is not None else '空欄',row['p_per_case'] if row else '',item['quantity'] if item['quantity'] is not None else '',state))

    def selected(self,_=None):
        if not self.tree.selection():return
        self.selection=self.keys[int(self.tree.selection()[0])];item=self.items[self.selection]
        self.quantity.set('' if item['quantity'] is None else str(item['quantity']));self.reason.set(item['reason'])
        row=item['source']
        self.note.set('原本ページ: '+','.join(map(str,row['pages'])) if row else 'PDFに商品がありません。数量を推定せず、原本確認の根拠を記録してください。')

    def pending(self):
        if self.selection:
            item=self.items[self.selection];saved='' if item['quantity'] is None else str(item['quantity'])
            if self.quantity.get()!=saved or self.reason.get()!=item['reason']:raise DataError(['数量と確認根拠を「反映」してください'])

    def change_policy(self,warehouse):
        self.pending()
        if warehouse not in self.reports:return
        for key,item in self.items.items():
            if key[0]!=warehouse:continue
            item['suggested']=suggested_quantity(item['source'],POLICIES[self.meta[warehouse]['policy'].get()]) if item['source'] else None
            if not item['edited']:item['quantity']=item['suggested']
        self.refresh()

    def apply_edit(self):
        if self.selection is None:raise DataError(['商品を選んでください'])
        item=self.items[self.selection]
        if not item['target']:raise DataError(['未登録JANです。先に商品マスタへ登録し、画面を開き直してください'])
        text=self.quantity.get()
        if not text.isascii() or not text.isdecimal():raise DataError(['確認数量は0以上の整数Pで入力してください'])
        quantity=int(text);reason=self.reason.get().strip()
        if quantity!=item['suggested'] and not reason:raise DataError(['変更・未読取数量には原本確認の根拠が必要です'])
        item.update(quantity=quantity,reason=reason,edited=True);self.refresh()

    def save(self):
        self.pending();self.service.ensure_writable()
        if set(self.reports)!=set(WAREHOUSES):raise DataError(['神戸・杉戸の両PDFを読み取ってください'])
        if any(item['target'] and item['quantity'] is None for item in self.items.values()):raise DataError(['数量が未確認の商品があります。0補完せず原本を確認してください'])
        reports={}
        from .core import stamp
        for w,r in self.reports.items():
            text=self.meta[w]['as_of'].get().strip()
            at=r['stock_date']+'T'+text+(':00' if len(text)==5 else '')+'+09:00' if re.fullmatch(r'\d{2}:\d{2}(?::\d{2})?',text) else text
            try:stamp(at)
            except ValueError:raise DataError([LABELS[w]+': 確認済み在庫基準時刻をHH:MMで入力してください'])
            reports[w]=dict(report=r,as_of=at,basis='closing',time_origin='pdf_footer' if text==r['printed_at'].split()[-1] else 'manual',blank_policy=POLICIES[self.meta[w]['policy'].get()])
        decisions={key:dict(quantity_p=item['quantity'],reason=item['reason']) for key,item in self.items.items() if item['target']}
        folder=filedialog.askdirectory(parent=self,title='確認済み在庫CSVの新規保存先')
        if not folder:return
        self.service.save_pdf_inventory(reports,decisions,self.app.operator.get(),True,self.revision,folder)
        self.app.invalidate();self.app.data=None
        self.app.info.set('確認済み在庫CSV・商品PLマスタ・PDF原本証跡・修正履歴を保存しました。需要予測と補充条件CSVを配置して「データを開いて計算」へ進んでください。')
        self.app.result_status.set('在庫PDFの確認・保存が完了しました。需要予測・補充条件と合わせてデータを開いてください。')
        self.destroy()

    def preview(self,warehouse):
        if warehouse not in self.reports:raise DataError(['先にPDFを読み取ってください'])
        PDFPreview(self,base64.b64decode(self.reports[warehouse]['content_base64']))


class PDFPreview(tk.Toplevel):
    def __init__(self,parent,content):
        super().__init__(parent)
        import pypdfium2
        self.document=pypdfium2.PdfDocument(content);self.title('在庫PDF原本');self.geometry('900x740');self.transient(parent)
        self.page=tk.StringVar(value='1');picker=ttk.Combobox(self,textvariable=self.page,values=[str(i+1) for i in range(len(self.document))],state='readonly',width=6);picker.pack(anchor='w',padx=6,pady=4);picker.bind('<<ComboboxSelected>>',lambda _:self.render())
        frame=ttk.Frame(self);frame.pack(fill='both',expand=True);self.canvas=tk.Canvas(frame)
        y=ttk.Scrollbar(frame,command=self.canvas.yview);y.pack(side='right',fill='y');x=ttk.Scrollbar(frame,orient='horizontal',command=self.canvas.xview);x.pack(side='bottom',fill='x');self.canvas.pack(fill='both',expand=True);self.canvas.configure(yscrollcommand=y.set,xscrollcommand=x.set)
        self.protocol('WM_DELETE_WINDOW',self.close);self.render();self.grab_set()

    def render(self):
        from PIL import ImageTk
        page=self.document[int(self.page.get())-1];bitmap=page.render(scale=1.4)
        self.photo=ImageTk.PhotoImage(bitmap.to_pil(),master=self);self.canvas.delete('all');self.canvas.create_image(0,0,anchor='nw',image=self.photo);self.canvas.configure(scrollregion=self.canvas.bbox('all'));self.canvas.yview_moveto(0)
        bitmap.close();page.close()

    def close(self):
        self.document.close();parent=self.master;self.destroy();parent.grab_set()
