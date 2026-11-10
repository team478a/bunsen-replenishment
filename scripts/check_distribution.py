"""Validate and launch only our freshly built preview ZIP in an isolated folder."""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
import tempfile
import zipfile


def digest(path):
    result = hashlib.sha256()
    with path.open('rb') as stream:
        while block := stream.read(1024 * 1024):
            result.update(block)
    return result.hexdigest()


def check(archive_path, result_path):
    archive_path, result_path = Path(archive_path).resolve(), Path(result_path).resolve()
    if result_path.exists():
        raise ValueError('Result already exists')
    expected = archive_path.with_suffix('.zip.sha256').read_text(encoding='ascii').split()[0]
    if digest(archive_path) != expected:
        raise ValueError('ZIP checksum mismatch')
    with tempfile.TemporaryDirectory(prefix='bunsen-package-check-') as temp:
        root = Path(temp) / '日本語 空白の展開先'
        root.mkdir()
        with zipfile.ZipFile(archive_path) as archive:
            names = archive.namelist()
            if len({name.casefold() for name in names}) != len(names):
                raise ValueError('Duplicate package entries')
            for name in names:
                parts = PurePosixPath(name).parts
                if not parts or parts[0] != 'BunsenReplenishment' or len(parts) < 2 or '..' in parts or '\\' in name or ':' in name:
                    raise ValueError('Unexpected package path')
                archive.extract(name, root)
        bundle = root / 'BunsenReplenishment'
        manifest = json.loads((bundle / 'package-manifest.json').read_text(encoding='utf-8'))
        actual = {p.relative_to(bundle).as_posix() for p in bundle.rglob('*') if p.is_file()}
        if set(manifest['files']) != actual - {'package-manifest.json'}:
            raise ValueError('Package file list mismatch')
        for name, info in manifest['files'].items():
            path = bundle / name
            if path.stat().st_size != info['size'] or digest(path) != info['sha256']:
                raise ValueError(f'Package file mismatch: {name}')
            if path.suffix.lower() == '.db' or path.name.startswith('.env'):
                raise ValueError('Business data or secret file included')
        env = dict(os.environ)
        for name in ('PYTHONHOME', 'PYTHONPATH', 'VIRTUAL_ENV', 'TCL_LIBRARY', 'TK_LIBRARY'):
            env.pop(name, None)
        system_root = Path(os.environ['SystemRoot'])
        env['PATH'] = str(system_root / 'System32') + os.pathsep + str(system_root)
        env['LOCALAPPDATA'] = str(Path(temp) / 'local-only')
        cwd = Path(temp) / '別の作業フォルダー'
        cwd.mkdir()
        flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
        def run(command, check=True):
            return subprocess.run(command, cwd=cwd, env=env, capture_output=True,
                                  encoding='utf-8', check=check, timeout=120, creationflags=flags)
        version = run([str(bundle / 'BunsenTools.exe'), '--version']).stdout.strip()
        run([str(bundle / 'BunsenReplenishment.exe'), '--version'])
        checks = {}
        for name, args in [('gui', ['--self-test-output']), ('tools', ['self-test', '--output'])]:
            report = Path(temp) / (name + '.json')
            executable = 'BunsenReplenishment.exe' if name == 'gui' else 'BunsenTools.exe'
            try:
                run([str(bundle / executable), *args, str(report)])
            except subprocess.CalledProcessError as exc:
                detail = report.read_text(encoding='utf-8') if report.exists() else exc.stderr
                raise RuntimeError(f'{executable}: {detail}') from exc
            checks[name] = json.loads(report.read_text(encoding='utf-8'))
            if not checks[name]['success'] or not checks[name]['frozen']:
                raise ValueError('The standalone executable did not pass')
            previous = report.read_bytes()
            collision = run([str(bundle / executable), *args, str(report)], check=False)
            if collision.returncode == 0 or report.read_bytes() != previous:
                raise ValueError('Diagnostic result was overwritten')
        demo = Path(temp) / 'demo-output'
        run([str(bundle / 'BunsenTools.exe'), 'demo', '--output', str(demo)])
        if len(list(demo.rglob('*.html'))) != 6:
            raise ValueError('Bundled demo reports missing')
        diagnosis = Path(temp) / 'CSV読取検査.json'
        diagnosed = run([str(bundle / 'BunsenTools.exe'), 'diagnose', str(bundle / 'samples/morning'), '--dispatch', '2026-11-09', '--output', str(diagnosis)], check=False)
        if diagnosed.returncode not in (0, 2):
            raise ValueError('Bundled CSV diagnosis failed: ' + diagnosed.stderr)
        reading = json.loads(diagnosis.read_text(encoding='utf-8'))['read_consistency']
        if reading['status'] != 'stable_observed' or reading['atomic_snapshot'] is not False:
            raise ValueError('CSV read observation metadata missing or overstated')
        outlook = Path(temp) / '参考見通し.json'
        reference = run([str(bundle / 'BunsenTools.exe'), 'outlook', str(bundle / 'samples/morning'), '--start', '2026-11-09', '--output', str(outlook)], check=False)
        if reference.returncode != 2:
            raise ValueError('Partial reference outlook did not return code 2: ' + reference.stderr)
        reference_data = json.loads(outlook.read_text(encoding='utf-8'))
        if reference_data['formal_issue_allowed'] or len(reference_data['rows']) != 30 or not outlook.with_suffix('.html').is_file():
            raise ValueError('Reference outlook is not explicitly non-issuable')
        kit = Path(temp) / '現場 受入キット'
        run([str(bundle / 'BunsenTools.exe'), 'acceptance-kit', '--dispatch', '2026-11-10', '--output', str(kit)])
        kit_info = json.loads((kit / 'kit.json').read_text(encoding='utf-8'))
        record = json.loads((kit / 'acceptance-record.json').read_text(encoding='utf-8'))
        if not kit_info['trial_only'] or record['overall_status'] != 'not_performed':
            raise ValueError('Acceptance evidence incorrectly recorded')
        if (kit / 'trial-data').exists():
            raise ValueError('Kit generation created a database')
        collision = run([str(bundle / 'BunsenTools.exe'), 'acceptance-kit', '--dispatch', '2026-11-10', '--output', str(kit)], check=False)
        if collision.returncode == 0:
            raise ValueError('Existing acceptance kit overwritten')
        info = json.loads((bundle / 'build-info.json').read_text(encoding='utf-8'))
        if info['version'] != version:
            raise ValueError('Executable and build version differ')
        for name in ('Python.txt', 'Tcl.txt', 'Tk.txt'):
            if not (bundle / 'licenses' / name).is_file():
                raise ValueError('Runtime license missing')
        for name in info['build_dependencies']:
            if not any(path.is_file() for path in (bundle / 'licenses/build-tools' / name).rglob('*')):
                raise ValueError('Build-tool license missing')
        result = dict(success=True, archive=archive_path.name, sha256=expected, build=info,
                      relocated=True, python_on_path=False, executables=checks, demo_reports=6,
                      acceptance_kit=True, reference_outlook=True, csv_read_observation=True)
    result_path.parent.mkdir(parents=True, exist_ok=True)
    with result_path.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('archive', type=Path)
    parser.add_argument('--result', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(check(args.archive, args.result), ensure_ascii=True))
