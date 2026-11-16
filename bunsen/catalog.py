"""Confirmed local product catalog, independent of inventory and forecasts."""
import csv
import io

from .core import DataError, ValidationIssue, V2_SCHEMA

FIELDS = V2_SCHEMA['products'].split(',')[:-1]+['expiry_type','shelf_life_days','confirmed']
EDITABLE = set(FIELDS)-{'confirmed'}


def validate_product(values):
    errors=[];result={}
    product=values.get('product') if isinstance(values,dict) else None
    def error(field,message):
        errors.append(ValidationIssue('products.csv',field,'MASTER_INVALID',message,product=product,phase='contract'))
    if not isinstance(values,dict) or set(values)!=EDITABLE:
        raise DataError(['商品マスタの登録項目が一致しません'])
    for field in ('product','name'):
        value=values[field]
        if not isinstance(value,str) or not value.strip() or any(ord(c)<32 for c in value) or (field=='product' and '|' in value):
            error(field,'空欄・制御文字は使用できません。商品コードに縦棒は使用できません')
        else:result[field]=value.strip()
    for field in ('p_per_ball','balls_per_case','cases_per_pl','shelf_life_days'):
        value=values[field]
        if type(value) is not int or value<=0:error(field,'確認済みの正整数を入力してください（推測・ゼロ補完不可）')
        else:result[field]=value
    for field,allowed in (('ship_unit',('PL','CASE')),('expiry_type',('best_before','use_by'))):
        if values[field] not in allowed:error(field,'確認済みの区分を選択してください')
        else:result[field]=values[field]
    if errors:raise DataError(errors)
    return dict(result,confirmed='yes')


def catalog_csv(products):
    stream=io.StringIO(newline='');writer=csv.DictWriter(stream,fieldnames=FIELDS,lineterminator='\n')
    writer.writeheader();writer.writerows(products)
    return stream.getvalue()


class CatalogService:
    def product_master(self):
        import json
        row=self.db.execute("SELECT id,payload FROM events WHERE kind='product_master_change' ORDER BY id DESC LIMIT 1").fetchone()
        return dict(revision=row[0],products=json.loads(row[1])['products']) if row else dict(revision=0,products=[])

    def save_product_master(self, values, reason, operator, acknowledged=False, *, expected_revision, create):
        from .service import encode
        self.ensure_writable()
        if acknowledged is not True or not isinstance(reason,str) or not reason.strip() or not isinstance(operator,str) or not operator.strip():
            raise DataError(['商品マスタの保存には確認担当者・変更理由・明示確認が必要です'])
        product=validate_product(values)
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            state=self.product_master()
            if type(expected_revision) is not int or expected_revision!=state['revision']:
                raise DataError(['商品マスタが別画面で更新されています。最新の登録内容を再表示してください'])
            before=next((p for p in state['products'] if p['product']==product['product']),None)
            if type(create) is not bool or (create and before is not None) or (not create and before is None):
                raise DataError(['商品コードが重複しているか編集対象がありません。追加/編集モードを確認してください'])
            products=sorted([p for p in state['products'] if p['product']!=product['product']]+[product],key=lambda p:p['product'])
            payload=dict(before=before,after=product,products=products,operator=operator.strip(),reason=reason.strip(),acknowledged=True,base_revision=state['revision'])
            cursor=self.db.execute('INSERT INTO events(at,kind,payload) VALUES(?,?,?)',(self.clock().isoformat(),'product_master_change',encode(payload)))
            revision=cursor.lastrowid
        return dict(revision=revision,products=products)

    def assert_product_master_current(self, data):
        revision=getattr(data,'product_master_revision',None)
        if revision is not None and (type(revision) is not int or revision!=self.product_master()['revision']):
            raise DataError(['登録商品マスタが変更されています。再取込・再計算・再確認してください'])

    def export_product_master(self, path):
        from pathlib import Path
        state=self.product_master()
        if not state['products']:raise DataError(['登録商品マスタがありません'])
        path=Path(path)
        # Exclusive creation preserves input files and previously exported snapshots.
        created=False
        try:
            with path.open('x',encoding='utf-8-sig',newline='') as stream:
                created=True;stream.write(catalog_csv(state['products']))
        except Exception:
            if created:path.unlink(missing_ok=True)
            raise
        return path.resolve()
