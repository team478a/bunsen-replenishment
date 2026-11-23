"""Plain-language replenishment settings; never suggest business defaults."""
import tkinter as tk
from tkinter import ttk
from .ui_theme import section,responsive_label

FIELDS=(
    ('cycle_days','補充する間隔（日）','何日ごとに補充するか。1以上の整数を入力します。'),
    ('lead_days','到着までの日数（日）','出荷日から到着までの暦日数（休日を含む）。0は出荷当日です。'),
    ('safety_days','安全在庫の日数（日）','各日の翌日から何日分の予測需要を残すか。0は安全在庫なしです。'),
    ('coverage_days','補充でまかなう日数（日）','到着日から何日分を補充の対象にするか。1以上の整数です。'),
)

def build_settings_page(app,parent):
    ttk.Label(parent,text='補充の条件を設定する',style='Title.TLabel').pack(anchor='w',pady=8)
    responsive_label(parent,text='神戸・杉戸の全商品に共通する条件です。CSV取込や倉庫・商品の選択は不要です。').pack(fill='x',pady=5)
    app.setting_status=tk.StringVar(value='共通条件が未設定です。業務で確認した4項目を入力して保存してください。')
    responsive_label(parent,textvariable=app.setting_status,style='Status.TLabel').pack(fill='x',pady=6)
    conditions=section(parent,'補充条件（全商品共通）')
    app.setting_values={};app.setting_entries=[]
    for key,label,help_text in FIELDS:
        row=ttk.Frame(conditions);row.pack(fill='x',pady=5)
        ttk.Label(row,text=label,width=25).pack(side='left')
        var=tk.StringVar();app.setting_values[key]=var
        entry=ttk.Entry(row,textvariable=var,width=8,state='normal');entry.pack(side='left',padx=(4,10));app.setting_entries.append(entry)
        hint=ttk.Label(row,text=help_text,style='Muted.TLabel',wraplength=500)
        hint.pack(side='left',fill='x',expand=True)
        hint.bind('<Configure>',lambda event,w=hint:w.configure(wraplength=max(160,event.width-8)))
    responsive_label(conditions,text='実際の対象期間は「補充する間隔」と「補充でまかなう日数」の大きい方です。安全在庫は日別の需要予測から計算します。',style='Muted.TLabel').pack(fill='x',pady=6)
    last=section(parent,'確認して保存')
    app.setting_reason=tk.StringVar();app.setting_checked=tk.BooleanVar(value=False)
    ttk.Label(last,text='設定・変更理由（条件の確認根拠）').pack(anchor='w')
    reason=ttk.Entry(last,textvariable=app.setting_reason);reason.pack(fill='x',pady=4);app.setting_entries.append(reason)
    responsive_label(last,text='運用見直し、配送条件変更など、確認した根拠を記入してください。初期表示やサンプルの数値をそのまま正式値にしないでください。',style='Muted.TLabel').pack(fill='x',pady=4)
    row=ttk.Frame(last);row.pack(fill='x',pady=5)
    ttk.Label(row,text='設定を確認した担当者').pack(side='left')
    entry=ttk.Entry(row,textvariable=app.operator,width=18);entry.pack(side='left',padx=8);app.setting_entries.append(entry)
    app.setting_save_button=app.write_button(row,text='共通条件を保存',style='Primary.TButton',command=lambda:app.run(lambda:app.confirmed_action(app.setting_checked,app.apply_settings)))
    app.setting_save_button.pack(side='left',padx=5)
    responsive_label(last,text='保存した条件は、次回のデータ取込から神戸・杉戸の全商品に適用します。変更履歴は保存します。条件変更後はデータを再取込・再計算してください。',style='Muted.TLabel').pack(fill='x',pady=4)
    responsive_label(parent,text='商品の賞味・消費期限や1PLのP数は「JAN・倉庫別PLマスタ」で設定します。出荷指示の再出力は「履歴」を使ってください。',style='Muted.TLabel').pack(fill='x',pady=8)
    # Legacy controls remain for old v2 data/API, hidden from the JAN workflow.
    app.expiry_frame=ttk.LabelFrame(parent,text='旧v2データの期限設定',padding=8)
    expiry=app.expiry_frame
    app.expiry_product=tk.StringVar();app.expiry_picker=ttk.Combobox(expiry,textvariable=app.expiry_product,state='readonly',width=18)
    ttk.Label(expiry,text='対象商品').pack(side='left');app.expiry_picker.pack(side='left');app.expiry_picker.bind('<<ComboboxSelected>>',app.select_expiry)
    app.expiry_type=tk.StringVar();ttk.Combobox(expiry,textvariable=app.expiry_type,values=('賞味期限','消費期限'),state='readonly',width=10).pack(side='left',padx=4)
    ttk.Label(expiry,text='製造からの期限日数').pack(side='left');app.expiry_days=tk.StringVar();ttk.Entry(expiry,textvariable=app.expiry_days,width=7).pack(side='left')
    app.write_button(expiry,text='期限を保存',command=lambda:app.run(lambda:app.confirmed_action(app.setting_checked,app.apply_expiry))).pack(side='left',padx=5)
    app.settings=tk.Text(parent,height=7,wrap='word',state='disabled')
    app.history_list=tk.Listbox(parent,height=6)
