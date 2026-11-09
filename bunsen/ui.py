import copy
import json
import os
import sqlite3
import tkinter as tk
import webbrowser
from datetime import date
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from .core import DataError, LABELS, allocation_label, now_jst, shortages, units, validate
from .lot_dialog import LotAllocationDialog
from .planning import comparison_text
from .service import Service, write_reports


class App:
    def __init__(self, root):
        self.root = root
        self.folder = None
        self.data = None
        self.lines = None
        self.dispatch = None
        self.expected_revision = None
        self.lot_dialog = None
        self.draft_number = None
        self.draft_working = None
        self.home = Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'BunsenReplenishment'
        self.service = Service(self.home / 'history.db')
        root.title('ブンセン 補充出荷指示 — サンプル仕様')
        root.geometry('1180x820')
        root.option_add('*Font', ('Yu Gothic UI', 12))
        style = ttk.Style()
        style.configure('Treeview', rowheight=34, font=('Yu Gothic UI', 12))
        style.configure('Treeview.Heading', font=('Yu Gothic UI', 12, 'bold'))
        tabs = ttk.Notebook(root)
        tabs.pack(fill='both', expand=True)
        daily, settings = ttk.Frame(tabs, padding=12), ttk.Frame(tabs, padding=12)
        self.tabs, self.daily = tabs, daily
        tabs.add(daily, text='日常操作')
        tabs.add(settings, text='管理設定・履歴')
        ttk.Label(daily, text='データ取込 → 補充計算 → 不足確認・修正 → 出荷指示書', font=('Yu Gothic UI', 17, 'bold')).pack(anchor='w')
        ttk.Label(daily, text='仮CSV仕様・架空サンプル専用／業務ルールの本番承認前', foreground='#a54000').pack(anchor='w')
        bar = ttk.Frame(daily)
        bar.pack(fill='x', pady=10)
        ttk.Label(bar, text='出荷日').pack(side='left')
        self.day = tk.StringVar(value=now_jst().date().isoformat())
        ttk.Entry(bar, textvariable=self.day, width=13).pack(side='left', padx=5)
        ttk.Button(bar, text='1 データ取込', command=lambda: self.run(self.import_data)).pack(side='left', padx=5)
        ttk.Button(bar, text='2 補充計算', command=lambda: self.run(self.compute)).pack(side='left', padx=5)
        self.info = tk.StringVar(value='CSVフォルダーを選択してください。欠損はゼロとして扱いません。')
        ttk.Label(daily, textvariable=self.info, wraplength=1100).pack(anchor='w')
        resume_bar = ttk.Frame(daily)
        resume_bar.pack(fill='x')
        self.resume_info = tk.StringVar(value='新規計算')
        ttk.Label(resume_bar, textvariable=self.resume_info).pack(side='left')
        ttk.Button(resume_bar, text='仮指示の再開を終了', command=self.invalidate).pack(side='left', padx=10)
        columns = ('warehouse', 'product', 'suggested', 'quantity', 'units', 'lot', 'arrival')
        self.tree = ttk.Treeview(daily, columns=columns, show='headings', height=5, selectmode='browse')
        for col, label, width in zip(columns, ('倉庫', '商品', '推奨P', '指示P', 'PL＋甲', '実ロット', '到着日'), (160, 100, 90, 90, 200, 130, 130)):
            self.tree.heading(col, text=label)
            self.tree.column(col, width=width)
        self.tree.pack(fill='x', pady=10)
        self.tree.bind('<<TreeviewSelect>>', self.selected)
        edit = ttk.Frame(daily)
        edit.pack(fill='x')
        self.qty, self.lot = tk.StringVar(), tk.StringVar()
        ttk.Label(edit, text='選択明細の数量P').pack(side='left')
        ttk.Entry(edit, textvariable=self.qty, width=10).pack(side='left', padx=4)
        ttk.Label(edit, text='実ロット').pack(side='left')
        ttk.Entry(edit, textvariable=self.lot, width=16).pack(side='left', padx=4)
        ttk.Button(edit, text='3 数量・ロットを反映', command=lambda: self.run(self.edit)).pack(side='left')
        ttk.Button(edit, text='複数ロットを指定', command=lambda: self.run(self.open_allocations)).pack(side='left', padx=5)
        self.detail = tk.Text(daily, height=11, wrap='word')
        self.detail.pack(fill='both', expand=True, pady=10)
        author = ttk.Frame(daily)
        author.pack(fill='x')
        ttk.Label(author, text='確定担当者').pack(side='left')
        self.operator = tk.StringVar()
        ttk.Entry(author, textvariable=self.operator, width=16).pack(side='left', padx=5)
        self.ack = tk.BooleanVar(value=False)
        ttk.Checkbutton(author, text='実ロット・期限・積載・改訂差替えを再確認した', variable=self.ack).pack(side='left')
        buttons = ttk.Frame(daily)
        buttons.pack(fill='x', pady=10)
        ttk.Button(buttons, text='4 仮指示書を出力', command=lambda: self.run(lambda: self.issue(False))).pack(side='left', padx=5)
        ttk.Button(buttons, text='4 確定して正式指示書を発行', command=lambda: self.run(lambda: self.issue(True))).pack(side='left', padx=5)
        ttk.Label(settings, text='設定値はCSVで管理します。変更後は一式再取込・再計算が必要です。', wraplength=1000).pack(anchor='w')
        self.settings = tk.Text(settings, height=14, wrap='word')
        self.settings.pack(fill='both', expand=True)
        ttk.Button(settings, text='発行履歴を更新', command=self.history).pack(anchor='w', pady=5)
        self.history_list = tk.Listbox(settings, height=7)
        self.history_list.pack(fill='x')
        ttk.Button(settings, text='選択履歴の帳票を再出力', command=lambda: self.run(self.reprint)).pack(anchor='w', pady=5)
        ttk.Button(settings, text='選択した最新の仮指示を再開（画面内容を置換）', command=lambda: self.run(self.resume_draft)).pack(anchor='w', pady=5)
        self.day.trace_add('write', lambda *_: self.invalidate())
        self.qty.trace_add('write', lambda *_: self.ack.set(False))
        self.lot.trace_add('write', lambda *_: self.ack.set(False))
        self.operator.trace_add('write', lambda *_: self.ack.set(False))
        root.protocol('WM_DELETE_WINDOW', self.close)
        self.history()

    def run(self, action):
        try:
            action()
        except (DataError, ValueError, OSError, sqlite3.Error) as exc:
            self.ack.set(False)
            self.show(str(exc))
            messagebox.showerror('不備・未確認 — 処理を停止しました', str(exc))

    def show(self, text):
        self.detail.configure(state='normal')
        self.detail.delete('1.0', 'end')
        self.detail.insert('1.0', text)
        self.detail.configure(state='disabled')

    def invalidate(self, keep_draft=False):
        if keep_draft and self.draft_number and self.lines is not None:
            self.draft_working = copy.deepcopy(self.lines)
        if not keep_draft:
            self.draft_number, self.draft_working = None, None
            self.resume_info.set('新規計算')
        if self.lot_dialog is not None and self.lot_dialog.winfo_exists():
            self.lot_dialog.destroy()
        self.lot_dialog = None
        self.lines = None
        self.expected_revision = None
        self.ack.set(False)
        self.tree.delete(*self.tree.get_children())
        self.show('条件が変更されました。再計算が必要です。')

    def import_data(self):
        if self.draft_number:
            self.ensure_no_pending_edit()
        folder = filedialog.askdirectory(title='CSV一式のフォルダー')
        if not folder:
            return
        self.invalidate(keep_draft=True)
        self.data = None  # A failed import must not leave the old data usable.
        self.info.set('データ未取込（取込を実行中、または不備による停止）')
        self.data = self.service.import_folder(folder)
        self.folder = folder
        self.info.set(f'取込済み: {folder} / データ識別 {self.data.digest[:16]}')
        self.display_settings()
        self.show('取込完了。補充計算で最新データと照合してください。再開中の仮指示の手修正は保持します。')

    def display_settings(self):
        self.settings.configure(state='normal')
        self.settings.delete('1.0', 'end')
        self.settings.insert('1.0', json.dumps({k: self.data.tables[k] for k in ('products', 'warehouses', 'policies', 'vehicles')}, ensure_ascii=False, indent=2))
        self.settings.configure(state='disabled')

    def compute(self):
        if self.data is None:
            raise DataError(['データ取込が必要'])
        if self.draft_number:
            self.ensure_no_pending_edit()
        self.invalidate(keep_draft=True)
        self.dispatch = date.fromisoformat(self.day.get())
        self.expected_revision = self.service.latest_revision(self.dispatch)
        if self.draft_number:
            self.lines = self.service.reconcile_draft(self.draft_number, self.data, self.draft_working)
        else:
            self.lines = self.service.calculate(self.data, self.dispatch)
        self.refresh()

    def refresh(self):
        self.tree.delete(*self.tree.get_children())
        products = {r['product']: r for r in self.data.tables['products']}
        for i, row in enumerate(self.lines):
            self.tree.insert('', 'end', iid=str(i), values=(LABELS[row['warehouse']], row['product'], row['suggested_p'], row['quantity_p'], units(row['quantity_p'], products[row['product']]), allocation_label(row), row['arrival']))
        errors = validate(self.data, self.lines, self.dispatch)
        basis = [f'{LABELS[r["warehouse"]]} {r["product"]}: 在庫{r["inventory_p"]}P ({r["inventory_as_of"]}) + 輸送中{r["transit_p"]}P - 到着前予測{r["before_p"]}P / 対象予測{r["demand_p"]}P + 安全在庫{r["safety_p"]}P → 必要{r["required_p"]}P → {r["rounding"]}推奨{r["suggested_p"]}P\n予測基準: {", ".join(r["forecast_as_of"])} / 輸送中確認: {r["transit_checked_at"]} {r["warning"]}' for r in self.lines]
        projections = []
        for r in self.lines:
            projections.append(f'{LABELS[r["warehouse"]]} {r["product"]}: ' + ' / '.join(f'{day["date"]}: {day["opening_p"]} + 到着{day["transit_p"]} - 予測{day["forecast_p"]} = {day["projected_closing_p"]}P' for day in r['timeline']))
        revision_note = f'計算時の正式改訂: R{self.expected_revision:03d}。発行済みの場合、次の正式発行は全倉庫一括の差替えです。'
        reference = [comparison_text(self.service.draft_comparison(self.draft_number, self.data, self.lines))] if self.draft_number else []
        self.show('\n'.join(reference + [revision_note] + shortages(self.data, self.lines) + ['\n計算根拠（予測は確定値ではありません）'] + basis + ['\n今回補充前の日別参考値（確定在庫ではありません・日内入庫順は未確認）'] + projections + ['\n正式発行の検証結果'] + (errors or ['検証エラーなし。担当者の再確認操作が必要です。'])))

    def resume_draft(self):
        selection = self.history_list.curselection()
        if not selection:
            raise DataError(['再開する仮指示を履歴から選択してください'])
        number = self.records[selection[0]][0]
        data, lines, dispatch = self.service.resume_draft(number)
        self.invalidate()
        self.day.set(dispatch.isoformat())
        self.data, self.lines = data, lines
        self.folder = None
        self.dispatch = dispatch
        self.draft_number, self.draft_working = number, copy.deepcopy(lines)
        self.expected_revision = self.service.latest_revision(dispatch)
        self.operator.set('')
        self.resume_info.set(f'仮指示 {number} を再開中（再取込・再計算でも手修正を保持）')
        self.info.set(f'保存原本から復元: {number} / データ識別 {data.digest[:16]}。朝データは別途取込してください。')
        self.display_settings()
        self.refresh()
        self.tabs.select(self.daily)

    def selected(self, _event):
        if self.lines and self.tree.selection():
            row = self.lines[int(self.tree.selection()[0])]
            self.qty.set(str(row['quantity_p']))
            self.lot.set(row['lot'])

    def edit(self):
        if not self.lines or not self.tree.selection():
            raise DataError(['修正する明細を選択してください'])
        if not self.qty.get().isascii() or not self.qty.get().isdecimal():
            raise DataError(['数量Pは0以上の整数必須'])
        row = self.lines[int(self.tree.selection()[0])]
        if 'allocations' in row:
            raise DataError(['複数ロット指定済みの明細は「複数ロットを指定」画面で変更してください'])
        before = dict(row)
        row.update(quantity_p=int(self.qty.get()), lot=self.lot.get().strip())
        self.ack.set(False)
        self.service.event('edit', {'digest': self.data.digest, 'before': before, 'after': row})
        self.refresh()

    def open_allocations(self):
        if not self.lines or not self.tree.selection():
            raise DataError(['ロットを指定する明細を選択してください'])
        if self.lot_dialog is not None and self.lot_dialog.winfo_exists():
            self.lot_dialog.lift()
            return
        index = int(self.tree.selection()[0])
        row = self.lines[index]
        if str(row['quantity_p']) != self.qty.get() or row['lot'] != self.lot.get().strip():
            raise DataError(['編集中の数量・ロットを反映してから複数ロットを指定してください'])
        self.ack.set(False)
        self.lot_dialog = LotAllocationDialog(self.root, self.data, self.lines, index, self.dispatch,
                                              lambda quantity, rows: self.apply_allocations(index, quantity, rows), self.service.clock)

    def apply_allocations(self, index, quantity, assignments):
        row = self.lines[index]
        before = copy.deepcopy(row)
        after = dict(row, quantity_p=quantity, lot='', allocations=copy.deepcopy(assignments))
        self.service.event('edit', {'digest': self.data.digest, 'before': before, 'after': after})
        row.update(after)
        self.ack.set(False)
        self.refresh()

    def ensure_no_pending_edit(self):
        if self.lines and self.tree.selection():
            row = self.lines[int(self.tree.selection()[0])]
            if str(row['quantity_p']) != self.qty.get() or row['lot'] != self.lot.get().strip():
                raise DataError(['編集中の数量・ロットを反映してください'])

    def issue(self, formal):
        if not self.lines:
            raise DataError(['再計算が必要'])
        self.ensure_no_pending_edit()
        payload = self.service.issue(self.data, self.lines, self.dispatch, self.operator.get(), formal, self.ack.get(), expected_revision=self.expected_revision, draft_number=self.draft_number)
        self.ack.set(False)
        if formal:
            self.invalidate()
        elif self.draft_number:
            self.draft_number = payload['number']
            self.draft_working = copy.deepcopy(self.lines)
            self.resume_info.set(f'仮指示 {self.draft_number} を再開中（再取込・再計算でも手修正を保持）')
            self.refresh()
        self.history()
        paths = write_reports(self.service.report_payload(payload['number']), self.home / 'reports')
        for path in paths:
            webbrowser.open(path.as_uri())
        self.info.set(f'{payload["number"]} 発行済み。ブラウザーの印刷からPDF保存できます。')

    def history(self):
        self.records = self.service.history()
        self.history_list.delete(0, 'end')
        seen = set()
        for number, at, formal in self.records:
            group = number.rsplit('-R', 1)[0]
            status = '旧版・差替え済み' if group in seen else '最新改訂'
            seen.add(group)
            self.history_list.insert('end', f'{number} | {"正式" if formal else "仮"} | {status} | {at}')

    def reprint(self):
        selection = self.history_list.curselection()
        if not selection:
            raise DataError(['履歴を選択してください'])
        number = self.records[selection[0]][0]
        for path in write_reports(self.service.report_payload(number), self.home / 'reports'):
            webbrowser.open(path.as_uri())

    def close(self):
        self.service.close()
        self.root.destroy()


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()
