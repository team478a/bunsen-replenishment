"""Read the inspected Muroo inventory layout. Never infer absent quantities."""
import base64
import hashlib
import io
import json
import re
import unicodedata
from datetime import date
from pathlib import Path

from .core import DataError, stamp, WAREHOUSES
from .requirements_data import SCHEMA, EVIDENCE, csv_text

READER_VERSION = 'muroo-current-total-v1'


def printed_time(report):
    from datetime import datetime
    return datetime.strptime(report['printed_at'],'%Y/%m/%d %H:%M:%S').strftime('%H:%M:%S')


def _norm(value):
    return unicodedata.normalize('NFKC', value).replace(' ', '').replace('\u3000', '')


def _number(value):
    if value is None:return None
    if not re.fullmatch(r'(?:0|[1-9][0-9]*|[1-9][0-9]{0,2}(?:,[0-9]{3})+)',value):
        raise ValueError('数量が0以上の整数ではありません: '+value)
    return int(value.replace(',',''))


def _cell(words, left, right, y):
    values=[_norm(w['text']) for w in words if left <= (w['x0']+w['x1'])/2 < right
            and abs((w['top']+w['bottom'])/2-y)<5]
    if not values:return None
    if len(values)!=1:raise ValueError('数量セルを一意に読み取れません')
    return _number(values[0])


def read_report(path, member=None):
    """Return immutable-source candidates. No candidate is a confirmed stock row."""
    from .input_guard import _signature
    path=Path(path);before=path.stat();signature=_signature(path)
    if member is None:
        if before.st_size>20*1024*1024:raise DataError(['PDFは20MB以下にしてください'])
        content=path.read_bytes()
        if path.read_bytes()!=content:raise DataError(['読取中にPDFが変更されました'])
    else:
        import zipfile
        try:
            with zipfile.ZipFile(path) as archive:
                matches=[i for i in archive.infolist() if i.filename==member]
                if len(matches)!=1 or matches[0].file_size>20*1024*1024:raise DataError(['ZIP内のPDF名重複または20MB上限を確認してください'])
                with archive.open(matches[0]) as stream:content=stream.read(20*1024*1024+1)
        except (zipfile.BadZipFile,RuntimeError,KeyError) as exc:raise DataError(['ZIP内のPDFを読み取れません: '+str(exc)]) from exc
    if signature!=_signature(path):raise DataError(['読取中にPDF/ZIPが変更されました'])
    result=read_bytes(content)
    result.update(path=str(path.resolve()),filename=member or path.name,member=member)
    return result


