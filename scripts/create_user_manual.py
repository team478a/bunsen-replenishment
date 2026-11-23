from pathlib import Path
from html import escape
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak

ROOT=Path(__file__).resolve().parents[1]
import argparse,sys
sys.path.insert(0,str(ROOT))
from bunsen import __version__
parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True);parser.add_argument('--portable-fonts',action='store_true');args=parser.parse_args()
OUT=args.output;OUT.parent.mkdir(parents=True,exist_ok=True)
if Path('C:/Windows/Fonts/meiryo.ttc').exists() and not args.portable_fonts:
    FONT,BOLD='JP','JPB'
    pdfmetrics.registerFont(TTFont('JP','C:/Windows/Fonts/meiryo.ttc',subfontIndex=0))
    pdfmetrics.registerFont(TTFont('JPB','C:/Windows/Fonts/meiryob.ttc',subfontIndex=0))
else:
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    FONT,BOLD='HeiseiKakuGo-W5','HeiseiMin-W3'
    pdfmetrics.registerFont(UnicodeCIDFont(FONT));pdfmetrics.registerFont(UnicodeCIDFont(BOLD))
pdfmetrics.registerFontFamily(FONT,normal=FONT,bold=BOLD,italic=FONT,boldItalic=BOLD)
NAVY=colors.HexColor('#183447');TEAL=colors.HexColor('#087F8C');MUTED=colors.HexColor('#546879')
styles={
 'body':ParagraphStyle('body',fontName=FONT,fontSize=11,leading=18,textColor=NAVY,spaceAfter=8,wordWrap='CJK'),
 'small':ParagraphStyle('small',fontName=FONT,fontSize=9,leading=14,textColor=MUTED,spaceAfter=5,wordWrap='CJK'),
 'title':ParagraphStyle('title',fontName=BOLD,fontSize=24,leading=35,textColor=NAVY,spaceAfter=15,wordWrap='CJK'),
 'h1':ParagraphStyle('h1',fontName=BOLD,fontSize=20,leading=29,textColor=NAVY,spaceAfter=16,wordWrap='CJK'),
 'h2':ParagraphStyle('h2',fontName=BOLD,fontSize=12,leading=20,textColor=TEAL,spaceBefore=9,spaceAfter=6,wordWrap='CJK'),
 'cell':ParagraphStyle('cell',fontName=FONT,fontSize=10,leading=16,textColor=NAVY,wordWrap='CJK'),
}
story=[]
def p(text,kind='body'):return Paragraph(text,styles[kind])
def add(text,kind='body'):story.append(p(text,kind))
def heading(n,title):
 if story:story.append(PageBreak())
 add(f'操作ガイド  /  {n:02d}', 'small');add(title,'h1')
def step(n,title,text):add(f'{n}　{title}','h2');add(text)
def box(title,text):
 t=Table([[p(title,'h2')],[p(text)]],colWidths=[491])
 t.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,-1),colors.HexColor('#EAF5F4')),('BOX',(0,0),(-1,-1),0.6,colors.HexColor('#B9DDDA')),('LEFTPADDING',(0,0),(-1,-1),12),('RIGHTPADDING',(0,0),(-1,-1),12),('TOPPADDING',(0,0),(-1,-1),3),('BOTTOMPADDING',(0,0),(-1,-1),5)]));story.append(t);story.append(Spacer(1,9))
def table(rows,widths):
 t=Table([[p(escape(str(v)),'cell') for v in row] for row in rows],colWidths=widths,repeatRows=1,hAlign='LEFT')
 t.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.HexColor('#DDECEF')),('ROWBACKGROUNDS',(0,1),(-1,-1),[colors.white,colors.HexColor('#F5F8FA')]),('LINEBELOW',(0,0),(-1,0),1,TEAL),('GRID',(0,1),(-1,-1),0.3,colors.HexColor('#D7E0E5')),('VALIGN',(0,0),(-1,-1),'TOP'),('LEFTPADDING',(0,0),(-1,-1),9),('RIGHTPADDING',(0,0),(-1,-1),9),('TOPPADDING',(0,0),(-1,-1),8),('BOTTOMPADDING',(0,0),(-1,-1),8)]));story.append(t);story.append(Spacer(1,9))

