"""Versioned local backups. Restoration always creates a new read-only copy."""
from __future__ import annotations

import argparse
from contextlib import closing, contextmanager
from datetime import date
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import sqlite3
import stat
import sys
import tempfile
import time
import uuid
import zipfile

from .core import DataError, SCHEMA, now_jst, stamp

MAX_FILE = 1024 ** 3
MAX_TOTAL = 2 * MAX_FILE
MAX_ENTRIES = 10000
MAX_MANIFEST = 1024 ** 2
CHUNK = 1024 ** 2
BACKUP_TIMEOUT_SECONDS = 30
TABLE_COLUMNS = {
    'events': ['id', 'at', 'kind', 'payload'],
    'issues': ['id', 'dispatch', 'revision', 'formal', 'number', 'at', 'payload'],
}


def file_digest(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        while block := stream.read(CHUNK):
            digest.update(block)
    return digest.hexdigest()


def copy_limited(source, destination, limit):
    with Path(source).open('rb') as incoming, Path(destination).open('xb') as outgoing:
        total = 0
        while block := incoming.read(CHUNK):
            total += len(block)
            if total > limit:
                raise DataError(['コピー中に容量上限を超えました'])
            outgoing.write(block)


def raw_digest(raw):
    if not isinstance(raw, dict) or not raw or any(name not in {k + '.csv' for k in SCHEMA} or not isinstance(value, str) for name, value in raw.items()):
        raise DataError(['取込原本の形式が不正'])
    return hashlib.sha256(json.dumps(raw, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def validate_database(db):
    """Validate archival consistency; do not reconfirm historical business values."""
    try:
        db.execute('PRAGMA trusted_schema=OFF')
        if db.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
            raise DataError(['履歴DBの整合性検査に失敗'])
        objects = db.execute("SELECT type,name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' AND type!='index'").fetchall()
        if set(objects) != {('table', name) for name in TABLE_COLUMNS}:
            raise DataError(['対応していない履歴DBスキーマ（テーブル・ビュー・トリガー）'])
        for table, columns in TABLE_COLUMNS.items():
            if [row[1] for row in db.execute(f'PRAGMA table_info({table})')] != columns:
                raise DataError([f'{table}: 対応していない履歴DB列'])
        issues, revisions = {}, {}
        for _, dispatch, revision, formal, number, at, content in db.execute('SELECT * FROM issues ORDER BY id'):
            payload = json.loads(content)
            stamp(at)
            day = date.fromisoformat(dispatch)
            expected = f'{"F" if formal else "P"}-{day:%Y%m%d}-R{revision:03d}'
            if type(revision) is not int or revision < 1 or formal not in (0, 1) or number != expected:
                raise DataError(['発行番号・改訂が不正'])
            if not isinstance(payload, dict) or any(payload.get(k) != v for k, v in dict(dispatch=dispatch, revision=revision, number=number, at=at).items()) or type(payload.get('formal')) is not bool or payload['formal'] != bool(formal):
                raise DataError([f'{number}: 発行メタデータとJSONが不一致'])
            if not isinstance(payload.get('tables'), dict) or not isinstance(payload.get('lines'), list) or raw_digest(payload.get('raw')) != payload.get('digest'):
                raise DataError([f'{number}: 取込原本ハッシュまたは明細形式が不正'])
            if number in issues:
                raise DataError(['発行番号が重複'])
            issues[number] = payload
            revisions.setdefault((dispatch, formal), []).append(revision)
        if any(sorted(values) != list(range(1, len(values) + 1)) for values in revisions.values()):
            raise DataError(['改訂履歴に欠落・重複があります'])
        issue_events = set()
        events = 0
        for _, at, kind, content in db.execute('SELECT * FROM events ORDER BY id'):
            stamp(at)
            payload = json.loads(content)
            if not isinstance(payload, dict) or not isinstance(kind, str) or not kind:
                raise DataError(['操作イベントの形式が不正'])
            if kind == 'import' and raw_digest(payload.get('raw')) != payload.get('digest'):
                raise DataError(['取込イベントの原本ハッシュが不一致'])
            if kind == 'issue':
                number = payload.get('number')
                if number in issue_events or issues.get(number) != payload:
                    raise DataError(['発行履歴と発行イベントが不一致'])
                issue_events.add(number)
            events += 1
        if issue_events != set(issues):
            raise DataError(['発行イベントに欠落があります'])
        for payload in issues.values():
            reference = payload.get('source_draft')
            if reference:
                source = issues.get(reference['number'])
                if source is None or source['formal'] or source['dispatch'] != payload['dispatch'] or source['digest'] != reference['digest']:
                    raise DataError(['再開元仮指示との参照が不一致'])
        return dict(events=events, issues=len(issues), last_issue=next(reversed(issues), None))
    except (sqlite3.Error, ValueError, TypeError, KeyError, OverflowError) as exc:
        if isinstance(exc, DataError):
            raise
        raise DataError([f'履歴DBを検証できません: {exc}']) from exc


def safe_member(name):
    parts = PurePosixPath(name).parts
    reserved = {'CON', 'PRN', 'AUX', 'NUL'} | {f'{prefix}{i}' for prefix in ('COM', 'LPT') for i in range(1, 10)}
    if not parts or '/'.join(parts) != name or name.startswith('/') or '\\' in name:
        raise DataError([f'不正なアーカイブパス: {name}'])
    for part in parts:
        if part in ('.', '..') or part.endswith((' ', '.')) or part.split('.')[0].upper() in reserved or any(ord(c) < 32 or c in '<>:"|?*' for c in part):
            raise DataError([f'不正なアーカイブパス: {name}'])
    if name not in ('manifest.json', 'history.db') and not (parts[0] == 'reports' and len(parts) > 1):
        raise DataError([f'未対応アーカイブ項目: {name}'])
    return parts


def _report_files(folder):
    if folder.is_symlink() or folder.is_junction():
        raise DataError(['帳票フォルダーのリンクは保存できません'])
    if not folder.exists():
        return
    for path in sorted(folder.iterdir()):
        if path.is_symlink() or path.is_junction():
            raise DataError([f'帳票のリンクは保存できません: {path.name}'])
        if path.is_dir():
            yield from _report_files(path)
        elif path.is_file():
            yield path
        else:
            raise DataError([f'未対応の帳票項目: {path.name}'])


def _entries(archive):
    entries = archive.infolist()
    if len(entries) > MAX_ENTRIES:
        raise DataError(['バックアップのファイル数上限を超えています'])
    names, size = set(), 0
    for entry in entries:
        safe_member(entry.filename)
        if entry.filename.casefold() in names or entry.is_dir() or stat.S_ISLNK(entry.external_attr >> 16) or entry.flag_bits & 1:
            raise DataError(['重複・リンク・暗号化など未対応のZIP項目'])
        names.add(entry.filename.casefold())
        limit = MAX_MANIFEST if entry.filename == 'manifest.json' else MAX_FILE
        if entry.file_size > limit:
            raise DataError(['バックアップの1ファイル上限を超えています'])
        size += entry.file_size
    if size > MAX_TOTAL or not {'manifest.json', 'history.db'} <= names:
        raise DataError(['バックアップ容量上限超過、または必須ファイル欠損'])
    return entries


@contextmanager
def validated_backup(path, expected_sha256=None):
    """Work on a private copy, so selection/restore never uses unchecked changes."""
    try:
        path = Path(path)
        if path.stat().st_size > MAX_TOTAL + 64 * CHUNK:
            raise DataError(['ZIPファイルの容量上限を超えています'])
        with tempfile.TemporaryDirectory(prefix='bunsen-check-') as temp:
            root = Path(temp)
            copied = root / 'archive.zip'
            copy_limited(path, copied, MAX_TOTAL + 64 * CHUNK)
            digest = file_digest(copied)
            if expected_sha256 is not None and digest != expected_sha256:
                raise DataError(['選択・検証後にZIPが変更されています。再検証してください'])
            content = root / 'content'
            content.mkdir()
            with zipfile.ZipFile(copied) as archive:
                entries = _entries(archive)
                manifest = json.loads(archive.read('manifest.json'))
                if not isinstance(manifest, dict) or manifest.get('format') != 'bunsen-backup' or type(manifest.get('version')) is not int or manifest['version'] != 1:
                    raise DataError(['未対応のバックアップ形式・バージョン'])
                stamp(manifest['created_at'])
                files = manifest['files']
                if not isinstance(files, dict) or set(files) != {e.filename for e in entries if e.filename != 'manifest.json'}:
                    raise DataError(['マニフェストとZIP内のファイル一覧が不一致'])
                for entry in entries:
                    if entry.filename == 'manifest.json':
                        continue
                    info = files[entry.filename]
                    if not isinstance(info, dict) or type(info.get('size')) is not int or info['size'] != entry.file_size:
                        raise DataError(['マニフェストのファイルサイズが不一致'])
                    target = content.joinpath(*safe_member(entry.filename))
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with archive.open(entry) as source, target.open('xb') as output:
                        copied_size = 0
                        while block := source.read(CHUNK):
                            copied_size += len(block)
                            if copied_size > entry.file_size:
                                raise DataError(['展開サイズが不正'])
                            output.write(block)
                    if copied_size != info['size'] or file_digest(target) != info.get('sha256'):
                        raise DataError([f'{entry.filename}: SHA-256またはサイズ不一致'])
            with closing(sqlite3.connect((content / 'history.db').as_uri() + '?mode=ro', uri=True)) as db:
                counts = validate_database(db)
            stored_counts = manifest['counts']
            if not isinstance(stored_counts, dict) or set(stored_counts) != {'events', 'issues', 'last_issue'} or type(stored_counts.get('events')) is not int or type(stored_counts.get('issues')) is not int or counts != stored_counts:
                raise DataError(['履歴件数とマニフェストが不一致'])
            yield content, dict(manifest, archive_sha256=digest)
    except (OSError, ValueError, KeyError, TypeError, sqlite3.Error, zipfile.BadZipFile, RuntimeError, EOFError) as exc:
        if isinstance(exc, DataError):
            raise
        raise DataError([f'バックアップを検証できません: {exc}']) from exc


def inspect_backup(path):
    with validated_backup(path) as (_, manifest):
        return manifest


def create_backup(db, data_home, destination, clock=now_jst):
    home, destination = Path(data_home).resolve(), Path(destination).resolve()
    main_path = next(row[2] for row in db.execute('PRAGMA database_list') if row[1] == 'main')
    if not main_path or Path(main_path).resolve().parent != home:
        raise DataError(['履歴DBとデータフォルダーの組合せが一致しません'])
    if destination.is_relative_to(home):
        raise DataError(['バックアップ先は現在のデータフォルダー外を指定してください'])
    if destination.exists():
        raise DataError(['既存のバックアップは上書きしません。別名を指定してください'])
    if db.in_transaction:
        raise DataError(['未完了のDB更新があります。更新完了後にバックアップしてください'])
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='bunsen-save-') as temp:
        root = Path(temp)
        snapshot = root / 'history.db'
        with closing(sqlite3.connect(snapshot)) as output:
            deadline = time.monotonic() + BACKUP_TIMEOUT_SECONDS
            def progress(_status, _remaining, _total):
                if time.monotonic() > deadline:
                    raise DataError(['履歴DBが使用中、または保存に時間がかかっています。更新完了後に再試行してください'])
            busy_timeout = db.execute('PRAGMA busy_timeout').fetchone()[0]
            db.execute(f'PRAGMA busy_timeout={min(busy_timeout, 100)}')
            try:
                db.backup(output, pages=128, progress=progress, sleep=0.05)
            finally:
                db.execute(f'PRAGMA busy_timeout={busy_timeout}')
            output.execute('PRAGMA journal_mode=DELETE')
            if snapshot.stat().st_size > MAX_FILE:
                raise DataError(['履歴DBの容量上限を超えています'])
            counts = validate_database(output)
        paths = {'history.db': snapshot}
        total_size = snapshot.stat().st_size
        for path in _report_files(home / 'reports'):
            name = path.relative_to(home).as_posix()
            safe_member(name)
            staged = root.joinpath(*PurePosixPath(name).parts)
            staged.parent.mkdir(parents=True, exist_ok=True)
            if path.stat().st_size > MAX_FILE:
                raise DataError(['帳票ファイルの容量上限を超えています'])
            if len(paths) + 2 > MAX_ENTRIES:
                raise DataError(['バックアップのファイル数上限を超えています'])
            copy_limited(path, staged, min(MAX_FILE, MAX_TOTAL - total_size))
            total_size += staged.stat().st_size
            paths[name] = staged
        files = {name: dict(size=path.stat().st_size, sha256=file_digest(path)) for name, path in paths.items()}
        manifest = dict(format='bunsen-backup', version=1, created_at=clock().isoformat(), counts=counts, files=files)
        archive_path = root / 'backup.zip'
        with zipfile.ZipFile(archive_path, 'w', zipfile.ZIP_DEFLATED) as archive:
            for name, path in paths.items():
                archive.write(path, name)
            archive.writestr('manifest.json', json.dumps(manifest, ensure_ascii=False, sort_keys=True))
        verified = inspect_backup(archive_path)
        created = False
        try:
            with destination.open('xb') as output, archive_path.open('rb') as source:
                created = True
                shutil.copyfileobj(source, output)
                output.flush()
                os.fsync(output.fileno())
            if file_digest(destination) != verified['archive_sha256']:
                raise DataError(['保存後のバックアップ検証に失敗'])
        except Exception:
            if created:
                destination.unlink(missing_ok=True)
            raise
        return verified


def restore_backup(archive_path, parent, expected_sha256=None, clock=now_jst):
    with validated_backup(archive_path, expected_sha256) as (content, manifest):
        parent = Path(parent).resolve()
        parent.mkdir(parents=True, exist_ok=True)
        final = parent / ('restored-' + uuid.uuid4().hex)
        if final.exists():
            raise DataError(['復元先は既に存在します。上書きしません'])
        with tempfile.TemporaryDirectory(prefix='.bunsen-restore-', dir=parent) as temp:
            staging = Path(temp) / 'content'
            shutil.copytree(content, staging)
            marker = dict(format='bunsen-restored', version=1, read_only=True,
                          restored_at=clock().isoformat(), archive_sha256=manifest['archive_sha256'],
                          backup_created_at=manifest['created_at'], counts=manifest['counts'])
            (staging / 'restore-info.json').write_text(json.dumps(marker, ensure_ascii=False, indent=2), encoding='utf-8')
            if not staging.resolve().is_relative_to(Path(temp).resolve()) or final.parent != parent:
                raise DataError(['復元先パスが不正'])
            staging.rename(final)
        return final


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description='ブンセン 履歴バックアップ・新規復元')
    sub = parser.add_subparsers(dest='action', required=True)
    create = sub.add_parser('create')
    create.add_argument('data_home', type=Path)
    create.add_argument('destination', type=Path)
    inspect = sub.add_parser('inspect')
    inspect.add_argument('archive', type=Path)
    restore = sub.add_parser('restore')
    restore.add_argument('archive', type=Path)
    restore.add_argument('parent', type=Path)
    args = parser.parse_args(argv)
    try:
        if args.action == 'create':
            with closing(sqlite3.connect((args.data_home.resolve() / 'history.db').as_uri() + '?mode=ro', uri=True)) as db:
                result = create_backup(db, args.data_home, args.destination)
        elif args.action == 'inspect':
            result = inspect_backup(args.archive)
        else:
            result = {'restored_directory': str(restore_backup(args.archive, args.parent)), 'read_only': True}
        print(json.dumps(result, ensure_ascii=True, indent=2))
    except (DataError, OSError, sqlite3.Error) as exc:
        parser.exit(1, str(exc) + '\n')


if __name__ == '__main__':
    main()