def read_bytes(content):
    import pdfplumber
    if len(content)>20*1024*1024 or not content.startswith(b'%PDF-'):raise DataError(['20MB以下のPDFが必要です'])
    report=dict(reader=READER_VERSION,sha256=hashlib.sha256(content).hexdigest(),
                content_base64=base64.b64encode(content).decode('ascii'),rows=[],issues=[],printed_pages=[])
    warehouse=None;stock_date=None;printed_at=None;pending=None;seen=set();current_product=None;index=0
    try:
        with pdfplumber.open(io.BytesIO(content)) as pdf:
            if not 1<=len(pdf.pages)<=50:raise ValueError('PDFは1〜50ページにしてください')
            report['pages']=len(pdf.pages)
            for index,page in enumerate(pdf.pages,1):
                words=page.extract_words();text=page.extract_text() or '';normal=_norm(text)
                if '在庫一覧受払' not in normal or not words:raise ValueError(f'{index}ページ: 未対応様式または画像PDFです')
                candidates=[w for w,name in [('KOBE','神戸支店'),('SUGITO','杉戸営業所')] if name in normal]
                if len(candidates)!=1:raise ValueError(f'{index}ページ: 営業所を判定できません')
                if warehouse and warehouse!=candidates[0]:raise ValueError('異なる倉庫のページが混在しています')
                warehouse=candidates[0]
                dates=re.findall(r'在庫集計日[:：](\d{4}/\d{2}/\d{2})',normal)
                if len(dates)!=1:raise ValueError(f'{index}ページ: 在庫集計日を判定できません')
                day=date.fromisoformat(dates[0].replace('/','-')).isoformat()
                if stock_date and day!=stock_date:raise ValueError('異なる在庫集計日が混在しています')
                stock_date=day
                numbers=re.findall(r'(\d+)/(\d+)ページ',normal)
                if numbers!=[(str(index),str(len(pdf.pages)))]:raise ValueError(f'{index}ページ: ページ欠落・重複・順序を確認してください')
                printed=re.findall(r'\d{4}/\d{2}/\d{2}\s+\d{1,2}:\d{2}:\d{2}',text)
                if len(printed)!=1:raise ValueError(f'{index}ページ: 作成日時を判定できません')
                # The inspected reports print a fresh timestamp on each page.
                report['printed_pages'].append(printed[0])
                if printed_at is None:printed_at=printed[0]
                current=[w for w in words if _norm(w['text'])=='現在']
                cin=[w for w in words if _norm(w['text'])=='C入']
                if len(current)!=1 or len(cin)!=1:raise ValueError(f'{index}ページ: 現在庫・C入の見出しを判定できません')
                x=current[0]['x0'];y=current[0]['bottom']
                sub=[w for w in words if w['top']>=y-1 and w['top']<y+20 and w['x0']>x-25 and _norm(w['text']) in ('ケース','バラ')]
                sub.sort(key=lambda w:w['x0'])
                if [_norm(w['text']) for w in sub]!=['ケース','バラ']:raise ValueError('現在庫のケース・バラ列を判定できません')
                left=sub[0]['x0']-4;split=(sub[0]['x1']+sub[1]['x0'])/2;right=sub[1]['x1']+4
                cx=(cin[0]['x0']+cin[0]['x1'])/2
                markers=[w for w in words if (w['x1']<cin[0]['x0']-30 and re.fullmatch(r'\d{6}|\d{13}',w['text'])) or _norm(w['text'])=='商品合計']
                markers.sort(key=lambda w:w['top'])
                for word in markers:
                    value=_norm(word['text']);cy=(word['top']+word['bottom'])/2
                    if value!='商品合計':
                        current_product=value
                        if len(value)!=13:raise ValueError(f'{index}ページ: 13桁JANを取得できません（補完不可）')
                        factor=_cell(words,cx-16,cx+16,cy)
                        name=' '.join(w['text'] for w in words if w['x1']<cin[0]['x0']-10 and w['x0']>word['x1'] and abs(w['top']-word['top'])<7)
                        if pending is None:pending=dict(product=value,name=name,p_per_case=factor,pages=[index],issues=[])
                        elif pending['product']!=value:raise ValueError(f'{index}ページ: 商品合計が欠落しています')
                        elif pending['p_per_case']!=factor:pending['issues'].append('商品内のC入が一致しません')
                        if index not in pending['pages']:pending['pages'].append(index)
                        if factor is None or factor<=0:pending['issues'].append('C入が未確認です')
                    else:
                        if pending is None:raise ValueError(f'{index}ページ: 商品合計に対応するJANがありません')
                        row=dict(pending,cases=_cell(words,left,split,cy),loose=_cell(words,split,right,cy))
                        if index not in row['pages']:row['pages'].append(index)
                        if row['product'] in seen:raise ValueError('同一JANの商品合計が複数あります（自動加算不可）')
                        seen.add(row['product']);report['rows'].append(row);pending=None
                page.close()
            if pending is not None:raise ValueError('最終商品の商品合計が欠落しています')
            if not report['rows']:raise ValueError('商品合計を読み取れませんでした')
    except Exception as exc:
        raise DataError([f'在庫PDF / 倉庫 {warehouse or "未確認"} / 集計日 {stock_date or "未確認"} / JAN {current_product or "未確認"} / {index}ページ: '+str(exc)]) from exc
    report.update(warehouse=warehouse,stock_date=stock_date,printed_at=printed_at)
    return report


def suggested_quantity(row, blank_policy='unknown'):
    if blank_policy not in ('unknown','loose_zero','all_zero'):raise DataError(['空欄の意味が不正です'])
    if row['issues'] or not row['p_per_case']:return None
    cases=row['cases'];loose=row['loose']
    if loose is None and blank_policy in ('loose_zero','all_zero'):loose=0
    if cases is None and blank_policy=='all_zero':cases=0
    if cases is None or loose is None:return None
    return cases*row['p_per_case']+loose


