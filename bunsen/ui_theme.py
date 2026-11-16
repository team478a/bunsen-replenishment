"""Shared, offline Tk presentation; business validation stays in services."""
import tkinter as tk
from tkinter import ttk

INK = '#203549'
MUTED = '#52677a'
BG = '#f2f5f8'
TEAL = '#006c70'


def apply_theme(root):
    style = ttk.Style(root)
    style.theme_use('clam')
    root.configure(background=BG)
    root.option_add('*Font', ('Yu Gothic UI', 11))
    style.configure('.', font=('Yu Gothic UI', 11), background=BG, foreground=INK)
    style.configure('TFrame', background=BG)
    style.configure('TLabel', background=BG)
    style.configure('Title.TLabel', font=('Yu Gothic UI', 20, 'bold'), foreground=INK)
    style.configure('Muted.TLabel', foreground=MUTED)
    style.configure('Status.TLabel', foreground=TEAL, font=('Yu Gothic UI', 11, 'bold'))
    style.configure('TLabelframe', bordercolor='#cdd8e1', relief='solid')
    style.configure('TLabelframe.Label', foreground=TEAL, font=('Yu Gothic UI', 11, 'bold'))
    style.configure('TButton', padding=(12, 7), background='#ffffff', bordercolor='#b8c8d4')
    style.map('TButton', background=[('active', '#e1eef0')], foreground=[('disabled', '#74818b')])
    style.configure('Primary.TButton', background=TEAL, foreground='#ffffff', bordercolor=TEAL)
    style.map('Primary.TButton', background=[('disabled', '#dbe3e9'), ('active', '#00575b')],
              foreground=[('disabled', '#74818b'), ('!disabled', '#ffffff')])
    style.configure('TEntry', padding=5, fieldbackground='#ffffff')
    style.configure('TCombobox', padding=4, fieldbackground='#ffffff')
    style.configure('TCheckbutton', padding=(0, 4))
    style.configure('TNotebook', background=BG, borderwidth=0)
    style.configure('TNotebook.Tab', padding=(18, 9), background='#e3eaf0')
    style.map('TNotebook.Tab', background=[('selected', '#ffffff')], foreground=[('selected', TEAL)])
    style.configure('Treeview', background='#ffffff', fieldbackground='#ffffff', foreground=INK,
                    rowheight=34, borderwidth=0, font=('Yu Gothic UI', 11))
    style.configure('Treeview.Heading', background='#e4edf3', foreground=INK,
                    padding=(6, 8), font=('Yu Gothic UI', 10, 'bold'))
    style.map('Treeview', background=[('selected', '#d4ecec')], foreground=[('selected', '#143f43')])


def section(parent, title, **pack_options):
    frame = ttk.LabelFrame(parent, text=title, padding=(10, 6))
    frame.pack(fill='x', pady=5, **pack_options)
    return frame


def table_scrollbars(tree):
    """Keep scrollbars within the table, including wide product/date columns."""
    parent = tree.master
    parent.columnconfigure(0, weight=1)
    parent.rowconfigure(0, weight=1)
    tree.grid(row=0, column=0, sticky='nsew')
    vertical = ttk.Scrollbar(parent, orient='vertical', command=tree.yview)
    vertical.grid(row=0, column=1, sticky='ns')
    horizontal = ttk.Scrollbar(parent, orient='horizontal', command=tree.xview)
    horizontal.grid(row=1, column=0, sticky='ew')
    tree.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
    tree.tag_configure('alternate', background='#f1f6f9')
    tree.tag_configure('attention', background='#fff0d5', foreground='#7b4400')
    for key in tree['columns']:
        tree.column(key, minwidth=65, stretch=False,
                    anchor='e' if key in ('inventory', 'demand', 'safety', 'required', 'suggested',
                                         'quantity', 'final', 'pallets', 'remaining', 'case', 'pl') else 'w')


def text_panel(widget):
    widget.configure(background='#ffffff', foreground=INK, relief='flat', borderwidth=0,
                     padx=10, pady=8, font=('Yu Gothic UI', 11), selectbackground='#cce7e8')
    scrollbar = ttk.Scrollbar(widget.master, orient='vertical', command=widget.yview)
    scrollbar.pack(side='right', fill='y')
    widget.configure(yscrollcommand=scrollbar.set)


def responsive_label(parent, **options):
    label = ttk.Label(parent, wraplength=1000, **options)
    parent.bind('<Configure>', lambda event: label.configure(wraplength=max(200, event.width-24)), add='+')
    return label


def brand(app):
    header = tk.Frame(app.root, background=INK, padx=18, pady=10)
    header.pack(fill='x', before=app.tabs)
    tk.Label(header, text='BUNSEN  /  補充出荷指示', background=INK, foreground='white',
             font=('Yu Gothic UI', 16, 'bold')).pack(side='left')
    app.mode_badge = tk.Label(header, background=INK, foreground='#b8e8e6', font=('Yu Gothic UI', 11, 'bold'))
    app.mode_badge.pack(side='right')
    app.tabs.pack_configure(padx=12, pady=(10, 12))
    # Daily tasks first; maintenance and inspection stay out of the working path.
    for index, tab in enumerate((app.daily.master.master, app.delivery_tab.master.master, app.master_tab.master.master)):
        app.tabs.insert(index, tab)
    app.tabs.tab(app.daily.master.master, text='補充出荷')
    app.tabs.tab(app.settings.master.master.master, text='管理・履歴')
    app.tabs.select(app.daily.master.master)
    app.root.minsize(1000, 650)
    app.page_viewports = {str(page.master.master): page.master for page in
                          (app.daily, app.delivery_tab, app.master_tab, app.settings.master)}

    def wheel(event):
        if event.widget.winfo_toplevel() != app.root:
            return
        # Tables and text have their own scrollbars and wheel behavior.
        if isinstance(event.widget, (tk.Text, tk.Listbox, ttk.Treeview, ttk.Combobox)):
            return
        viewport = app.page_viewports.get(app.tabs.select())
        if viewport and viewport.bbox('all')[3] > viewport.winfo_height():
            viewport.yview_scroll(-int(event.delta / 120), 'units')
    app.root.bind_all('<MouseWheel>', wheel, add='+')


def scroll_page(page):
    """Create a scrollable content frame before constructing its widgets."""
    viewport = tk.Canvas(page, background=BG, highlightthickness=0, borderwidth=0)
    scrollbar = ttk.Scrollbar(page, orient='vertical', command=viewport.yview)
    scrollbar.pack(side='right', fill='y')
    viewport.pack(side='left', fill='both', expand=True)
    viewport.configure(yscrollcommand=scrollbar.set)
    body = ttk.Frame(viewport)
    item = viewport.create_window(0, 0, window=body, anchor='nw')
    body.bind('<Configure>', lambda _: viewport.configure(scrollregion=viewport.bbox('all')))
    viewport.bind('<Configure>', lambda event: viewport.itemconfigure(item, width=event.width))

    def reveal_focus(event):
        widget = event.widget
        ancestor = widget
        while ancestor is not None and ancestor != body:
            ancestor = getattr(ancestor, 'master', None)
        bounds = viewport.bbox('all')
        if ancestor is None or not bounds or not widget.winfo_ismapped():
            return
        top = widget.winfo_rooty() - body.winfo_rooty()
        bottom = top + widget.winfo_height()
        visible_top = viewport.canvasy(0)
        if top < visible_top or bottom > visible_top + viewport.winfo_height():
            viewport.yview_moveto(max(0, top - 12) / max(1, bounds[3]))
    page.winfo_toplevel().bind('<FocusIn>', reveal_focus, add='+')
    return body