add('現場担当者・管理担当者向け  /  Windows版','small')
add('ブンセン\n補充出荷指示アプリ'.replace('\n','<br/>'),'title')
add('操作マニュアル  0.7.1-preview.1','h2')
add('ムロオ神戸・杉戸へ、いつ・何PLの補充が必要かを確認し、倉庫別のExcel・PDFを作成するための手順です。')
box('日常の流れ','在庫PDFを読み取る → 予測CSVを取込 → データを開いて計算 → 数量を確認・修正 → 確定してExcel・PDF出力')
table([['行いたいこと','参照ページ'],['初めて起動する・必要データをそろえる','2'],['ムロオの在庫PDFを読み取る','3'],['予測結果CSVを取り込む','4'],['必要量を確認し、出荷指示書を作る','5'],['履歴・バックアップ・14日計画を使う','6'],['エラーで止まったとき・最終チェック','7'],['JANマスタCSVの一括登録','8']],[373,118])
add('この版の数量ルール','h2')
add('商品はJAN単位、出荷は整数の物理PL単位です。神戸と杉戸で1PLのP数が異なる場合は、それぞれの商品マスタを使います。甲・混載・端数パレット出荷は対象外です。')
add('大口・特売は加算せず、「予測出荷数量（パック）」を使用します。ブンセン側在庫・製造数量・確定入庫予定・輸送中数量は計算に使用しません。')
add('対象アプリ: 0.7.1-preview.1 / 資料作成日: 2026年11月23日','small')
add('本書は現行実装に基づく操作説明です。正式業務設定・倉庫コード対応・現場受入の承認を代替するものではありません。','small')

heading(2,'起動と、最初の準備')
step(1,'ZIPをすべて展開する','ZIPを新しいフォルダーへ展開し、BunsenReplenishment.exeを起動します。exeだけを取り出さず、_internal・docs・samplesなどを一緒に保管してください。PythonやExcel本体の追加インストールは不要です。')
step(2,'JAN・物理PLマスタを登録する','「管理・その他」→「JAN・倉庫別PLマスタ」を開きます。「新規JAN」でJAN（13桁）、品名、賞味/消費期限、製造からの期限日数、神戸・杉戸それぞれの1物理PLのP数を入力します。「CSV取込・ひな形」で一括登録もできます（8ページ）。担当者と登録・変更根拠を入力し、「確認済みJAN・PLマスタを保存」を押します。')
step(3,'承認済みの補充条件を用意する','管理担当者が倉庫別・JAN別の補充条件CSV（requirements.csv）を準備します。サイクル・配送日数・安全日数・対象日数には業務で確認した値を使います。サンプル値を本番用に流用しないでください。取込後の変更は「管理・その他」の補充条件画面で行えます。')
add('1日分を同じフォルダーにまとめる','h2')
table([['ファイル','用意する方法'],['inventory.csv / source_evidence.csv','在庫PDFの確認保存で出力'],['products.csv / pallets.csv','在庫PDFの確認保存で使用マスタを出力'],['pdf-review.json','在庫PDFの確認・修正記録として保管'],['forecast.csv / forecast-review.csv','予測CSVの取込・確認保存で出力'],['requirements.csv','管理担当者が承認済みの条件で準備']],[237,254])
add('例: 日付別の空フォルダーへ在庫保存 → 同じ場所へ予測保存 → 承認済みrequirements.csvを配置。既存ファイルを上書きせず、その日・版ごとに分けます。','small')
add('登録マスタのない商品・未確定の条件は推測で埋めません。倉庫コード対応は業務確認待ちのため、確認結果が出てから指定します。','small')

