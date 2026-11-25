"""Plain-language replenishment settings; never suggest business defaults."""
import tkinter as tk
from tkinter import ttk
from .ui_theme import section,responsive_label
from .core import WAREHOUSES

FIELDS=(
    ('cycle_days','補充する間隔（日）','何日ごとに補充するか。1以上の整数を入力します。'),
    ('lead_days','到着までの日数（日）','出荷日から到着までの暦日数（休日を含む）。0は出荷当日です。'),
    ('safety_days','安全在庫の日数（日）','各日の翌日から何日分の予測需要を残すか。0は安全在庫なしです。'),
    ('coverage_days','補充でまかなう日数（日）','到着日から何日分を補充の対象にするか。1以上の整数です。'),
)

def build_settings_page(app,parent):
    ttk.Label(parent,text='補充の条件を設定する',style='Title.TLabel').pack(anchor='w',pady=8)
    responsive_label(parent,text='神戸・杉戸で条件を分けて設定します。倉庫別の基本条件を保存します。必要な商品だけ、下の「商品別の安全在庫」で日数を変更できます。条件CSVは不要です。').pack(fill='x',pady=5)
    app.setting_status=tk.StringVar(value='神戸・杉戸の条件をそれぞれ入力して保存してください。')
    responsive_label(parent,textvariable=app.setting_status,style='Status.TLabel').pack(fill='x',pady=6)
    conditions=section(parent,'補充条件（倉庫別・商品共通）')
    app.setting_values={};app.setting_entries=[];app.setting_warehouses={}
    for column,w in enumerate(WAREHOUSES,1):
        var=tk.StringVar();app.setting_warehouses[w]=var
        ttk.Label(conditions,textvariable=var,style='Status.TLabel',wraplength=210).grid(row=0,column=column,padx=12,pady=8,sticky='w')
    for row,(key,label,help_text) in enumerate(FIELDS,1):
        ttk.Label(conditions,text=label,width=24).grid(row=row,column=0,padx=4,pady=7,sticky='w')
        for column,w in enumerate(WAREHOUSES,1):
            var=tk.StringVar();app.setting_values[(w,key)]=var
            entry=ttk.Entry(conditions,textvariable=var,width=8,state='normal');entry.grid(row=row,column=column,padx=12,pady=7,sticky='w');app.setting_entries.append(entry)
        ttk.Label(conditions,text=help_text,style='Muted.TLabel',wraplength=350).grid(row=row,column=3,padx=8,pady=7,sticky='w')
    note=ttk.Frame(parent);note.pack(fill='x')
    responsive_label(note,text='実際の対象期間は「補充する間隔」と「補充でまかなう日数」の大きい方です。安全在庫は日別の需要予測から計算します。',style='Muted.TLabel').pack(fill='x',pady=6)
    last=section(parent,'確認して保存')
    app.setting_reason=tk.StringVar();app.setting_checked=tk.BooleanVar(value=False)
    ttk.Label(last,text='設定・変更理由（条件の確認根拠）').pack(anchor='w')
    reason=ttk.Entry(last,textvariable=app.setting_reason);reason.pack(fill='x',pady=4);app.setting_entries.append(reason)
    responsive_label(last,text='運用見直し、配送条件変更など、確認した根拠を記入してください。初期表示やサンプルの数値をそのまま正式値にしないでください。',style='Muted.TLabel').pack(fill='x',pady=4)
    row=ttk.Frame(last);row.pack(fill='x',pady=5)
    ttk.Label(row,text='設定を確認した担当者').pack(side='left')
    entry=ttk.Entry(row,textvariable=app.operator,width=18);entry.pack(side='left',padx=8);app.setting_entries.append(entry)
    app.setting_save_button=app.write_button(row,text='倉庫別の条件を保存',style='Primary.TButton',command=lambda:app.run(lambda:app.confirmed_action(app.setting_checked,app.apply_settings)))
    app.setting_save_button.pack(side='left',padx=5)
    responsive_label(last,text='保存した基本条件は、次回の取込から該当倉庫の全商品に適用します。商品別設定がある場合は、その安全在庫日数を優先します。変更履歴は保存します。条件変更後はデータを再取込・再計算してください。',style='Muted.TLabel').pack(fill='x',pady=4)
    responsive_label(parent,text='倉庫番号KOBE・SUGITOはアプリ内の識別です。予測CSVの倉庫番号は登録済みの対応だけを表示します。商品の賞味・消費期限や1PLのP数は「JAN・倉庫別PLマスタ」で設定します。出荷指示の再出力は「履歴」を使ってください。',style='Muted.TLabel').pack(fill='x',pady=8)
    from .product_safety_ui import build_product_safety
    build_product_safety(app,parent)
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
