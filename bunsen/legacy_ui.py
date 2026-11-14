import argparse
import copy
import json
import os
import sqlite3
import sys
import tkinter as tk
import webbrowser
from datetime import date
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from . import __version__

from .core import DataError, LABELS, allocation_label, now_jst, shortages, units, validate
from .backup import create_backup, inspect_backup, restore_backup
from .diagnostics import PHASE_LABELS, STATUS_LABELS, READ_LABELS, diagnose, export_report, export_template
from .lot_dialog import LotAllocationDialog
from .planning import comparison_text
from .outlook import STATUS as OUTLOOK_STATUS, build_outlook, export_outlook
from .service import Service, write_reports


class LegacyApp:
    def __init__(self, root, data_home=None):
        self.root = root
        self.folder = None
        self.data = None
        self.lines = None
        self.dispatch = None
        self.expected_revision = None
        self.lot_dialog = None
        self.draft_number = None
        self.draft_working = None
        default_home = Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'BunsenReplenishment'
        self.home = Path(data_home or default_home).resolve()
        self.service = Service(self.home / 'history.db')
        self.normal_home = default_home.resolve() if self.service.read_only else self.home
        self.restored_home = self.home if self.service.read_only else None
        self.backup_path = None
        self.backup_manifest = None
        self.write_buttons = []
        root.title(f'ブンセン 補充出荷指示 {__version__} — サンプル仕様')
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
        self.write_button(bar, text='1 データ取込', command=lambda: self.run(self.import_data)).pack(side='left', padx=5)
        self.write_button(bar, text='2 補充計算', command=lambda: self.run(self.compute)).pack(side='left', padx=5)
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
        self.write_button(edit, text='3 数量・ロットを反映', command=lambda: self.run(self.edit)).pack(side='left')
        self.write_button(edit, text='複数ロットを指定', command=lambda: self.run(self.open_allocations)).pack(side='left', padx=5)
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
        self.write_button(buttons, text='4 仮指示書を出力', command=lambda: self.run(lambda: self.issue(False))).pack(side='left', padx=5)
        self.write_button(buttons, text='4 確定して正式指示書を発行', command=lambda: self.run(lambda: self.issue(True))).pack(side='left', padx=5)
        ttk.Label(settings, text='設定値はCSVで管理します。変更後は一式再取込・再計算が必要です。', wraplength=1000).pack(anchor='w')
        self.settings = tk.Text(settings, height=14, wrap='word')
        self.settings.pack(fill='both', expand=True)
        ttk.Button(settings, text='発行履歴を更新', command=self.history).pack(anchor='w', pady=5)
        self.history_list = tk.Listbox(settings, height=7)
        self.history_list.pack(fill='x')
        ttk.Button(settings, text='選択履歴の帳票を再出力', command=lambda: self.run(self.reprint)).pack(anchor='w', pady=5)
        self.write_button(settings, text='選択した最新の仮指示を再開（画面内容を置換）', command=lambda: self.run(self.resume_draft)).pack(anchor='w', pady=5)
        maintenance = ttk.Frame(tabs, padding=12)
        tabs.add(maintenance, text='バックアップ・復元')
        self.storage_info = tk.StringVar()
        ttk.Label(maintenance, textvariable=self.storage_info, wraplength=1080).pack(anchor='w', pady=8)
        ttk.Label(maintenance, text='取込原本を含む履歴DBとreports内の帳票を保存します。未発行の画面編集・元CSVフォルダー・別の場所に保存したPDFは含みません。', wraplength=1080).pack(anchor='w', pady=8)
        ttk.Button(maintenance, text='バックアップZIPを新規作成', command=lambda: self.run(self.save_backup)).pack(anchor='w', pady=8)
        ttk.Button(maintenance, text='ZIPを選択して検証', command=lambda: self.run(self.select_backup)).pack(anchor='w', pady=8)
        self.restore_button = ttk.Button(maintenance, text='検証したZIPを新規フォルダーへ復元', command=lambda: self.run(self.restore_selected), state='disabled')
        self.restore_button.pack(anchor='w', pady=8)
        self.open_restore_button = ttk.Button(maintenance, text='復元履歴を閲覧（画面内容を置換）', command=lambda: self.run(self.open_restored))
        self.open_restore_button.pack(anchor='w', pady=8)
        self.normal_button = ttk.Button(maintenance, text='通常データへ戻る（画面内容を置換）', command=lambda: self.run(self.return_normal))
        self.normal_button.pack(anchor='w', pady=8)
        ttk.Label(maintenance, text='現行データは上書きしません。復元履歴は閲覧専用です。本番復帰にはバックアップ後の出荷・改訂との照合が必要です。切替前に必要な画面編集を仮指示として保存してください。', wraplength=1080).pack(anchor='w', pady=8)
        self.backup_result = tk.Text(maintenance, height=9, wrap='word', state='disabled')
        self.backup_result.pack(fill='both', expand=True)
        diagnostics = ttk.Frame(tabs, padding=12)
        tabs.add(diagnostics, text='取込前診断・項目対応')
        self.diagnostic_report = None
        self.diagnostic_folder = tk.StringVar()
        self.diagnostic_day = tk.StringVar(value=self.day.get())
        self.diagnostic_summary = tk.StringVar(value='実CSV対応は未確認です。仮データ契約v0の診断で、データ取込や正式発行の確認ではありません。')
        ttk.Label(diagnostics, textvariable=self.diagnostic_summary, wraplength=1080).pack(anchor='w', pady=6)
        folder_bar = ttk.Frame(diagnostics)
        folder_bar.pack(fill='x', pady=6)
        ttk.Label(folder_bar, text='診断フォルダー').pack(side='left')
        ttk.Entry(folder_bar, textvariable=self.diagnostic_folder, state='readonly', width=60).pack(side='left', padx=5)
        ttk.Button(folder_bar, text='フォルダーを選択', command=self.select_diagnostic_folder).pack(side='left', padx=5)
        diag_bar = ttk.Frame(diagnostics)
        diag_bar.pack(fill='x', pady=6)
        ttk.Label(diag_bar, text='診断対象出荷日').pack(side='left')
        ttk.Entry(diag_bar, textvariable=self.diagnostic_day, width=14).pack(side='left', padx=5)
        ttk.Button(diag_bar, text='診断を実行（取込しません）', command=lambda: self.run(self.run_diagnostic)).pack(side='left', padx=5)
        self.diagnostic_save = ttk.Button(diag_bar, text='JSON・印刷HTMLを新規保存', command=lambda: self.run(self.save_diagnostic), state='disabled')
        self.diagnostic_save.pack(side='left', padx=5)
        ttk.Button(diagnostics, text='実CSV項目対応表の未確認ひな形を保存', command=lambda: self.run(self.save_mapping_template)).pack(anchor='w', pady=6)
        issue_frame = ttk.Frame(diagnostics)
        issue_frame.pack(fill='both', expand=True)
        columns = ('phase', 'filename', 'line', 'product', 'warehouse', 'date', 'field', 'code', 'message')
        self.diagnostic_tree = ttk.Treeview(issue_frame, columns=columns, show='headings', height=7)
        for name, label, width in zip(columns, ('区分', 'ファイル', '行', '商品', '倉庫', '対象日／基準日時', '項目', 'コード', '理由'), (110, 150, 60, 100, 100, 200, 160, 180, 420)):
            self.diagnostic_tree.heading(name, text=label)
            self.diagnostic_tree.column(name, width=width)
        vertical = ttk.Scrollbar(issue_frame, orient='vertical', command=self.diagnostic_tree.yview)
        horizontal = ttk.Scrollbar(issue_frame, orient='horizontal', command=self.diagnostic_tree.xview)
        self.diagnostic_tree.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        self.diagnostic_tree.grid(row=0, column=0, sticky='nsew')
        vertical.grid(row=0, column=1, sticky='ns')
        horizontal.grid(row=1, column=0, sticky='ew')
        issue_frame.columnconfigure(0, weight=1)
        issue_frame.rowconfigure(0, weight=1)
        self.diagnostic_details = tk.Text(diagnostics, height=6, wrap='word', state='disabled')
        self.diagnostic_details.pack(fill='x', pady=6)
        self.outlook_tab = ttk.Frame(tabs, padding=12)
        tabs.add(self.outlook_tab, text='14日参考見通し')
        self.outlook_report = None
        self.outlook_info = tk.StringVar(value='日常操作で取り込んだデータと対象出荷日を基に表示します。仮／正式指示や手修正は反映しません。')
        ttk.Label(self.outlook_tab, textvariable=self.outlook_info, wraplength=1080).pack(anchor='w', pady=8)
        outlook_bar = ttk.Frame(self.outlook_tab)
        outlook_bar.pack(fill='x', pady=6)
        ttk.Button(outlook_bar, text='取込済データから参考見通しを表示', command=lambda: self.run(self.show_outlook)).pack(side='left', padx=5)
        self.outlook_save = ttk.Button(outlook_bar, text='JSON・印刷HTMLを新規保存', command=lambda: self.run(self.save_outlook), state='disabled')
        self.outlook_save.pack(side='left', padx=5)
        ttk.Label(self.outlook_tab, text='予測に基づく参考値・出荷指示ではありません。欠損の影響日以降は確認不能。日内入庫順・未来の予測精度は未確認です。', wraplength=1080, foreground='#a54000').pack(anchor='w', pady=6)
        outlook_frame = ttk.Frame(self.outlook_tab)
        outlook_frame.pack(fill='both', expand=True)
        outlook_columns = ('warehouse', 'product', 'date', 'period', 'opening', 'transit', 'forecast', 'closing', 'status')
        self.outlook_tree = ttk.Treeview(outlook_frame, columns=outlook_columns, show='headings', height=10, selectmode='browse')
        for col, label, width in zip(outlook_columns, ('倉庫', '商品', '日付', '期間', '始業予測P', '輸送中到着P', '需要予測P', '終業予測P', '状態'), (130, 80, 120, 100, 100, 110, 100, 100, 160)):
            self.outlook_tree.heading(col, text=label)
            self.outlook_tree.column(col, width=width)
        self.outlook_tree.tag_configure('unknown', foreground='#995000')
        self.outlook_tree.tag_configure('shortage_risk', foreground='#aa1800')
        self.outlook_tree.bind('<<TreeviewSelect>>', self.select_outlook)
        oy = ttk.Scrollbar(outlook_frame, orient='vertical', command=self.outlook_tree.yview)
        ox = ttk.Scrollbar(outlook_frame, orient='horizontal', command=self.outlook_tree.xview)
        self.outlook_tree.configure(yscrollcommand=oy.set, xscrollcommand=ox.set)
        self.outlook_tree.grid(row=0, column=0, sticky='nsew')
        oy.grid(row=0, column=1, sticky='ns'); ox.grid(row=1, column=0, sticky='ew')
        outlook_frame.columnconfigure(0, weight=1); outlook_frame.rowconfigure(0, weight=1)
        self.outlook_details = tk.Text(self.outlook_tab, height=7, wrap='word', state='disabled')
        self.outlook_details.pack(fill='x', pady=6)
        self.diagnostic_folder.trace_add('write', lambda *_: self.invalidate_diagnostic())
        self.diagnostic_day.trace_add('write', lambda *_: self.invalidate_diagnostic())
        self.day.trace_add('write', lambda *_: self.invalidate())
        self.qty.trace_add('write', lambda *_: self.ack.set(False))
        self.lot.trace_add('write', lambda *_: self.ack.set(False))
        self.operator.trace_add('write', lambda *_: self.ack.set(False))
        root.protocol('WM_DELETE_WINDOW', self.close)
        self.history()
        self.update_mode()

    def write_button(self, parent, **options):
        button = ttk.Button(parent, **options)
        self.write_buttons.append(button)
        return button

    def invalidate_outlook(self):
        self.outlook_report = None
        self.outlook_save.configure(state='disabled')
        self.outlook_tree.delete(*self.outlook_tree.get_children())
        self.outlook_info.set('データまたは条件が変更されました。参考見通しを再表示してください。')
        self.outlook_details.configure(state='normal')
        self.outlook_details.delete('1.0', 'end')
        self.outlook_details.configure(state='disabled')

    def show_outlook(self):
        self.invalidate_outlook()
        if self.data is None:
            raise DataError(['日常操作でデータを取り込んでください'])
        report = build_outlook(self.data, date.fromisoformat(self.day.get()), self.service.clock())
        self.outlook_report = report
        for index, row in enumerate(report['rows']):
            number = lambda key: '確認不能' if row[key] is None else row[key]
            values = (LABELS[row['warehouse']], row['product'], row['date'], row['period'],
                      number('opening_p'), number('transit_p'), number('forecast_p'), number('projected_closing_p'), OUTLOOK_STATUS[row['status']])
            self.outlook_tree.insert('', 'end', iid=str(index), values=values, tags=(row['status'],))
        self.outlook_info.set(f'{report["start"]}〜{report["end"]} / データ識別 {report["dataset_digest"][:16]} / 不備{len(report["issues"])}件。補充前・予測参考値です。')
        self.outlook_save.configure(state='normal')

    def select_outlook(self, _event=None):
        if self.outlook_report is None or not self.outlook_tree.selection():
            return
        row = self.outlook_report['rows'][int(self.outlook_tree.selection()[0])]
        basis = next(r for r in self.outlook_report['bases'] if (r['warehouse'], r['product']) == (row['warehouse'], row['product']))
        self.outlook_details.configure(state='normal')
        self.outlook_details.delete('1.0', 'end')
        self.outlook_details.insert('1.0', f'{row["reason"] or "入力不備の検出なし。確定在庫ではありません。"}\n' + json.dumps(dict(row=row, basis=basis), ensure_ascii=False, indent=2))
        self.outlook_details.configure(state='disabled')

    def save_outlook(self):
        if self.outlook_report is None:
            raise DataError(['参考見通しの再表示が必要です'])
        path = filedialog.asksaveasfilename(title='参考見通しを新規保存', initialfile='reference-outlook.json', defaultextension='.json', filetypes=[('参考見通しJSON', '*.json')], confirmoverwrite=False)
        if path:
            paths = export_outlook(self.outlook_report, path, self.folder)
            self.open_report(paths[1])

    def invalidate_diagnostic(self):
        self.diagnostic_report = None
        self.diagnostic_tree.delete(*self.diagnostic_tree.get_children())
        self.diagnostic_save.configure(state='disabled')
        self.diagnostic_summary.set('診断条件が変わりました。再診断が必要です。実CSV対応・業務承認は未確認です。')
        self.diagnostic_details.configure(state='normal')
        self.diagnostic_details.delete('1.0', 'end')
        self.diagnostic_details.configure(state='disabled')

    def select_diagnostic_folder(self):
        folder = filedialog.askdirectory(title='読み取りだけを行うCSVフォルダー')
        if folder:
            self.diagnostic_folder.set(folder)

    def run_diagnostic(self):
        self.invalidate_diagnostic()
        self.ack.set(False)
        if not self.diagnostic_folder.get():
            raise DataError(['診断フォルダーを選択してください'])
        dispatch = date.fromisoformat(self.diagnostic_day.get())
        report = diagnose(self.diagnostic_folder.get(), dispatch, self.service.clock(),workflow=getattr(self,'diagnostic_workflow',None))
        self.diagnostic_report = report
        for i, issue in enumerate(report['issues']):
            display = dict(issue, phase=PHASE_LABELS[issue['phase']])
            self.diagnostic_tree.insert('', 'end', iid=str(i), values=tuple(display.get(field) if display.get(field) is not None else '—' for field in self.diagnostic_tree['columns']))
        status = '不備あり' if report['issues'] else '仮契約の入力不備は検出されませんでした'
        self.diagnostic_summary.set(f'{status}: {len(report["issues"])}件。正式発行可の判定ではありません。実CSV対応・業務承認は未確認です。')
        details = [f'対象出荷日: {report["dispatch"]}／診断日時: {report["checked_at"]}',
                   f'読取中変更検査: {READ_LABELS[report["read_consistency"]["status"]]}。原子的取得ではありません。',
                   f'仮契約適合: {"適合" if report["contract_compatible"] else "不適合"}／計算条件: {"確認可" if report["calculation_possible"] else "不備あり・未実施"}',
                   *report['skipped_checks'], *report['limitations']]
        details.extend(f'{info["filename"]}: {STATUS_LABELS[info["status"]]}／列={info["headers"]}／原本SHA-256={info["sha256"] or "未取得"}' for info in report['files'])
        self.diagnostic_details.configure(state='normal')
        self.diagnostic_details.insert('1.0', '\n'.join(details))
        self.diagnostic_details.configure(state='disabled')
        self.diagnostic_save.configure(state='normal')

    def save_diagnostic(self):
        if self.diagnostic_report is None:
            raise DataError(['先に診断を実行してください'])
        path = filedialog.asksaveasfilename(title='診断結果の新規保存先（入力フォルダー外）', defaultextension='.json', filetypes=[('診断JSON', '*.json')], confirmoverwrite=False)
        if path:
            outputs = export_report(self.diagnostic_report, path)
            self.open_report(outputs[1])

    def save_mapping_template(self):
        path = filedialog.asksaveasfilename(title='未確認の項目対応表を新規保存', initialfile='csv-mapping-review.json', defaultextension='.json', filetypes=[('項目対応表JSON', '*.json')], confirmoverwrite=False)
        if path:
            export_template(path, self.diagnostic_folder.get() or None)

    def update_mode(self):
        for button in self.write_buttons:
            button.configure(state='disabled' if self.service.read_only else 'normal')
        mode = '復元履歴・閲覧専用（実出荷用の発行は不可）' if self.service.read_only else '通常データ'
        if self.service.trial_only:
            mode = '架空データ試用・実出荷不可 / ' + mode
        self.root.title(f'ブンセン 補充出荷指示 {__version__} — {mode}')
        self.storage_info.set(f'{mode}: {self.home}')
        if self.service.read_only:
            self.info.set(f'{mode}: {self.home}')
        self.open_restore_button.configure(state='normal' if self.restored_home else 'disabled')
        self.normal_button.configure(state='normal' if self.home != self.normal_home and (self.normal_home / 'history.db').is_file() else 'disabled')
        self.normal_button.configure(text='元の試用データへ戻る（画面内容を置換）' if self.service.trial_only else '通常データへ戻る（画面内容を置換）')

    def backup_message(self, text):
        self.backup_result.configure(state='normal')
        self.backup_result.delete('1.0', 'end')
        self.backup_result.insert('1.0', text)
        self.backup_result.configure(state='disabled')

    def save_backup(self):
        path = filedialog.asksaveasfilename(title='新規バックアップの保存先（現行データ外）', defaultextension='.zip', filetypes=[('バックアップZIP', '*.zip')], confirmoverwrite=False, initialfile=f'bunsen-{now_jst():%Y%m%d-%H%M%S}.zip')
        if not path:
            return
        self.backup_message('バックアップを作成・検証中です。')
        self.root.update_idletasks()
        try:
            manifest = create_backup(self.service.db, self.home, path, self.service.clock)
        except (DataError, OSError, sqlite3.Error) as exc:
            self.backup_message(f'バックアップ作成失敗。\n{exc}')
            raise
        self.backup_message(f'保存・検証完了: {path}\n{self.manifest_text(manifest)}')
        if not self.service.read_only:
            self.service.event('backup_created', {'archive_sha256': manifest['archive_sha256'], 'counts': manifest['counts']})

    @staticmethod
    def manifest_text(manifest):
        counts = manifest['counts']
        return f'作成日時: {manifest["created_at"]}\n操作履歴: {counts["events"]}件／発行履歴: {counts["issues"]}件\n直近発行番号: {counts["last_issue"] or "なし"}\n保存ファイル: {len(manifest["files"])}件\nZIP識別: {manifest["archive_sha256"]}'

    def select_backup(self):
        path = filedialog.askopenfilename(title='検証するバックアップZIP', filetypes=[('バックアップZIP', '*.zip')])
        if not path:
            return
        self.backup_path, self.backup_manifest = None, None
        self.restore_button.configure(state='disabled')
        self.backup_message('検証中。完了するまで復元できません。')
        self.root.update_idletasks()
        try:
            manifest = inspect_backup(path)
        except (DataError, OSError) as exc:
            self.backup_message(f'検証失敗。復元できません。\n{exc}')
            raise
        self.backup_path, self.backup_manifest = Path(path), manifest
        self.restore_button.configure(state='normal')
        self.backup_message(f'検証済み: {path}\n{self.manifest_text(manifest)}')

    def restore_selected(self):
        if self.backup_path is None or self.backup_manifest is None:
            raise DataError(['先にZIPを選択して検証してください'])
        parent = filedialog.askdirectory(title='復元先の親フォルダー（新規フォルダーを自動作成）')
        if not parent:
            return
        try:
            restored = restore_backup(self.backup_path, parent, self.backup_manifest['archive_sha256'], self.service.clock)
        except (DataError, OSError) as exc:
            self.backup_path, self.backup_manifest = None, None
            self.restore_button.configure(state='disabled')
            self.backup_message(f'復元失敗。現行データは変更していません。ZIPを再検証してください。\n{exc}')
            raise
        self.restored_home = restored
        self.update_mode()
        self.backup_message(f'復元完了（閲覧専用）: {restored}\n現行の出荷履歴は置換していません。「復元履歴を閲覧」で確認できます。')
        if not self.service.read_only:
            self.service.event('restore_created', {'directory': str(restored), 'archive_sha256': self.backup_manifest['archive_sha256']})

    def switch_home(self, home):
        home = Path(home).resolve()
        replacement = Service(home / 'history.db', self.service.clock)
        self.invalidate()
        self.data, self.folder = None, None
        self.operator.set('')
        self.settings.configure(state='normal')
        self.settings.delete('1.0', 'end')
        self.settings.configure(state='disabled')
        self.service.close()
        self.service, self.home = replacement, home
        self.history()
        self.info.set(f'データ切替済み: {home}。必要なCSVを改めて取込してください。')
        self.update_mode()

    def open_restored(self):
        if self.restored_home is None:
            raise DataError(['先にバックアップを復元してください'])
        if not (self.restored_home / 'restore-info.json').is_file():
            raise DataError(['復元フォルダーの閲覧専用マーカーがありません。ZIPから新規復元してください'])
        self.switch_home(self.restored_home)

    def return_normal(self):
        if not (self.normal_home / 'history.db').is_file():
            raise DataError(['通常データがありません。本番復帰には出荷履歴の照合が必要です'])
        self.switch_home(self.normal_home)

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
        self.invalidate_outlook()
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
            self.open_report(path)
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
        folder = self.home / ('view-output' if self.service.read_only else 'reports')
        for path in write_reports(self.service.report_payload(number), folder):
            self.open_report(path)

    @staticmethod
    def open_report(path):
        # Frozen Windows DLL paths must not leak into the external browser.
        if sys.platform == 'win32' and getattr(sys, 'frozen', False):
            import ctypes
            from ctypes import wintypes
            kernel = ctypes.WinDLL('kernel32', use_last_error=True)
            kernel.GetDllDirectoryW.argtypes = [wintypes.DWORD, wintypes.LPWSTR]
            kernel.GetDllDirectoryW.restype = wintypes.DWORD
            kernel.SetDllDirectoryW.argtypes = [wintypes.LPCWSTR]
            kernel.SetDllDirectoryW.restype = wintypes.BOOL
            buffer = ctypes.create_unicode_buffer(32768)
            length = kernel.GetDllDirectoryW(len(buffer), buffer)
            if length >= len(buffer):
                raise OSError('DLL検索パスを確認できません')
            previous = buffer.value if length else None
            if not kernel.SetDllDirectoryW(None):
                raise ctypes.WinError(ctypes.get_last_error())
            try:
                webbrowser.open(path.as_uri())
            finally:
                if not kernel.SetDllDirectoryW(previous):
                    raise ctypes.WinError(ctypes.get_last_error())
        else:
            webbrowser.open(path.as_uri())

    def close(self):
        self.service.close()
        self.root.destroy()
