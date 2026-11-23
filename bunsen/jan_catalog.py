"""Explicitly confirmed JAN masters with warehouse-specific physical pallets."""
import json
from .core import DataError
from .requirements_data import SCHEMA, csv_text

FIELDS=('product','name','expiry_type','shelf_life_days','kobe_p_per_pl','sugito_p_per_pl')


def validate_jan(values):
    if not isinstance(values,dict) or set(values)!=set(FIELDS):raise DataError(['JANマスタ項目が一致しません'])
    result=dict(values)
    jan=result['product']
    if not isinstance(jan,str) or not jan.isascii() or not jan.isdecimal() or len(jan)!=13:raise DataError(['JANは13桁の文字列です'])
    if not isinstance(result['name'],str) or not result['name'].strip() or any(ord(c)<32 for c in result['name']):raise DataError(['品名を確認してください'])
    result['name']=result['name'].strip()
    if result['expiry_type'] not in ('best_before','use_by'):raise DataError(['期限の種別を確認してください'])
    for field in FIELDS[3:]:
        if type(result[field]) is not int or result[field]<=0:raise DataError([field+': 確認済みの正整数が必要です'])
    return result


def master_csvs(products):
    goods=[];pallets=[]
    for p in products:
        validate_jan(p)
        goods.append(dict(product=p['product'],name=p['name'],expiry_type=p['expiry_type'],shelf_life_days=p['shelf_life_days'],confirmed='yes'))
        for warehouse,key in [('KOBE','kobe_p_per_pl'),('SUGITO','sugito_p_per_pl')]:
            pallets.append(dict(warehouse=warehouse,product=p['product'],p_per_pl=p[key],confirmed='yes'))
    return {'products.csv':csv_text(SCHEMA['products'],goods),'pallets.csv':csv_text(SCHEMA['pallets'],pallets)}