heading(3,'在庫PDFを読み取り、確認する')
step(1,'神戸・杉戸のPDFを選ぶ','補充出荷画面の「在庫PDFを読み取る」を押します。各倉庫の「PDF / ZIPを選択」で当日の対象資料を選びます。ZIPの場合は一覧から対象日のPDFを1件選びます。複数日の在庫は合算しません。')
step(2,'原本と読取結果を照合する','「原本を見る」で、営業所名・在庫集計日・商品・現在庫を確認します。現在ケース×C入＋現在バラからP数量を求めます。商品合計だけを使用し、ロット明細や当入・当出は再加算しません。')
step(3,'在庫基準時刻を確認する','PDF左下の最初のページの作成時刻を初期表示します。必要ならHH:MMまたはHH:MM:SSで修正してください。始業・終業の選択はありません。在庫集計日までの入出庫を反映済みとし、翌日から需要を差し引きます。作成日が翌日でも在庫集計日はずらしません。')
step(4,'空欄と修正数量を確認する','空欄は最初「未確認」です。帳票上の意味を確認した場合だけ「バラの空欄は0」等のルールを選びます。必要な商品は確認数量Pと原本確認根拠を入力し、「反映」を押します。未読取・欠損・数量変更には理由が必要です。')
step(5,'確認済み在庫を保存する','担当者を入力し、「数量を確認して在庫CSVを保存」を押します。日付別の新しい保存先を選びます。2ページの在庫関連5ファイルが出力されます。保存後は予測・補充条件をそろえて計算へ進みます。')
box('読取できない・数量が空欄の場合','画像スキャンPDF、別レイアウト、ページ欠落、商品合計の不備などは停止します。対応する原本を入手するか、担当者が原本を確認して必要な数量・根拠を整備してください。不明値を0にして通さないでください。')
add('対応は確認済みの文字情報を持つムロオ「在庫一覧受払」PDFです。読取候補は、担当者の確認保存前には確定在庫として扱いません。','small')

heading(4,'予測結果CSVを取り込む')
step(1,'元の予測結果を選択する','「予測CSVを取込」→「予測CSVを選択」で既存システムの予測結果CSVを選びます。UTF-8（BOM可）、10MB以下が対象です。表示された行数・JAN数・対象日・不備を確認します。未登録JANは警告のみで取込保存でき、後からマスタを登録できます。')
table([['必須列','確認する内容'],['JAN / 倉庫ID / 予測日','13桁JAN、元倉庫コード、YYYY-MM-DD'],['予測出荷数量（パック）','0以上の整数または有限小数P'],['予測生成日時','タイムゾーン付き日時。未来の日時は不可'],['予測区分 / 予測用途','通常・暫定・補完と、正式/参考の区分']],[237,254])
step(2,'倉庫コードの対応を指定する','業務で確認した内容に従い、元コードが神戸・杉戸のどちらかを選びます。初回は未設定です。両倉庫が一意に対応するまで保存できません。前回採用した対応は次回表示されますが、今回の資料にも合うことを確認します。')
step(3,'担当者が採用し、保存する','担当者を入力して「この予測・倉庫対応を使用して保存」を押します。在庫保存と同じ日付別フォルダーを選び、forecast.csvとforecast-review.csvを保存します。同名ファイルがある場合は、新しい版のフォルダーを用意してください。')
box('採用するのは基本P列だけ','「大口込み総出荷数量（パック）」は使いません。大口・特売・注文・製造・入庫を別加算せず、予測在庫も現在庫として使いません。小数Pは途中で丸めず、最後に物理PLへ切り上げます。')
add('「通常予測」かつ「正式Production予測」を担当者が採用した行を正式指示の計算に使用できます。暫定・補完・参考予測を使った結果は正式発行できません。欠損・重複・未知の区分は停止します。未登録JANは取込後に登録してください。計算前には必要なマスタをそろえます。','small')
add('forecast-review.csvは元CSV・ハッシュ・担当者・倉庫対応を保存する証跡です。forecast.csvと一緒に保管し、数量を直接書き換えず元CSVから取り込み直してください。','small')

