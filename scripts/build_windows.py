"""Create a preview onedir ZIP in a private workspace; never read business data."""
import argparse
from datetime import datetime, timezone
from importlib.metadata import distribution, version
import json
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from bunsen import __version__
from bunsen.backup import file_digest


def git(*args):
    return subprocess.check_output(['git', '-C', str(ROOT), *args]).decode('utf-8').strip()


def copy_resources(target):
    # Only documented repository resources, never LOCALAPPDATA, data/, or output/.
    files = git('ls-files', '-z', '--', 'README.md', 'docs', 'samples').split('\0')
    for name in files:
        if not name or Path(name).suffix.lower() not in ('.md', '.csv'):
            continue
        source = ROOT / name
        if source.is_symlink() or source.is_junction():
            raise ValueError(f'Linked distribution resource: {name}')
        destination = target / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)


def copy_licenses(target, packages):
    licenses = target / 'licenses'
    licenses.mkdir()
    mandatory = {'Python.txt': Path(sys.base_prefix) / 'LICENSE.txt',
                 'Tk.txt': Path(sys.base_prefix) / 'tcl/tk8.6/license.terms',
                 'Tcl.txt': ROOT / 'distribution/licenses/Tcl.txt'}
    for name, source in mandatory.items():
        if not source.is_file():
            raise ValueError(f'Required runtime license not found: {source}')
        shutil.copyfile(source, licenses / name)
    for package in packages:
        dist = distribution(package)
        copied = 0
        for file in dist.files or []:
            relative = str(file).replace('\\', '/')
            if '.dist-info/' in relative and Path(relative).name.lower().startswith(('license', 'copying', 'notice')):
                destination = licenses / 'build-tools' / package / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(dist.locate_file(file), destination)
                copied += 1
        if not copied:
            raise ValueError(f'Build-tool license not found: {package}')


def build(output):
    if sys.platform != 'win32' or platform.machine().upper() not in ('AMD64', 'X86_64') or sys.maxsize <= 2**32:
        raise ValueError('Build requires Windows x64 and 64-bit Python')
    if sys.version_info[:2] != (3, 12):
        raise ValueError('Build requires Python 3.12')
    packages = {}
    for line in (ROOT / 'requirements-build.txt').read_text(encoding='utf-8').splitlines():
        if line.strip() and not line.startswith('#'):
            name, required = line.split('==')
            if version(name) != required:
                raise ValueError(f'Build dependency mismatch: {name} requires {required}')
            packages[name] = required
    revision = git('rev-parse', 'HEAD')
    dirty = bool(git('status', '--porcelain', '--untracked-files=normal'))
    name = f'BunsenReplenishment-{__version__}-win-x64-{revision[:12]}' + ('-dirty' if dirty else '')
    output = Path(output).resolve()
    archive_path = output / (name + '.zip')
    checksum_path = output / (name + '.zip.sha256')
    if archive_path.exists() or checksum_path.exists():
        raise ValueError('Distribution output already exists; choose another output directory')
    output.mkdir(parents=True, exist_ok=True)
    workspace = ROOT / 'build'
    workspace.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='windows-', dir=workspace) as temp:
        work = Path(temp).resolve()
        if not work.is_relative_to(workspace.resolve()):
            raise ValueError('Unexpected build workspace')
        subprocess.run([sys.executable, '-m', 'PyInstaller', '--noconfirm', '--distpath', str(work / 'dist'),
                        '--workpath', str(work / 'work'), str(ROOT / 'distribution/bunsen.spec')], cwd=ROOT, check=True)
        bundle = work / 'dist/BunsenReplenishment'
        copy_resources(bundle)
        copy_licenses(bundle, packages)
        shutil.copyfile(ROOT / 'distribution/START-HERE.txt', bundle / 'START-HERE.txt')
        metadata = dict(version=__version__, revision=revision, dirty=dirty, signed=False,
                        built_at=datetime.now(timezone.utc).isoformat(), platform='win-x64',
                        python=platform.python_version(), build_dependencies=packages)
        (bundle / 'build-info.json').write_text(json.dumps(metadata, indent=2), encoding='utf-8')
        files = {path.relative_to(bundle).as_posix(): dict(size=path.stat().st_size, sha256=file_digest(path))
                 for path in sorted(bundle.rglob('*')) if path.is_file()}
        (bundle / 'package-manifest.json').write_text(json.dumps(dict(version=1, files=files), indent=2), encoding='utf-8')
        with archive_path.open('xb') as stream:
            with zipfile.ZipFile(stream, 'w', zipfile.ZIP_DEFLATED) as archive:
                for path in sorted(bundle.rglob('*')):
                    if path.is_file():
                        archive.write(path, 'BunsenReplenishment/' + path.relative_to(bundle).as_posix())
        checksum_path.write_text(file_digest(archive_path) + '  ' + archive_path.name + '\n', encoding='ascii')
    return archive_path


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, default=ROOT / 'dist')
    print(build(parser.parse_args().output))
