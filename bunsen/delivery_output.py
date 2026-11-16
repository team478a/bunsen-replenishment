"""Plan outputs share the existing offline workbook writer and PDF runtime."""
import html
import io
import json
from pathlib import Path

from .core import DataError, LABELS, units
from .shipping_output import xlsx_grid_bytes

HEADERS = ['便','出荷日','入荷日','商品','品名','数量P','PL枠','期限種別','概算期限','出荷残日','入荷残日','数量換算']


def plan_notice(report):
    parts = [report['notice'], '正式配送予定' if report['formal'] else '参考・未確定配送計画（正式使用不可）']
    if report.get('trial_only'):parts.append('架空試用・実出荷不可')
    if report.get('restored_view'):parts.append('復元参考・実出荷不可')
    return ' / '.join(parts)


def grid_rows(report, warehouse):
    products={p['product']:p for p in report['products']}
    return [[trip['trip_id'],item['dispatch'],item['arrival'],item['product'],item['name'],item['quantity_p'],item['pallets'],
             '賞味期限' if item['expiry_type']=='best_before' else '消費期限',item['estimated_expiry'],item['remaining_dispatch_days'],item['remaining_arrival_days'],units(item['quantity_p'],products[item['product']])]
            for trip in report['trips'] if trip['warehouse']==warehouse for item in trip['items']]


def plan_grid(report, warehouse):
    grid=[[f'14日配送計画 - {LABELS[warehouse]}'],[plan_notice(report)],
          [report['plan_id'],'計算実行日時',report['calculated_at'],'確認担当者',report.get('operator','未確認')],
          [f'仮製造日 {report["assumed_manufacture_date"]} / 最大16PL枠・1PL1商品。実期限ではありません。'],[],HEADERS]+grid_rows(report,warehouse)
    grid += [[],['日別予測（確定在庫ではありません）'],['商品','日付','需要予測P','確定入庫P','計画入庫P','計画後残高P','安全P']]
    grid += [[r['product'],r['date'],r['forecast_p'],r['transit_p'],r['planned_inbound_p'],r['projected_after_plan_p'],r['safety_p']] for r in report['timeline'] if r['warehouse']==warehouse]
    grid += [[],['参考警告']]+[[w] for w in report['warnings'] if w.startswith(warehouse+' ')]
    grid += [['データ識別',report['digest']],['確定日時',report.get('confirmed_at','未確定')]]
    return grid


def plan_pdf(report, warehouse):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Table, TableStyle, Spacer
    font='HeiseiKakuGo-W5';pdfmetrics.registerFont(UnicodeCIDFont(font))
    style=ParagraphStyle('jp-plan',fontName=font,fontSize=9,leading=13,wordWrap='CJK')
    para=lambda v:Paragraph(html.escape(str(v)),style)
    output=io.BytesIO();doc=SimpleDocTemplate(output,pagesize=landscape(A4),leftMargin=22,rightMargin=22,topMargin=22,bottomMargin=28)
    story=[para(f'14日配送計画 - {LABELS[warehouse]} / {report["plan_id"]}'),para(plan_notice(report)),
           para(f'計算実行 {report["calculated_at"]} / 仮製造日 {report["assumed_manufacture_date"]} / 担当者 {report.get("operator","未確認")}'),Spacer(1,8)]
    def table(head,rows,widths):
        t=Table([[para(v) for v in head]]+[[para(v) for v in row] for row in rows],colWidths=widths,repeatRows=1)
        t.setStyle(TableStyle([('GRID',(0,0),(-1,-1),.3,colors.grey),('BACKGROUND',(0,0),(-1,0),colors.HexColor('#e5eff5')),('VALIGN',(0,0),(-1,-1),'TOP'),('TOPPADDING',(0,0),(-1,-1),5),('BOTTOMPADDING',(0,0),(-1,-1),5)]))
        return t
    story += [table(HEADERS,grid_rows(report,warehouse),[44,73,73,45,76,55,34,48,73,35,35,80]),Spacer(1,10),para('日別予測（実在庫の期限切れ・廃棄数量は算出しません）')]
    timeline=[[r['product'],r['date'],r['forecast_p'],r['transit_p'],r['planned_inbound_p'],r['projected_after_plan_p'],r['safety_p']] for r in report['timeline'] if r['warehouse']==warehouse]
    story += [table(['商品','日付','需要予測P','確定入庫P','計画入庫P','計画後残高P','安全P'],timeline,[100,100,90,90,90,90,80]),Spacer(1,8)]
    story += [para(w) for w in report['warnings'] if w.startswith(warehouse+' ')]
    story += [para('データ識別 '+report['digest']),para('確定日時 '+report.get('confirmed_at','未確定'))]
    def footer(canvas,document):
        canvas.setFont(font,8)
        flag='架空試用・実出荷不可 / ' if report.get('trial_only') else ''
        if report.get('restored_view'):flag+='復元参考・実出荷不可 / '
        if not report['formal']:flag+='未確定 / '
        canvas.drawString(22,12,flag+'今日製造した場合の概算期限。実期限・実出荷を保証しません。')
        canvas.drawRightString(810,12,str(document.page))
    doc.build(story,onFirstPage=footer,onLaterPages=footer)
    return output.getvalue()


def export_plan(report, folder):
    if report['formal'] and not report['forecast_confirmed']:raise DataError(['未確定予測の正式配送予定は出力できません'])
    folder=Path(folder).resolve();outputs=[]
    status='confirmed' if report['formal'] else 'reference'
    stem=report['plan_id']+'-'+status
    # IDs originate in the application, but reject path injection in public exporter calls.
    if any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-' for c in stem):
        raise DataError(['配送計画IDが不正です'])
    for warehouse in LABELS:
        grid=plan_grid(report,warehouse)
        body='<!doctype html><html lang="ja"><meta charset="utf-8"><style>body{font-family:Meiryo,sans-serif}td{border:1px solid #888;padding:5px}table{border-collapse:collapse}</style><table>'
        body+=''.join('<tr>'+''.join('<td>'+html.escape(str(v))+'</td>' for v in row)+'</tr>' for row in grid)+'</table></html>'
        outputs += [(folder/(stem+'-'+warehouse+'.xlsx'),xlsx_grid_bytes(grid)),(folder/(stem+'-'+warehouse+'.pdf'),plan_pdf(report,warehouse)),(folder/(stem+'-'+warehouse+'.html'),body.encode('utf-8'))]
    outputs.append((folder/(stem+'.json'),json.dumps(report,ensure_ascii=False,indent=2).encode('utf-8')))
    if any(p.exists() for p,_ in outputs):raise DataError(['既存の配送計画出力は上書きしません'])
    folder.mkdir(parents=True,exist_ok=True);created=[]
    try:
        for path,content in outputs:
            with path.open('xb') as stream:created.append(path);stream.write(content)
    except Exception:
        for path in created:path.unlink(missing_ok=True)
        raise
    return [p for p,_ in outputs]