heading(5,'数量を確認し、出荷指示書を出す')
step(1,'出荷日を指定して計算する','補充出荷画面で「出荷日」を指定し、「データを開いて計算」で2ページのCSV一式があるフォルダーを選びます。出荷日と到着日は別です。倉庫別の配送日数に基づく到着日を確認してください。')
step(2,'神戸・杉戸の必要量を確認する','同じ表に両倉庫の現在庫P、必要量P、出荷数量（物理PL）、到着日が表示されます。「計算根拠・取込情報を表示」で日別需要、安全在庫、在庫・予測の基準日時、警告を確認します。')
box('必要Pと出荷PLは別の数量','日別在庫が安全在庫を下回らないための必要Pを算出し、倉庫別マスタの1PL数量へ切り上げます。架空例: 必要250P、1PL=240Pなら2PL=480Pです。計算上の必要量と実際の供給可能量・実出荷実績は別です。')
step(3,'必要な場合だけ数量を修正する','表から商品を選ぶと修正欄が開きます。「指示量 PL」に0以上の整数PL数と理由を入力し、「修正を反映」を押します。入力だけで確定へ進まないでください。必要量を下回る修正には、在庫不足リスクが残ります。')
step(4,'担当者が確定し、保存先を選ぶ','担当者を入力し、「確定してExcel・PDF出力」を押します。この操作が最終確認です。保存先を選ぶと正式指示を保存・出力します。保存先選択をキャンセルした場合は確定しません。')
step(5,'出力完了と帳票を確認する','発行番号と出力先を確認します。神戸・杉戸それぞれのExcel・PDF・HTML（合計6ファイル）を出力します。PDFを開いて倉庫、出荷日、到着日、商品、数量、発行番号を確認し、必要に応じて印刷します。')
add('データを変更した後は再取込・再計算してください。在庫や予測の確認保存後は古い計算が無効になります。過去の確認結果を使い回しません。','small')

heading(6,'履歴・バックアップ・14日計画')
add('保存した指示をもう一度出力する','h2')
add('「履歴」で指示を選び、「選択した指示をExcel・PDF出力」を押します。旧版・復元履歴には注意表示が付きます。現場で使う前に、対象日と最新改訂を確認してください。')
add('確定後にファイル出力だけ失敗した場合','h2')
add('同じ画面の出力ボタンで保存済み指示を再出力できます。新たな改訂を重ねて作る必要はありません。途中までファイルができている場合は、別の空フォルダーを選びます。既存帳票は上書きしません。')
add('バックアップと復元','h2')
add('「管理・その他」からバックアップ機能を開き、ZIPを新規保存します。更新前にはバックアップを取得し、旧版アプリを閉じて、新しい版を別フォルダーへ展開してください。')
add('バックアップには履歴DBとアプリのreports配下を含みます。元の作業フォルダーや別の保存先へ出した帳票は別途保管してください。通常の履歴保存先は %LOCALAPPDATA%/BunsenReplenishment です。')
add('復元した履歴は閲覧専用です。再出力帳票は参考用となり、新たな正式発行はできません。通常業務へ戻るときは「通常データへ戻る」を使います。障害後の本番復帰は管理担当者へ依頼してください。')
add('14日配送計画を使う場合','h2')
add('「管理・その他」→「14日配送計画」を開き、必要な期間の予測・補充条件・商品期限をそろえて計算します。最大16物理PL/便、1PL1商品で計画します。正式な配送予定には担当者の確認操作が必要です。')
box('期限は概算の参考値','保存した計算実行日＋商品別期限日数で「今日製造した場合」を評価します。実際の製造日・ロット・実期限の適合性や廃棄数量を保証しません。先の予測が不足する場合は、計画や参考評価ができないことがあります。')

