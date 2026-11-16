"""Create isolated human acceptance exercises from bundled fictional CSVs only."""
import csv
from datetime import date, datetime, timedelta
import hashlib
import io
import json
from pathlib import Path

from . import __version__
from .core import DATES, TIMESTAMPS, SCHEMA, V2_SCHEMA, DataError, now_jst
from .resources import resource_root

SOURCE_DAY = date(2026, 11, 9)
SCENARIOS = ('before_morning', 'morning', 'multi_lot')
V2_SCENARIOS = ('replenishment', 'replenishment-provisional', 'delivery-plan', 'report-many-products')
V2_CASES = (
    ('environment','端末・起動・試用隔離','指定試験日・版・Windows・表示倍率を記録。trial-dataを使用し試用表示がある。通常DBを変更しない。'),
    ('import','ムロオ在庫・既存予測・補充条件取込','samples/replenishment。神戸120P・杉戸48Pを分析。ブンセン在庫・朝照合は不要。'),
    ('calculate','倉庫別の日別計算と甲切上げ','現在庫・需要予測・確定入庫・安全在庫・必要P・換算後P・PL＋甲・基準日時を確認。'),
    ('edit','数量・理由・担当者の修正履歴','神戸144Pへ修正し理由と担当者を保存。元計算120Pと区別し、変更後の確認が解除される。'),
    ('provisional','未確定予測の正式確定停止','samples/replenishment-provisional。明示選択時だけ参考計算でき、確認チェック後も正式確定は停止。'),
    ('output','倉庫別Excel/PDF・実プリンター','神戸・杉戸別の数量/単位/日付/担当者/試用表示を照合。端末・アプリ版・用紙・倍率・改ページ・紙/PDFの証拠を記録。'),
    ('delivery','14日計画と計算日固定の概算期限','samples/delivery-plan。計算日＋10日が概算期限。将来配送日や再起動で仮製造日を更新しない。実期限/廃棄量の表示ではない。'),
    ('delivery_confirm','正式配送予定の確認ゲート','担当者・明示チェックなしは停止。条件変更で確認解除。保存原本から再計算し、再起動後も同じ計算日時・期限で確認できる。'),
    ('many_products','多品目・長い商品名・16PL分便','samples/report-many-productsは8商品。初回は商品ごと神戸432P/杉戸216P（合計3456P/1728P）。商品別PL換算・甲端数枠を数え、神戸の初回配送は19枠で2便（16+3）。'),
    ('many_print','多ページの帳票照合','全8商品・全明細・日別推移・長い品名/理由を照合。PDFの各ページに表見出し/試用注意があり、概算期限/残日数/負数が読める。Excelの全文・行高・印刷プレビューも確認。'),
    ('history','履歴とバックアップ閲覧専用','通常指示と配送計画を再表示。別フォルダーへ復元し、閲覧専用・試用注意を保持。新規確定不可。'),
    ('mapping','実CSVの対応確認（業務側）','列・コード・単位・在庫基準時刻・確定入庫の網羅/重複・予測への注文包含・更新頻度/確認責任者を記録。未確認を補完しない。'),
    ('business','正式値と運用承認（業務側）','単位/期限/補充設定、配送枠/端数積付、修正承認、バックアップ保管/復旧/配布更新を責任者が確認。未確認は運用開始を保留。'),
)
CASES = (
    ('environment', '現場端末・起動', '版・端末・Windows・起動と日本語表示を確認'),
    ('draft', '前日仮指示', 'before_morningを取込、神戸・杉戸各240Pの仮指示を保存'),
    ('resume', '再起動・引継ぎ', '同じキットで再起動し仮指示再開。確認・担当者は復元しない'),
    ('morning_gate', '現物未確認の停止', 'before_morningのまま正式発行できない'),
    ('shortage', '朝照合・二重割当', 'morningで各240Pを保持、全体不足240P・正式発行停止'),
    ('formal', '不足修正・試用正式', '各120P・L001に修正、再確認後に試用正式帳票'),
    ('lots', '複数ロット・期限・積載', '別キットのmulti_lotで各L001=72P/L002=48P、変更後の再検証'),
    ('print', '実プリンター・PDF', '試用表示、日本語、数量・ロット・期限・日付・ページ割りを目視確認'),
    ('backup', 'バックアップ・復元', '別フォルダー復元は閲覧専用、試用表示保持、元の試用DBへ戻る'),
    ('mapping', '実CSV項目照合', '別途、仕様書・列・単位・基準日時・確認者を業務側で確認'),
    ('business', '業務条件承認', '不足配分・期限・積載・朝確認手順を責任者が確認'),
)


