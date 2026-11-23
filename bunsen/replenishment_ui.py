"""Simplified operational UI; legacy maintenance/history infrastructure is reused."""
from datetime import date
from pathlib import Path
from .simple_ui import SimpleUI
from .exact_quantity import number
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
from .requirements_data import active, line_units, NOTICE, pallet_p
from .ui_theme import apply_theme, brand, section, table_scrollbars, text_panel, responsive_label, scroll_page


class App(SimpleUI, JanCatalogUI, CatalogUI, DeliveryUI, LegacyApp):
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
        self.build_daily()
        self._build_management(scroll_page(self.settings.master))
        self.build_delivery_tab()
        self.build_product_master_tab()
        brand(self)
        self.build_jan_tab()
        self.finish_simple_navigation()
        self.reason.trace_add('write',lambda *_:self.ack.set(False))
        self.allow_provisional.trace_add('write',lambda *_:self.invalidate())
        self.history();self.update_mode()
        self.info.set('JAN・PL方式はrequirements.csvを含む一式を取り込んでください。入庫・輸送中は対象外。旧v2は従来計算として保持します。')

    def update_mode(self):
        super().update_mode()
        self.update_settings_state()
        if hasattr(self,'mode_badge'):
            mode=('復元履歴・閲覧専用' if self.service.read_only else 'ローカル保存')
            if self.service.trial_only:mode='架空データ試用・実出荷不可  /  '+mode
            self.mode_badge.configure(text=mode)

    def _build_management(self,parent):
        from .settings_ui import build_settings_page
        build_settings_page(self,parent)

    def open_settings_data(self):
        if self.import_data(automatic_sources=True):self.open_admin(self.management_page)

    def update_settings_state(self):
        if not hasattr(self,'setting_entries'):return
        loaded=self.data is not None
        writable=loaded and not self.service.read_only
        for entry in self.setting_entries:entry.configure(state='normal' if writable else 'disabled')
        self.setting_save_button.configure(state='normal' if writable else 'disabled')
        self.setting_picker.configure(state='readonly' if loaded else 'disabled')
        if not loaded:
            self.setting_targets={};self.setting_key.set('データを読み込むと倉庫・商品を選べます')
            self.setting_picker.configure(values=())
            for var in self.setting_values.values():var.set('')
            self.setting_status.set('データ未取込です。「データを開いて条件を表示」からCSV一式を読み込んでください。')
            self.expiry_frame.pack_forget()

    def import_data(self,automatic_sources=False):
        folder=filedialog.askdirectory(title='ムロオ新方式CSV一式を選択')
        if not folder:return False
        if automatic_sources:
            path=Path(folder)
            self.use_registered_master.set(False)
            self.use_registered_jan.set((path/"requirements.csv").is_file() and not (path/"products.csv").exists() and not (path/"pallets.csv").exists())
        self.invalidate();self.data=None;self.update_settings_state()
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

        return True

    def invalidate(self,keep_draft=False):
        self.invalidate_delivery()
        if hasattr(self,'editor'):self.editor.pack_forget()
        self.finalized=False
        if hasattr(self,'result_status'):self.result_status.set('未計算  /  データ・条件の変更後は再分析してください')
        super().invalidate(keep_draft=keep_draft)

    def display_settings(self):
        if not hasattr(self,'setting_picker'):return
        self.update_settings_state()
        if self.data is None:return
        self.refresh_product_master()
        self.refresh_jan_master()
        previous=self.setting_key.get()
        names={p['product']:p['name'] for p in self.data.tables['products']}
        self.setting_targets={LABELS[r['warehouse']]+' / '+r['product']+' / '+names[r['product']]:(r['warehouse'],r['product']) for r in self.data.tables['replenishment']}
        options=list(self.setting_targets)
        if active(self.data):self.expiry_frame.pack_forget()
        else:self.expiry_frame.pack(fill='x',pady=5)
        self.expiry_picker.configure(values=[r['product'] for r in self.data.tables['products']])
        self.expiry_product.set(self.data.tables['products'][0]['product']);self.select_expiry()
        self.setting_picker.configure(values=options)
        if options:self.setting_key.set(previous if previous in options else options[0]);self.select_settings()
        self.settings.configure(state='normal');self.settings.delete('1.0','end')
        self.settings.insert('1.0',json.dumps(self.data.tables['replenishment'],ensure_ascii=False,indent=2));self.settings.configure(state='disabled')

    def select_settings(self,_event=None):
        if not self.data:return
        from .settings_ui import selected_target
        w,p=selected_target(self)
        row=next(r for r in self.data.tables['replenishment'] if (r['warehouse'],r['product'])==(w,p))
        for key,var in self.setting_values.items():var.set(str(row[key]))
        self.setting_checked.set(False)
        self.setting_status.set('選択中: '+LABELS[w]+' / '+p+' / '+next(r['name'] for r in self.data.tables['products'] if r['product']==p)+('（閲覧専用）' if self.service.read_only else '。確認済みの条件だけを変更してください。'))

    def apply_settings(self):
        if not self.data or not self.setting_checked.get():raise DataError(['データ取込と設定確認が必要です'])
        values={}
        for key,var in self.setting_values.items():
            value=var.get()
            if not value.isascii() or not value.isdecimal():
                from .settings_ui import FIELDS
                label=next(label for field,label,_ in FIELDS if field==key)
                raise DataError([label+': 確認済みの整数を入力してください（空欄の補完はしません）'])
            values[key]=int(value)
        from .settings_ui import selected_target
        key=selected_target(self)
        fresh=self.service.update_settings(self.data,key,values,self.setting_reason.get(),self.operator.get())
        self.invalidate();self.data=fresh;self.display_settings();self.setting_reason.set('');self.info.set('設定変更を履歴保存しました。全明細を再分析・再確認してください。')
        self.setting_status.set('条件を保存しました。「補充出荷」で再計算して数量を確認してください。')

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
        self.editor.pack_forget()
        self.tree.delete(*self.tree.get_children())
        products={r['product']:r for r in self.data.tables['products']}
        for i,r in enumerate(self.lines):
            self.tree.insert('','end',iid=str(i),tags=('attention' if r['warning'] or r['quantity_p']<number(r['required_p']) else ('alternate' if i%2 else ''),),values=(LABELS[r['warehouse']],r['product']+' '+products[r['product']]['name'],r['inventory_p'],r['demand_p'],r['safety_p'],r['required_p'],r['suggested_p'],r['quantity_p'],r['quantity_p'] if self.finalized else '未確定',line_units(r,products[r['product']]),r['arrival']))
        errors=validate(self.data,self.lines,self.dispatch,self.service.clock())
        state='正式確定済み' if self.finalized else '未確定'
        warnings=sum(bool(r['warning']) or r['quantity_p']<number(r['required_p']) for r in self.lines)
        self.result_status.set(f'{state}  /  {len(self.lines)}明細  /  検証不備 {len(errors)}件  /  注意明細 {warnings}件')
        evidence=[]
        from .forecast_csv import metadata
        forecast_source=metadata(self.data.raw)
        if forecast_source:evidence.append('予測採用列: '+forecast_source['quantity_column']+' / 大口・特売・製造・入庫の別加算なし / 確認者 '+forecast_source['operator']+' / 倉庫対応 '+str(forecast_source['mapping']))
        for row in self.lines:
            if row.get('source_pdf'):evidence.append(f'在庫PDF {row["source_pdf"]} / SHA-256 {row["source_sha256"]} / 転記確認者 {row["inventory_checker"]}')
            evidence.append(f'{LABELS[row["warehouse"]]} {row["product"]}: 在庫基準 {row["inventory_as_of"]} ({row["inventory_basis"]}) / 入庫網羅 {row['transit_checked_at'] if row['transit_checked_at'] is not None else '対象外'} / 予測基準 {row["forecast_as_of"]}\n{row["warning"]}')
            evidence.extend(f'{r["date"]}: 始業予測 {r["opening_p"]}P + 確定入庫 {str(r['transit_p'])+'P' if r['transit_p'] is not None else '対象外'} - 需要予測 {r["forecast_p"]}P = 補充前予測 {r["projected_closing_p"]}P / 安全在庫 {r["safety_p"]}P' for r in row['timeline'])
            if row['quantity_p'] < number(row['required_p']):evidence.append('修正後も安全在庫未達のリスクがあります。理由と数量を確認してください。')
        if errors and not self.details_frame.winfo_manager():self.toggle_details()
        self.show('\n'.join([NOTICE if active(self.data) else '必要補充の指示であり実出荷実績ではありません。']+evidence+['確定前検証']+(errors or ['入力検証に不備なし。担当者の確認操作が必要です。'])))

    def selected(self,_event=None):
        if self.lines and self.tree.selection():
            if not self.finalized:self.editor.pack(fill='x',pady=5,before=self.output_bar)
            row=self.lines[int(self.tree.selection()[0])];self.qty.set(str(row['quantity_p']));self.reason.set(row['edit_reason'])
            factor=pallet_p(self.data,row['warehouse'],row['product']) if active(self.data) else 1
            self.edit_amount.set(str(row['quantity_p']//factor));self.edit_unit.configure(text='指示量 PL' if active(self.data) else '指示量 P')

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
            factor=pallet_p(self.data,row['warehouse'],row['product']) if active(self.data) else 1
            if self.edit_amount.get()!=str(row['quantity_p']//factor):raise DataError(['数量と修正理由を反映してください'])
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
        if hasattr(self,'simple_history'):
            self.simple_history.delete(0,'end')
            for item in self.history_list.get(0,'end'):self.simple_history.insert('end',item)

    def save_mapping_template(self):
        from .diagnostics import export_template
        path=filedialog.asksaveasfilename(title='新方式の実CSV項目対応表',initialfile='csv-mapping-review.json',defaultextension='.json',confirmoverwrite=False)
        if path:export_template(path,self.diagnostic_folder.get() or None,workflow=2)