heading(7,'困ったとき・出力前のチェック')
table([['表示・症状','対応'],['予測CSVを保存できない','倉庫コードの対応が未設定・重複していないか、不備欄と担当者を確認します。未登録JANだけでは取込を止めません。'],['不足・欠損・重複のエラー','表示された倉庫・JAN・日付・項目を確認し、元データの管理担当者へ整備を依頼します。0補完はしません。'],['在庫PDFが読めない','文字情報のある対応様式か、倉庫・集計日・ページが正しいかを確認します。画像OCRは対象外です。'],['予測があるのに計算できない','在庫基準日から対象期間・安全在庫計算まで、必要な日別予測がそろっているか確認します。'],['正式出力できない','暫定/補完/参考予測、過去日、必須データ不足、未反映修正、閲覧専用を確認します。'],['入荷前欠品リスク','今回の到着前に不足する予測です。出荷量の増加だけで解消したと判断せず、担当者が別途対応します。'],['ファイルが既にある','別の新規・空フォルダーを選びます。過去の原本・履歴・帳票を削除して回避しません。'],['端末の保護機能で起動が止まる','管理担当者に配布元・ハッシュ・運用方針を確認します。保護機能を無効にしないでください。']],[155,336])
add('確定前に確認すること','h2')
add('1. 出荷日・到着日・対象倉庫が正しい。<br/>2. 在庫の基準日時と日別予測の期間が合っている。<br/>3. 必要Pと物理PL、修正数量・理由を確認した。<br/>4. 不足・未確認・入荷前欠品の警告に対応した。<br/>5. 担当者と出力先、発行番号・改訂を確認する。')
add('詳細資料: docs/forecast-csv-import.md、docs/pdf-inventory-reader.md、docs/jan-pl-contract.md、docs/backup-restore.md。サンプルはすべて架空です。倉庫コード・補充条件・不足マスタの確認と現場受入後に、責任者の判断で運用してください。','small')

heading(8,'JANマスタCSVを一括登録する')
step(1,'ひな形を保存する','「管理・その他」→「JAN・倉庫別PLマスタ」→「CSV取込・ひな形」を開きます。「CSVひな形を保存」で空のCSVを保存します。既存商品の編集には「登録済みを同じCSV形式で出力」を使えます。')
table([['列名','入力内容'],['JAN','半角数字13桁。Excelで先頭0を落とさない'],['品名','全角英数・日本語をそのまま使用可'],['期限種別','賞味期限 または 消費期限'],['製造からの期限日数','確認済みの正整数'],['神戸1PLのP数 / 杉戸1PLのP数','それぞれの倉庫の物理1PLの確認済みP数']],[237,254])
step(2,'CSVを選び、新規・更新を確認する','UTF-8（BOM可）またはCP932で保存した6列のCSVを「CSVを選択」で読み取ります。5MB・1万行までです。新規/更新、JAN、品名、期限、両倉庫のPL数量を確認します。不備が1行でもあれば全体を停止します。')
step(3,'担当者が一括登録する','担当者と登録・変更根拠を入力し、「内容を確認して一括登録」を押します。同じJANは更新し、CSVにない既存商品は残します。原本・ハッシュ・変更前後を履歴へ保存します。')
box('予測を先に取り込んでも構いません','未登録JANを含む予測CSVは警告付きで保存できます。その後にこの画面でマスタを登録し、在庫PDFを確認保存してからCSV一式を取り込んで計算します。未登録JANが残る計算・正式発行は停止します。')
add('数値や期限は推測で埋めません。既存のproducts.csvとpallets.csvは別の計算用契約です。一括登録にはこの6列のひな形を使用してください。変更後は再取込・再計算してください。','small')

def footer(canvas,doc):
 w,h=A4;canvas.setStrokeColor(colors.HexColor('#CEDCE3'));canvas.line(44,43,w-44,43)
 canvas.setFont(FONT,8);canvas.setFillColor(MUTED);canvas.drawString(44,29,'ブンセン補充出荷指示アプリ | 0.7.1-preview.1 操作マニュアル')
 canvas.drawRightString(w-44,29,f'{doc.page} / 8')
 canvas.setFillColor(TEAL);canvas.rect(0,h-8,w,8,stroke=0,fill=1)
doc=SimpleDocTemplate(str(OUT),pagesize=A4,rightMargin=52,leftMargin=52,topMargin=35,bottomMargin=57,title='ブンセン補充出荷指示アプリ 操作マニュアル',author='ブンセン補充出荷指示アプリ',pageCompression=1)
doc.build(story,onFirstPage=footer,onLaterPages=footer)
print('Created user manual PDF')
