"""Actionable UI messages; structured validation issues remain unchanged in history."""
import re
import sqlite3
from collections import defaultdict
from .core import DataError, LABELS
INPUT_CODES={'INPUT_CHANGED','INPUT_RECHECK_FAILED'}
DETAIL_HINT='対象商品などの一覧は、画面の「計算根拠・取込情報」に表示しています。'
FIELDS={'quantity_p':'数量（パック）','as_of':'データの基準日時','date':'対象日',
        'product':'JAN・商品コード','warehouse':'倉庫','safety_days':'安全在庫の日数',
        'lead_days':'到着までの日数','confirmed':'確認状況','status':'予測の確認状況'}
GUIDANCE={
    'INVENTORY_AFTER_DISPATCH':('在庫の日付と出荷日が合っていません。',
        '在庫集計日・出荷日・到着までの日数を確認してください。在庫の基準と、需要を差し引く開始日が合っている必要があります。'),
    'FORECAST_AFTER_DISPATCH':('出荷日より後に作成された需要予測が含まれています。',
        '出荷日以前に作成された予測を取り込むか、出荷日が正しいか確認してください。'),
    'PROVISIONAL_FORECAST':('未確定の需要予測が含まれています。',
        '正式な指示書を作成するには、確認済みの予測を取り込み直してください。'),
    'DATE_RANGE':('補充条件の日数が大きすぎて、日付を計算できません。',
        '「補充条件」で到着までの日数・補充間隔・まかなう日数・安全在庫の日数を確認してください。')}


def _context(issue):
    parts=[]
    if issue.warehouse:parts.append(LABELS.get(issue.warehouse,'乾麺倉庫' if issue.warehouse=='DRY' else issue.warehouse))
    if issue.product:parts.append('JAN・商品コード '+issue.product)
    if issue.date:parts.append(issue.date)
    parts.append('ファイル '+issue.filename)
    if issue.line is not None:parts.append(f'{issue.line}行目')
    if issue.field:parts.append(FIELDS.get(issue.field,'入力内容'))
    return ' / '.join(parts)


def _period(issue):
    # Only interpret the known calculation message. Never invent the missing end date.
    match=re.fullmatch(r'(\d{4}-\d{2}-\d{2})から(\d{4}-\d{2}-\d{2})の予測欠損',issue.message)
    return f'{match[1]}〜{match[2]}' if match else (issue.date or '対象日の詳細を確認')


def _issue_text(issue):
    if issue.code in GUIDANCE:
        reason,action=GUIDANCE[issue.code]
        return reason+'\n  '+_context(issue)+'\n  '+action
    if issue.code=='FORECAST_MISSING':
        return '需要予測が足りません：'+_period(issue)+'\n  '+_context(issue)
    return issue.message+'\n  '+_context(issue)


def _plain_errors(error):
    structured={str(i) for i in error.issues}
    return [value for value in error.errors if value not in structured]


def _forecast_message(issues):
    lines=['現在の補充条件で計算するには、次の期間の需要予測が必要です。']
    groups=defaultdict(set)
    for issue in issues:groups[(issue.warehouse,_period(issue))].add(issue.product)
    for (warehouse,period),products in list(groups.items())[:6]:
        lines.append(f'・{LABELS.get(warehouse,warehouse or "倉庫未指定")}：{period}の予測が不足（{len(products)}商品）')
    if len(groups)>6:lines.append(f'ほか{len(groups)-6}件の不足期間があります。')
    lines.extend(['','対処方法：','不足期間を含む予測CSVを取り込み直し、「データを開いて計算」を押してください。',
        '安全在庫の日数は、今回の補充でまかなう期間の後に追加されます。',
        '補充条件が業務上正しいかも確認してください。不足分を0や別の日の数量で埋めないでください。'])
    return '\n'.join(lines)


def _system_error(error):
    if isinstance(error,PermissionError):
        return 'ファイルを開けませんでした','ファイルを開く、または保存する権限がありません。\nExcelやPDFを開いている場合は閉じ、書き込み可能な作業フォルダーを選んで再実行してください。'
    if isinstance(error,FileNotFoundError):
        return 'ファイルが見つかりません','選択したファイルやフォルダーが移動・削除されていないか確認し、選び直してください。'
    if isinstance(error,(OSError,sqlite3.Error)):
        return 'データを読み書きできませんでした','保存先の接続・空き容量・書き込み権限を確認してください。解決しない場合は、画面の詳細を担当者へお知らせください。'
    return '入力内容を確認してください','日付や数量などの入力形式を確認してください。解決しない場合は、画面の詳細を担当者へお知らせください。'

def describe_error(error):
    if not isinstance(error,DataError):return _system_error(error)
    issues=error.issues if isinstance(error,DataError) else []
    missing=[i for i in issues if i.code=='FORECAST_MISSING']
    if missing:
        others=[_issue_text(i) for i in issues if i.code!='FORECAST_MISSING']+_plain_errors(error)
        lines=[_forecast_message(missing)]
        if others:
            lines.extend(['','あわせて確認する内容：']+others[:3])
            if len(others)>3:lines.append(f'ほか{len(others)-3}件あります。')
        lines.extend(['',DETAIL_HINT])
        return '需要予測が足りないため、計算できません','\n'.join(lines)
    targets=[i for i in issues if i.code in INPUT_CODES]
    if not targets:
        entries=[_issue_text(i) for i in issues]+_plain_errors(error)
        lines=entries[:4]
        if len(entries)>4:lines.append(f'ほか{len(entries)-4}件あります。')
        if issues or len(entries)>4:lines.extend(['',DETAIL_HINT])
        return '入力データを確認してください','\n'.join(lines) or '入力データと補充条件を確認してください。'
    lines=['入力ファイルを安全に読み込めなかったため、処理を止めました。',
           '読み込み中の変更、移動、またはファイルを開けなかった可能性があります。',
           '', '対象ファイルと理由：']
    for issue in targets[:6]:lines.extend(['・'+issue.filename,'  '+issue.message])
    if len(targets)>6:lines.append(f'ほか{len(targets)-6}件のファイル確認があります。')
    lines.extend(['','対処方法：','1. 対象ファイルを編集している場合は、保存して閉じてください。',
                  '2. ファイルの移動・コピー・同期が終わってから、もう一度「データを開いて計算」を押してください。',
                  '3. 繰り返す場合は、入力データ一式を別の作業フォルダーへコピーして、そちらを選んでください。'])
    others=[_issue_text(i) for i in issues if i not in targets]+_plain_errors(error)
    if others:lines.extend(['','あわせて確認する内容：']+others[:3])
    if len(others)>3:lines.append(f'ほか{len(others)-3}件あります。')
    lines.extend(['',DETAIL_HINT])
    return '入力ファイルを読み込めませんでした','\n'.join(lines)


def error_details(error):
    """Full scrollable list and original support evidence; do not alter stored errors."""
    title,message=describe_error(error)
    lines=[title,message]
    if isinstance(error,DataError):
        lines.extend(['','対象の一覧（省略なし）：'])
        lines.extend(_issue_text(i) for i in error.issues)
        lines.extend(_plain_errors(error))
    lines.extend(['','問い合わせ用の詳細：',str(error)])
    return '\n'.join(lines)
