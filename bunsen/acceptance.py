"""Create isolated human acceptance exercises from bundled fictional CSVs only."""
import csv
from datetime import date, datetime, timedelta
import hashlib
import io
import json
from pathlib import Path

from . import __version__
from .core import DATES, TIMESTAMPS, SCHEMA, DataError, now_jst
from .resources import resource_root

SOURCE_DAY = date(2026, 11, 9)
SCENARIOS = ('before_morning', 'morning', 'multi_lot')
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


def _csv_paths():
    return [f'samples/{scenario}/{name}.csv' for scenario in SCENARIOS for name in SCHEMA
            if name != 'dry_stock' or scenario != 'before_morning']


def create_kit(output, dispatch, now=None):
    """No arbitrary source folder: real inputs can never be date-shifted here."""
    output = Path(output).resolve()
    if output.exists():
        raise DataError(['受入キットは既存フォルダーへ作成できません'])
    delta = timedelta(days=(dispatch - SOURCE_DAY).days)
    contents, sources = {}, {}
    try:
        for relative in _csv_paths():
            original = (resource_root() / relative).read_bytes()
            sources[relative] = _digest(original)
            reader = csv.DictReader(io.StringIO(original.decode('utf-8-sig')))
            if reader.fieldnames != SCHEMA[Path(relative).stem].split(','):
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
    # Editable human evidence is deliberately not part of the immutable CSV hashes.
    record = dict(format='bunsen-acceptance-record', version=1, trial_only=True,
                  dispatch=dispatch.isoformat(), application_version=__version__,
                  overall_status='not_performed', operator=None, performed_at=None, machine=None,
                  approval_status='unconfirmed', notes=None,
                  cases=[dict(id=key, title=title, expected=expected, status='not_performed',
                              actual=None, evidence=None, operator=None, performed_at=None)
                         for key, title, expected in CASES])
    contents['acceptance-record.json'] = json.dumps(record, ensure_ascii=False, indent=2).encode('utf-8')
    contents['GUIDE.md'] = _guide(dispatch).encode('utf-8')
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
                or manifest.get('trial_only') is not True or set(manifest['files']) != set(_csv_paths())):
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
