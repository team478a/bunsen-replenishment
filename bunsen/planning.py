"""Reapply explicit instructions to newly calculated evidence."""
import copy

from .core import DataError, LABELS, allocation_label, allocations


def keyed(lines):
    result = {}
    for row in lines:
        key = (row['warehouse'], row['product'])
        if key in result:
            raise DataError([f'倉庫={key[0]} 商品={key[1]}: 指示明細の重複'])
        result[key] = row
    return result


def overlay(calculated, instructions):
    if calculated and calculated[0].get('workflow') == 2:
        from .replenishment import overlay as v2_overlay
        return v2_overlay(calculated,instructions)
    current, requested = keyed(calculated), keyed(instructions)
    if current.keys() != requested.keys():
        errors = []
        for w, p in sorted(current.keys() - requested.keys()):
            errors.append(f'倉庫={w} 商品={p}: 現在データに追加された明細。仮指示への自動追加はしません')
        for w, p in sorted(requested.keys() - current.keys()):
            errors.append(f'倉庫={w} 商品={p}: 現在データにない指示明細。自動削除せず照合停止')
        raise DataError(errors)
    result = copy.deepcopy(calculated)
    for row in result:
        instruction = requested[(row['warehouse'], row['product'])]
        qty = instruction['quantity_p']
        if type(qty) is not int or qty < 0:
            raise DataError(['数量は0以上の整数必須'])
        row.update(quantity_p=qty, lot=instruction['lot'])
        if 'allocations' in instruction:
            row['allocations'] = allocations(instruction)
    return result


def compare(draft, data, lines):
    original = keyed(draft['lines'])
    # This also prevents rendering incomplete or mismatched comparisons.
    overlay(lines, draft['lines'])
    rows = []
    for line in lines:
        old = original[(line['warehouse'], line['product'])]
        rows.append(dict(warehouse=line['warehouse'], product=line['product'],
                         draft_quantity_p=old['quantity_p'], current_quantity_p=line['quantity_p'],
                         current_suggested_p=line['suggested_p'],
                         draft_arrival=old['arrival'], current_arrival=line['arrival'],
                         draft_lots=allocation_label(old), current_lots=allocation_label(line)))
    changed = [name for name, table in data.tables.items()
               if sorted(table, key=lambda r: repr(sorted(r.items()))) != sorted(draft['tables'].get(name, []), key=lambda r: repr(sorted(r.items())))]
    return dict(number=draft['number'], digest=draft['digest'], at=draft['at'],
                current_digest=data.digest, changed_tables=changed, rows=rows)


def comparison_text(reference, warehouse=None):
    labels = {'products': '商品マスタ', 'warehouses': '配送設定', 'policies': '補充設定',
              'inventory': '配送先在庫', 'forecast': '需要予測', 'transit': '輸送中',
              'vehicles': '車両設定', 'dry_stock': '乾麺現物在庫'}
    labels['replenishment'] = 'ムロオ補充条件'
    result = [f'再開元仮指示: {reference["number"]}（{reference["at"]}）',
              f'元データ {reference["digest"][:16]} → 現在データ {reference["current_digest"][:16]}',
              '変更された入力区分: ' + ('、'.join(labels[name] for name in reference['changed_tables']) or 'なし')]
    for row in reference['rows']:
        if warehouse is not None and row['warehouse'] != warehouse:
            continue
        result.append(f'{LABELS[row["warehouse"]]} {row["product"]}: 仮指示 {row["draft_quantity_p"]}P → 現在推奨 {row["current_suggested_p"]}P／現在指示 {row["current_quantity_p"]}P。到着 {row["draft_arrival"]} → {row["current_arrival"]}。ロット {row["draft_lots"]} → {row["current_lots"]}')
    return '\n'.join(result)
