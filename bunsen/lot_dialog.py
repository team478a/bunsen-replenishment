"""Manual lot allocation editor. No automatic lot selection or allocation."""
import copy
import sqlite3
import tkinter as tk
from tkinter import messagebox, ttk

from .core import DataError, LABELS, allocations, validate


class LotAllocationDialog(tk.Toplevel):
    def __init__(self, parent, data, lines, index, dispatch, on_save, clock):
        super().__init__(parent)
        self.data, self.lines, self.index = data, lines, index
        self.dispatch, self.on_save, self.clock = dispatch, on_save, clock
        row = lines[index]
        self.assigned = allocations(row) if 'allocations' in row or row['lot'] else []
        self.title(f'複数ロット指定 — {LABELS[row["warehouse"]]} / {row["product"]}')
        self.geometry('1040x760')
        self.transient(parent)
        self.grab_set()
        frame = ttk.Frame(self, padding=12)
        frame.pack(fill='both', expand=True)
        ttk.Label(frame, text='担当者がロットと数量を指定してください。自動選択・自動配分はしません。').pack(anchor='w')
        ttk.Label(frame, text='朝在庫一覧：他の倉庫・明細の割当も表示（残数だけで出荷可能とは判断しません）').pack(anchor='w', pady=(8, 0))
        cols = ('lot', 'stock', 'other', 'remaining', 'expiry', 'checked')
        self.stock_tree = ttk.Treeview(frame, columns=cols, show='headings', height=4, selectmode='browse')
        for col, label, width in zip(cols, ('ロット', '現物P', '他明細割当P', '他明細差引P', '賞味期限', '現物確認日時'), (120, 90, 120, 120, 120, 260)):
            self.stock_tree.heading(col, text=label)
            self.stock_tree.column(col, width=width)
        self.stock_tree.pack(fill='x')
        self.stocks = [r for r in data.tables['dry_stock'] if r['product'] == row['product']]
        other = {}
        for i, line in enumerate(lines):
            if i != index and line['product'] == row['product']:
                for assignment in allocations(line):
                    other[assignment['lot']] = other.get(assignment['lot'], 0) + assignment['quantity_p']
        for i, stock in enumerate(self.stocks):
            allocated = other.get(stock['lot'], 0)
            self.stock_tree.insert('', 'end', iid=str(i), values=(stock['lot'], stock['quantity_p'], allocated, stock['quantity_p'] - allocated, stock['best_before'], stock['checked_at']))
        self.stock_tree.bind('<<TreeviewSelect>>', self.select_stock)
        total_bar = ttk.Frame(frame)
        total_bar.pack(fill='x', pady=8)
        ttk.Label(total_bar, text='指示総量P（明示入力）').pack(side='left')
        self.total = tk.StringVar(value=str(row['quantity_p']))
        ttk.Entry(total_bar, textvariable=self.total, width=12).pack(side='left', padx=5)
        self.summary = tk.StringVar()
        ttk.Label(total_bar, textvariable=self.summary).pack(side='left', padx=8)
        cols = ('lot', 'quantity')
        self.assignment_tree = ttk.Treeview(frame, columns=cols, show='headings', height=4, selectmode='browse')
        for col, label in zip(cols, ('指定ロット', '割当P')):
            self.assignment_tree.heading(col, text=label)
        self.assignment_tree.pack(fill='x')
        self.assignment_tree.bind('<<TreeviewSelect>>', self.select_assignment)
        bar = ttk.Frame(frame)
        bar.pack(fill='x', pady=8)
        self.lot, self.quantity = tk.StringVar(), tk.StringVar()
        ttk.Label(bar, text='ロット').pack(side='left')
        ttk.Combobox(bar, textvariable=self.lot, values=[r['lot'] for r in self.stocks], state='readonly', width=18).pack(side='left', padx=4)
        ttk.Label(bar, text='数量P').pack(side='left')
        ttk.Entry(bar, textvariable=self.quantity, width=10).pack(side='left', padx=4)
        ttk.Button(bar, text='追加・更新', command=lambda: self.run(self.add)).pack(side='left', padx=4)
        ttk.Button(bar, text='選択割当を削除', command=lambda: self.run(self.remove)).pack(side='left', padx=4)
        ttk.Label(frame, text='各ロットの甲単位・期限・当日朝確認・倉庫横断残数・合計積載を全明細で再検証します。', wraplength=980).pack(anchor='w')
        self.result = tk.Text(frame, height=7, wrap='word')
        self.result.pack(fill='both', expand=True, pady=6)
        buttons = ttk.Frame(frame)
        buttons.pack(fill='x')
        ttk.Button(buttons, text='数量・分割を反映（不備は仮指示として保持可）', command=lambda: self.run(self.save)).pack(side='left', padx=4)
        ttk.Button(buttons, text='キャンセル', command=self.destroy).pack(side='left', padx=4)
        self.total.trace_add('write', lambda *_: self.preview())
        self.refresh_assignments()

    def run(self, action):
        try:
            action()
        except (DataError, ValueError, OSError, sqlite3.Error) as exc:
            messagebox.showerror('ロット指定の不備', str(exc), parent=self)

    @staticmethod
    def integer(text, positive=False):
        if not text.isascii() or not text.isdecimal() or (positive and int(text) == 0):
            raise DataError(['数量Pは正整数必須' if positive else '指示総量Pは0以上の整数必須'])
        return int(text)

    def select_stock(self, _event):
        selection = self.stock_tree.selection()
        if selection:
            self.lot.set(self.stocks[int(selection[0])]['lot'])

    def select_assignment(self, _event):
        selection = self.assignment_tree.selection()
        if selection:
            row = self.assigned[int(selection[0])]
            self.lot.set(row['lot'])
            self.quantity.set(str(row['quantity_p']))

    def add(self):
        lot = self.lot.get()
        if lot not in {r['lot'] for r in self.stocks}:
            raise DataError(['朝在庫一覧からロットを選択してください'])
        quantity = self.integer(self.quantity.get(), positive=True)
        for row in self.assigned:
            if row['lot'] == lot:
                row['quantity_p'] = quantity
                break
        else:
            self.assigned.append({'lot': lot, 'quantity_p': quantity})
        self.quantity.set('')
        self.refresh_assignments()

    def remove(self):
        selection = self.assignment_tree.selection()
        if not selection:
            raise DataError(['削除する割当を選択してください'])
        del self.assigned[int(selection[0])]
        self.quantity.set('')
        self.refresh_assignments()

    def refresh_assignments(self):
        self.assignment_tree.delete(*self.assignment_tree.get_children())
        for i, row in enumerate(self.assigned):
            self.assignment_tree.insert('', 'end', iid=str(i), values=(row['lot'], row['quantity_p']))
        self.preview()

    def preview(self):
        total = sum(r['quantity_p'] for r in self.assigned)
        self.summary.set(f'ロット割当合計 {total} P（指示総量とは別に確認）')
        try:
            quantity = self.integer(self.total.get())
            lines = copy.deepcopy(self.lines)
            lines[self.index].update(quantity_p=quantity, lot='', allocations=copy.deepcopy(self.assigned))
            errors = validate(self.data, lines, self.dispatch, self.clock())
            result = '\n'.join(errors) or '検証エラーなし。反映後、担当者の再確認操作が必要です。'
        except DataError as exc:
            result = str(exc)
        self.result.configure(state='normal')
        self.result.delete('1.0', 'end')
        self.result.insert('1.0', result)
        self.result.configure(state='disabled')

    def save(self):
        if self.quantity.get().strip():
            existing = next((r for r in self.assigned if r['lot'] == self.lot.get()), None)
            if existing is None or self.quantity.get() != str(existing['quantity_p']):
                raise DataError(['編集中のロット数量を追加・更新してから反映してください'])
        quantity = self.integer(self.total.get())
        self.on_save(quantity, copy.deepcopy(self.assigned))
        self.destroy()