def write_reviewed(reports, decisions, products, operator, folder,scope=None):
    """Validate review and create a complete JAN inventory kit, without overwriting."""
    if not operator.strip() or set(reports)!=set(WAREHOUSES):raise DataError(['担当者・両倉庫の在庫PDFが必要です'])
    known={p['product'] for p in products}
    if not known or len(known)!=len(products):raise DataError(['確認済みJANマスタが必要です'])
    expected={(w,p) for w in WAREHOUSES for p in known}
    if set(decisions)!=expected:raise DataError(['倉庫・登録JANごとの数量確認が必要です'])
    inventory=[];evidence=[];audit=[]
    for warehouse in WAREHOUSES:
        entry=reports[warehouse];report=entry['report']
        content=base64.b64decode(report['content_base64'],validate=True)
        # Re-read the immutable bytes. Editable presentation data is never trusted.
        canonical=read_bytes(content)
        if any(report.get(k)!=canonical[k] for k in canonical):raise DataError(['読取結果が変更されています。PDFを再読取してください'])
        if report.get('path') and read_report(report['path'],report.get('member'))['sha256']!=canonical['sha256']:
            raise DataError(['元PDF/ZIPが変更されています。再読取してください'])
        if canonical['warehouse']!=warehouse:raise DataError(['倉庫とPDFの営業所が一致しません'])
        basis=entry['basis'];as_of=entry['as_of'];policy=entry['blank_policy']
        if policy not in ('unknown','loose_zero','all_zero'):raise DataError(['空欄の意味が不正です'])
        instant=stamp(as_of)
        from .core import JST
        if instant.astimezone(JST).date().isoformat()!=report['stock_date']:raise DataError(['在庫基準日時とPDFの集計日が一致しません'])
        if basis not in ('opening','closing'):raise DataError(['在庫の始業・終業を指定してください'])
        if 'time_origin' in entry:
            if basis!='closing' or entry['time_origin'] not in ('pdf_footer','manual'):raise DataError(['新規PDFフローは集計日反映済み・翌日から需要計算です'])
            if entry['time_origin']=='pdf_footer' and instant.astimezone(JST).strftime('%H:%M:%S')!=printed_time(canonical):raise DataError(['PDF左下時刻と採用時刻が不一致です。手修正として確認してください'])
        name=warehouse+'-report.pdf';source={r['product']:r for r in canonical['rows']}
        evidence.append(dict(source_pdf=name,sha256=canonical['sha256'],content_base64=canonical['content_base64']))
        for product in sorted(known):
            decision=decisions[(warehouse,product)];quantity=decision['quantity_p'];reason=decision['reason'].strip()
            if type(quantity) is not int or quantity<0:raise DataError([warehouse+' '+product+': 確認済みの0以上の整数Pが必要です'])
            row=source.get(product);suggested=suggested_quantity(row,policy) if row else None
            if quantity!=suggested and not reason:raise DataError([warehouse+' '+product+': 未読取・変更数量の確認根拠が必要です'])
            inventory.append(dict(warehouse=warehouse,product=product,quantity_p=quantity,as_of=as_of,basis=basis,source_pdf=name,source_sha256=canonical['sha256'],checker=operator.strip(),confirmed='yes'))
            audit.append(dict(warehouse=warehouse,product=product,source=row,suggested_p=suggested,quantity_p=quantity,reason=reason,blank_policy=policy))
    from .jan_catalog import master_csvs
    outputs=master_csvs(products)
    outputs.update({'inventory.csv':csv_text(SCHEMA['inventory'],inventory),'source_evidence.csv':csv_text(EVIDENCE,evidence)})
    review=dict(reader=READER_VERSION,operator=operator.strip(),reports=reports,decisions=audit)
    if scope is not None:
        from .product_scope import FILE,text
        outputs[FILE]=text(scope);review['excluded_products']=scope['excluded']
    outputs['pdf-review.json']=json.dumps(review,ensure_ascii=False,indent=2)
    created=[]
    try:
        for name,text in outputs.items():
            path=Path(folder)/name
            with path.open('x',encoding='utf-8-sig',newline='') as stream:created.append(path);stream.write(text)
    except Exception:
        for path in created:path.unlink(missing_ok=True)
        raise
    return created,review
