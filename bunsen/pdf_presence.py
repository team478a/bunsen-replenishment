"""Per-report absent JANs are skipped, never converted to zero or master exclusions."""
import base64,csv,hashlib,io,json,re
from .core import DataError,WAREHOUSES
FILE='pdf-presence.csv'
HEADERS='warehouse,missing_json,source_pdf,sha256'

def verify(text,evidence_text,inventory=None):
    from .pdf_reader import read_bytes
    from .requirements_data import EVIDENCE,csv_text
    reader=csv.DictReader(io.StringIO(text),strict=True);rows=list(reader)
    if reader.fieldnames!=HEADERS.split(',') or len(rows)!=2 or {r.get('warehouse') for r in rows}!=set(WAREHOUSES):raise DataError(['PDF未掲載の証跡が不正です'])
    evidence=csv.DictReader(io.StringIO(evidence_text),strict=True)
    if evidence.fieldnames!=EVIDENCE.split(','):raise DataError(['PDF原本証跡が必要です'])
    sources={}
    for r in evidence:
        if r['source_pdf'] in sources:raise DataError(['PDF原本証跡が重複しています'])
        sources[r['source_pdf']]=r
    skipped=set()
    for r in rows:
        if set(r)!=set(HEADERS.split(',')) or any(v is None for v in r.values()):raise DataError(['PDF未掲載の証跡の列が不正です'])
        missing=json.loads(r['missing_json'])
        if not isinstance(missing,list) or any(not isinstance(p,str) or not re.fullmatch(r'[0-9]{13}',p) for p in missing) or missing!=sorted(set(missing)):raise DataError(['PDF未掲載JANの一覧が不正です'])
        source=sources.get(r['source_pdf'])
        if not source or source['sha256']!=r['sha256']:raise DataError(['PDF未掲載の原本ハッシュが不一致です'])
        content=base64.b64decode(source['content_base64'],validate=True)
        if hashlib.sha256(content).hexdigest()!=r['sha256']:raise DataError(['PDF未掲載の原本が変更されています'])
        report=read_bytes(content)
        if inventory is not None:
            from .core import JST,stamp
            for item in inventory:
                if item['warehouse']==r['warehouse'] and (item['source_pdf']!=r['source_pdf'] or item['source_sha256']!=r['sha256'] or stamp(item['as_of']).astimezone(JST).date().isoformat()!=report['stock_date']):raise DataError(['PDF未掲載の原本と在庫行の原本・集計日が不一致です'])
        if report['warehouse']!=r['warehouse'] or set(missing)&{p['product'] for p in report['rows']}:raise DataError(['PDF掲載商品を未掲載として除外できません'])
        skipped.update((r['warehouse'],p) for p in missing)
    if set(sources)!={r['source_pdf'] for r in rows}:raise DataError(['未掲載証跡に対応しないPDF原本があります'])
    if csv_text(HEADERS,rows)!=text:raise DataError(['PDF未掲載の証跡形式が不一致です'])
    return skipped