def _digest(content):
    return hashlib.sha256(content).hexdigest()


def _csv_paths(workflow=0):
    if workflow == 2:
        return [f'samples/{scenario}/{name}.csv' for scenario in V2_SCENARIOS for name in V2_SCHEMA]
    return [f'samples/{scenario}/{name}.csv' for scenario in SCENARIOS for name in SCHEMA
            if name != 'dry_stock' or scenario != 'before_morning']


def create_kit(output, dispatch, now=None, *, workflow=0):
    """No arbitrary source folder: real inputs can never be date-shifted here."""
    output = Path(output).resolve()
    if output.exists():
        raise DataError(['受入キットは既存フォルダーへ作成できません'])
    delta = timedelta(days=(dispatch - SOURCE_DAY).days)
    contents, sources = {}, {}
    try:
        for relative in _csv_paths(workflow):
            original = (resource_root() / relative).read_bytes()
            sources[relative] = _digest(original)
            reader = csv.DictReader(io.StringIO(original.decode('utf-8-sig')))
            expected=(V2_SCHEMA if workflow==2 else SCHEMA)[Path(relative).stem].split(',')
            if workflow==2 and Path(relative).stem=='products' and Path(relative).parent.name in ('delivery-plan','report-many-products'):
                expected=expected[:-1]+['expiry_type','shelf_life_days']+expected[-1:]
            if reader.fieldnames != expected:
                raise DataError(['同梱サンプルの列が仮契約と一致しません'])
            stream = io.StringIO(newline='')
            writer = csv.DictWriter(stream, fieldnames=reader.fieldnames, lineterminator='\n')
            writer.writeheader()
            for row in reader:
                for field, value in row.items():
                    if field in DATES:
                        row[field] = (date.fromisoformat(value) + delta).isoformat()
                    elif field in TIMESTAMPS:
                        row[field] = (datetime.fromisoformat(value) + delta).isoformat()
                writer.writerow(row)
            contents[relative] = stream.getvalue().encode('utf-8')
    except (OverflowError, ValueError) as exc:
        raise DataError(['試験日の変換に失敗しました。日付範囲と同梱サンプルを確認してください']) from exc
    manifest = dict(format='bunsen-acceptance-kit', version=1, application_version=__version__,
                    trial_only=True, dispatch=dispatch.isoformat(), source_dispatch=SOURCE_DAY.isoformat(),
                    days_shifted=delta.days, created_at=(now or now_jst()).isoformat(),
                    source_files=sources, files={p: _digest(b) for p, b in contents.items()})
    if workflow==2:
        manifest['workflow']=2
    # Editable human evidence is deliberately not part of the immutable CSV hashes.
    record = dict(format='bunsen-acceptance-record', version=1, trial_only=True,
                  dispatch=dispatch.isoformat(), application_version=__version__,
                  overall_status='not_performed', operator=None, performed_at=None, machine=None,
                  approval_status='unconfirmed', notes=None,
                  cases=[dict(id=key, title=title, expected=expected, status='not_performed',
                              actual=None, evidence=None, operator=None, performed_at=None)
                         for key, title, expected in CASES])
    contents['acceptance-record.json'] = json.dumps(record, ensure_ascii=False, indent=2).encode('utf-8')
    if workflow==2:
        record['cases']=[dict(id=key,title=label,expected=expected,status='not_performed',actual=None,evidence=None,operator=None,performed_at=None) for key,label,expected in V2_CASES]
        contents['acceptance-record.json']=json.dumps(record,ensure_ascii=False,indent=2).encode('utf-8')
    contents['GUIDE.md'] = (_guide_v2(dispatch) if workflow==2 else _guide(dispatch)).encode('utf-8')
    if workflow==2:
        contents['OPERATIONAL-READINESS.md']=(resource_root()/'docs/operational-readiness.md').read_bytes()
    contents['kit.json'] = json.dumps(manifest, ensure_ascii=False, indent=2).encode('utf-8')
    # Reserve a new directory exclusively. On failure remove only paths we created.
    output.mkdir(parents=True, exist_ok=False)
    created, directories = [], []
    try:
        for relative, content in contents.items():
            path = output / relative
            for parent in reversed(path.parent.relative_to(output).parents):
                target = output / parent
                if target != output and not target.exists():
                    target.mkdir(); directories.append(target)
            if not path.parent.exists():
                path.parent.mkdir(); directories.append(path.parent)
            with path.open('xb') as stream:
                created.append(path)
                stream.write(content)
    except Exception:
        for path in reversed(created):
            path.unlink(missing_ok=True)
        for path in reversed(directories):
            path.rmdir()
        output.rmdir()
        raise
    return manifest


