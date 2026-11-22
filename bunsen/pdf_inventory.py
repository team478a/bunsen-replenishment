"""Prepare unconfirmed transcription blanks and immutable PDF evidence."""
import base64
import hashlib
from pathlib import Path
from .core import DataError, WAREHOUSES, stamp
from .requirements_data import SCHEMA, EVIDENCE, csv_text
from .input_guard import _signature


def prepare_inventory(products, pdfs, as_of, basis, checker, folder):
    if not products or set(pdfs)!=set(WAREHOUSES) or basis not in ('opening','closing') or not checker.strip():
        raise DataError(['登録JAN・両倉庫のPDF・始業/終業・確認者が必要です'])
    stamp(as_of)
    sources=[];inventory=[]
    for warehouse in WAREHOUSES:
        path=Path(pdfs[warehouse]);before=_signature(path);content=path.read_bytes();after=_signature(path)
        if before!=after or path.read_bytes()!=content:raise DataError(['読取中にPDFが変わりました'])
        if len(content)>20*1024*1024 or not content.startswith(b'%PDF-'):raise DataError(['20MB以下のPDFを選択してください'])
        # Canonical names avoid accidentally merging two equally named warehouse reports.
        name=warehouse+'-report.pdf';digest=hashlib.sha256(content).hexdigest()
        sources.append(dict(source_pdf=name,sha256=digest,content_base64=base64.b64encode(content).decode('ascii')))
        for p in products:
            inventory.append(dict(warehouse=warehouse,product=p['product'],quantity_p='',as_of=as_of,basis=basis,
                                  source_pdf=name,source_sha256=digest,checker=checker.strip(),confirmed=''))
    outputs={'inventory.csv':csv_text(SCHEMA['inventory'],inventory),'source_evidence.csv':csv_text(EVIDENCE,sources)}
    created=[]
    try:
        for name,text in outputs.items():
            target=Path(folder)/name
            with target.open('x',encoding='utf-8-sig',newline='') as stream:created.append(target);stream.write(text)
    except Exception:
        for target in created:target.unlink(missing_ok=True)
        raise
    return created