class JanCatalog:
    def import_jan_csv(self,report,operator,reason,acknowledged,expected_revision,mode='merge'):
        from .jan_csv import analyse, read_csv
        from .service import encode
        import base64
        self.ensure_writable()
        fresh=analyse(base64.b64decode(report['source_base64'],validate=True))
        if any(report.get(k)!=v for k,v in fresh.items()):raise DataError(['CSV候補が変更されています。再読取してください'])
        if fresh['errors']:raise DataError(fresh['errors'])
        if report.get('path') and read_csv(report['path'])['source_sha256']!=fresh['source_sha256']:raise DataError(['元CSVが変更されています。再読取してください'])
        if not isinstance(operator,str) or not operator.strip() or not isinstance(reason,str) or not reason.strip() or acknowledged is not True:raise DataError(['担当者・登録変更根拠・確認保存が必要です'])
        with self.db:
            self.db.execute('BEGIN IMMEDIATE');state=self.jan_master()
            if type(expected_revision) is not int or state['revision']!=expected_revision:raise DataError(['JANマスタが更新されています。CSVを再読取してください'])
            from .product_scope import csv_excluded
            excluded=csv_excluded(state,{p['product'] for p in fresh['products']},operator,reason,mode)
            products={p['product']:p for p in state['all_products']};changes=[]
            for p in fresh['products']:
                changes.append(dict(before=products.get(p['product']),after=p));products[p['product']]=p
            from .product_scope import validate as validate_scope
            validate_scope(excluded,set(products))
            payload=dict(products=sorted(products.values(),key=lambda p:p['product']),before=state['all_products'],after=fresh['products'],changes=changes,mode=mode,excluded=excluded,
                         base_revision=state['revision'],operator=operator.strip(),reason=reason.strip(),acknowledged=True,source='csv_import',csv_report=report)
            cur=self.db.execute('INSERT INTO events(at,kind,payload) VALUES(?,?,?)',(self.clock().isoformat(),'jan_master_change',encode(payload)))
        return self.jan_master()

    def set_jan_active(self,products,active,operator,reason,acknowledged,expected_revision):
        from .service import encode
        self.ensure_writable()
        if type(active) is not bool or not isinstance(products,list) or not products or not all(isinstance(p,str) for p in products) or len(set(products))!=len(products):raise DataError(['対象JANと対象/対象外の指定が必要です'])
        if acknowledged is not True or any(not isinstance(v,str) or not v.strip() or any(ord(c)<32 for c in v) for v in (operator,reason)):raise DataError(['対象変更の担当者・理由・確認保存が必要です'])
        with self.db:
            self.db.execute('BEGIN IMMEDIATE');state=self.jan_master();known={p['product'] for p in state['all_products']}
            if type(expected_revision) is not int or state['revision']!=expected_revision:raise DataError(['JANマスタが更新されています。最新一覧を再表示してください'])
            if set(products)-known:raise DataError(['未登録JANの対象設定は変更できません'])
            excluded=dict(state['excluded'])
            for p in products:
                if active:excluded.pop(p,None)
                else:excluded[p]=dict(operator=operator.strip(),reason=reason.strip())
            payload=dict(source='scope_change',products=state['all_products'],excluded=excluded,before_excluded=state['excluded'],targets=products,active=active,
                         base_revision=state['revision'],operator=operator.strip(),reason=reason.strip(),acknowledged=True)
            self.db.execute('INSERT INTO events(at,kind,payload) VALUES(?,?,?)',(self.clock().isoformat(),'jan_master_change',encode(payload)))
        return self.jan_master()

    def save_pdf_inventory(self,reports,decisions,operator,acknowledged,expected_revision,folder):
        from .pdf_reader import write_reviewed
        from .service import encode
        from .core import stamp
        self.ensure_writable()
        if acknowledged is not True:raise DataError(['PDF読取結果の担当者確認が必要です'])
        created=[]
        try:
            with self.db:
                self.db.execute('BEGIN IMMEDIATE');state=self.jan_master()
                if state['revision']!=expected_revision:raise DataError(['JANマスタが変更されています。PDF確認画面を開き直してください'])
                for entry in reports.values():
                    if stamp(entry['as_of'])>self.clock():raise DataError(['未来の在庫基準日時は使用できません'])
                created,review=write_reviewed(reports,decisions,state['products'],operator,folder,scope=state if state['excluded'] else None)
                review.update(jan_master_revision=state['revision'],acknowledged=True)
                self.db.execute('INSERT INTO events(at,kind,payload) VALUES(?,?,?)',
                                (self.clock().isoformat(),'pdf_inventory_review',encode(review)))
        except Exception:
            for path in created:path.unlink(missing_ok=True)
            raise
        return created

    def jan_master(self):
        row=self.db.execute("SELECT id,payload FROM events WHERE kind='jan_master_change' ORDER BY id DESC LIMIT 1").fetchone()
        payload=json.loads(row[1]) if row else {};all_products=payload.get('products',[]);excluded=payload.get('excluded',{})
        return dict(revision=row[0] if row else 0,products=[p for p in all_products if p['product'] not in excluded],all_products=all_products,excluded=excluded)

    def save_jan_master(self,values,operator,reason,acknowledged,expected_revision,create):
        from .service import encode
        self.ensure_writable();p=validate_jan(values)
        if not isinstance(operator,str) or not operator.strip() or not isinstance(reason,str) or not reason.strip() or acknowledged is not True:
            raise DataError(['担当者・変更根拠・明示確認が必要です'])
        with self.db:
            self.db.execute('BEGIN IMMEDIATE');state=self.jan_master()
            if type(expected_revision) is not int or state['revision']!=expected_revision:raise DataError(['JANマスタが更新されています。最新の一覧を再表示してください'])
            before=next((x for x in state['all_products'] if x['product']==p['product']),None)
            if type(create) is not bool or (create and before is not None) or (not create and before is None):raise DataError(['JANの重複または編集対象なし'])
            products=sorted([x for x in state['all_products'] if x['product']!=p['product']]+[p],key=lambda x:x['product'])
            payload=dict(products=products,before=before,after=p,excluded=state['excluded'],base_revision=state['revision'],operator=operator.strip(),reason=reason.strip(),acknowledged=True)
            cur=self.db.execute('INSERT INTO events(at,kind,payload) VALUES(?,?,?)',(self.clock().isoformat(),'jan_master_change',encode(payload)))
        return dict(revision=cur.lastrowid,products=products)

    def assert_jan_current(self,data):
        from .product_scope import FILE,parse
        if FILE in data.raw:
            scope=parse(data.raw[FILE]);state=self.jan_master()
            if scope!=dict(revision=state['revision'],excluded=state['excluded']):raise DataError(['補充対象JANが更新されています。再取込・再計算してください'])
        rev=getattr(data,'jan_master_revision',None)
        if rev is not None and (type(rev) is not int or rev!=self.jan_master()['revision']):raise DataError(['JAN・倉庫別PLマスタが更新されています。再取込・再計算・再確認してください'])