def _guide(dispatch):
    return f'''# 架空データによる現場受入キット — 実出荷に使用しない

試験出荷日: {dispatch.isoformat()}。版: {__version__}。
同梱の2026-11-09サンプルの全日付・基準日時・賞味期限を同じ日数だけ移しています。
数量・単位・相対期限は架空の例で、実在庫・業務承認・実CSV接続を示しません。

配布版: `BunsenReplenishment.exe --trial-kit "このキットのフォルダー"`
ソース版: `python -m bunsen --trial-kit "このキットのフォルダー"`
コマンドのパスは実際の絶対パスに置き換えてください。キット全体を移動しても使えます。
試用DBはキット内trial-data、通常のLOCALAPPDATA履歴には接続しません。
実時計を使用します。正式発行の操作試験は指定出荷日の07時以降に行ってください。
別日や07時前は朝データが未来等として止まります。時計変更・検証解除は不要です。

1. 出荷日を試験日にし、samples/before_morningを取込→計算→仮指示。
2. 閉じて同じオプションで再起動。履歴から最新の仮指示を再開。
3. 朝現物未確認で正式発行が止まることを確認。
4. samples/morningを再取込・計算。各240Pを保持し全体不足240Pを表示。
5. 両倉庫を120P、ロットL001に修正。担当者と再確認後、試用正式発行。
6. ブラウザーで試用表示・数量・期限等を確認し、実プリンターとPDFのページ割りを確認。
7. バックアップ→別フォルダー復元→閲覧専用・試用表示→元の試用DBへの復帰を確認。

複数ロットの試験は別の新規キットを使い、samples/multi_lotを取込し各倉庫を
120P（L001=72P、L002=48P）に指定します。試験同士の改訂・履歴を混ぜないでください。
CSVを編集した場合、このキットでの起動は拒否します。別キットを新規作成してください。

acceptance-record.jsonの各ケースについて担当者・日時・実結果・証拠の保存先を記入します。
初期状態は全件not_performed、承認unconfirmedです。自動実行で現場結果を埋めません。
pass/fail/blocked/not_performed等を記録し、判定の根拠を残してください。
実CSV照合・業務設定承認は別途必要です。帳票・紙・PDF・バックアップは試用資料です。
実データや試験証拠をリポジトリへコミットしないでください。
'''


def prepare_trial_home(folder):
    """Verify immutable fictional CSVs before any DB is opened or created."""
    folder = Path(folder).resolve()
    try:
        manifest = json.loads((folder / 'kit.json').read_text(encoding='utf-8'))
        if (manifest.get('format') != 'bunsen-acceptance-kit' or manifest.get('version') != 1
                or manifest.get('application_version') != __version__
                or manifest.get('workflow',0) not in (0,2)
                or manifest.get('trial_only') is not True or set(manifest['files']) != set(_csv_paths(manifest.get('workflow',0)))):
            raise ValueError('未対応のキットまたはアプリ版の不一致')
        date.fromisoformat(manifest['dispatch'])
        for relative, expected in manifest['files'].items():
            path = folder / relative
            if not path.resolve().is_relative_to(folder) or path.is_symlink() or _digest(path.read_bytes()) != expected:
                raise ValueError('サンプル改変: ' + relative)
        # Unknown CSVs must not silently become part of the exercise.
        actual = {p.relative_to(folder).as_posix() for p in (folder / 'samples').rglob('*') if p.suffix.lower() == '.csv'}
        if actual != set(manifest['files']):
            raise ValueError('未定義CSV')
        home = folder / 'trial-data'
        if home.resolve() != home or home.is_symlink():
            raise ValueError('試用DBフォルダーが外部へ転送されています')
        if any((home / name).resolve() != home / name for name in ('history.db', 'history.db-wal', 'history.db-shm', 'history.db-journal')):
            raise ValueError('試用DBが外部へ転送されています')
        marker = home / 'trial-info.json'
        if home.exists():
            info = json.loads(marker.read_text(encoding='utf-8'))
            if info != {'format': 'bunsen-trial-home', 'version': 1, 'trial_only': True}:
                raise ValueError('試用DB識別が不正です')
        else:
            home.mkdir()
            with marker.open('x', encoding='utf-8') as stream:
                json.dump(dict(format='bunsen-trial-home', version=1, trial_only=True), stream)
        return home, date.fromisoformat(manifest['dispatch'])
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        raise DataError([f'受入キットを確認できません。新規生成してください: {exc}']) from exc


def _guide_v2(dispatch):
    return f'''# ムロオ必要補充方式の架空試用 - 実出荷に使用しない

試験日 {dispatch}、版 {__version__}。同梱の架空サンプルだけを同じ日数だけ移しています。
`BunsenReplenishment.exe --trial-kit "このキットの絶対パス"` または
`python -m bunsen --trial-kit "このキットの絶対パス"`で起動します。
履歴はキット内trial-dataへ分離します。ブンセン在庫・朝照合は不要です。

1. samples/replenishmentを取込し、分析・補充計算を実行します。
2. 神戸120P、杉戸48P、必要P・換算後P・PL＋甲・在庫/予測/入庫基準を確認します。
3. 担当者名を入れ、必要なら数量と理由を修正・反映します。確認チェック後に正式確定します。
4. 出力ボタンで神戸・杉戸別のExcel/PDF/HTMLを新しい保存先へ出力し、日本語・数量・ページ割りを確認します。
5. samples/replenishment-provisionalは明示選択で参考分析できますが正式確定は停止します。
6. 管理画面の設定変更は根拠・担当者確認を記録します。変更後は再分析・再確認します。
7. samples/delivery-planを取込し「14日配送計画」で計画を計算・保存します。計算日＋10日の概算期限と、出荷/入荷残日数を確認します。試験日と計算実行日が同じ場合は概算期限が{dispatch+timedelta(days=10)}です。将来の配送日を仮製造日にしません。
8. 担当者と計画用確認チェックがないと正式配送予定へ確定できません。保存履歴を選び、同じ計算日時で再表示/確定/出力できることを確認します。
9. samples/report-many-productsを取込します。8商品の長い架空品名と商品別PL換算を確認します。初回は各商品で神戸432P/杉戸216P、倉庫合計3456P/1728Pです。神戸の初回は19PL枠で16枠+3枠の2便です。甲端数も1枠を占有し、異なる商品を同じPLへ積みません。
10. 多品目の指示書と14日計画をExcel/PDFへ出力し、全明細・改ページ・繰返し見出し・試用/参考/概算注意・長い品名を確認します。数量修正では長い架空理由も試します。Excelで行高上限を超える長文は対象セルを示して停止し、内容を切り捨てません。
11. 紙のプリンター名・用紙・倍率・Excel/PDF表示アプリ・版・証拠ファイル先を記録します。読めない文字・欠け・不一致は不合格として残し、現場結果を自動テスト結果で代用しません。

サンプルのムロオ在庫は試験日07時の宣言です。それ以前は未来データとして停止します。
これは朝のブンセン在庫確認条件ではありません。時計や検証を変更しないでください。
試験日や版が異なる場合は新規キットを生成してください。設定値は架空例で業務承認済みではありません。
受入記録は全件未実施・承認未確認です。人が確認した結果だけを記入します。
acceptance-record.jsonのexpectedと実際の数量/表示を比較し、status/actual/evidence/operator/performed_atを記入してください。実CSV・正式値・運用承認は同梱OPERATIONAL-READINESS.mdの項目で別途確認します。
必要補充数量は実出荷実績や供給可能在庫の保証ではありません。
'''
