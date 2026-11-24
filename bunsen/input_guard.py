"""Detect observed CSV changes; this is not an atomic multi-file snapshot."""
from .core import ValidationIssue

METHOD = 'before_after_names_metadata_and_bytes'


def unchecked():
    return dict(method=METHOD, status='not_checked', atomic_snapshot=False,
                changed_files=[], unverified_files=[])


def _signature(path):
    info = path.stat()
    return (info.st_dev, info.st_ino, info.st_mode, info.st_size,
            info.st_mtime_ns, info.st_ctime_ns)


class InputGuard:
    def __init__(self, folder, ignored_names=()):
        self.folder = folder
        self.ignored_names = set(ignored_names)
        info = folder.stat()
        self.folder_identity = (info.st_dev, info.st_ino)
        self.initial = self._inventory()
        self.contents = {}

    def _inventory(self):
        info = self.folder.stat()
        if (info.st_dev, info.st_ino) != self.folder_identity:
            raise OSError('CSVフォルダー自体が差し替えられました')
        return {path.name: _signature(path) for path in self.folder.iterdir()
                if path.suffix.lower() == '.csv' and path.name not in self.ignored_names}

    def remember(self, path, content):
        self.contents[path.name] = content

    def finish(self):
        changes, failures = {}, {}

        def compare(current):
            for name in sorted(self.initial.keys() | current.keys()):
                if name not in self.initial:
                    changes[name] = '読み込み中にファイルが追加されました'
                elif name not in current:
                    changes[name] = '読み込み中にファイルが削除または移動されました'
                elif self.initial[name] != current[name]:
                    changes[name] = '読み込み中にファイルが変更または置き換えられました'

        try:
            current = self._inventory()
            compare(current)
            # Re-read the actual bytes, including files whose size/mtime appear unchanged.
            for name, original in self.contents.items():
                if name not in current:
                    continue
                try:
                    if (self.folder / name).read_bytes() != original:
                        changes[name] = '読み込み中にファイルの内容が変わりました'
                except OSError:
                    failures[name] = '読み込み後にファイルを開き直せず、内容を確認できませんでした'
            compare(self._inventory())
        except OSError:
            failures[str(self.folder)] = '読み込み後にフォルダーを確認できませんでした'
        for name in self.initial.keys() - self.contents.keys():
            if name not in changes:
                failures[name] = 'ファイルを最初に読み込めず、内容が変わっていないか確認できませんでした'
        issues = [ValidationIssue(name, 'input_file', 'INPUT_CHANGED', reason)
                  for name, reason in sorted(changes.items())]
        issues.extend(ValidationIssue(name, 'input_file', 'INPUT_RECHECK_FAILED', reason)
                      for name, reason in sorted(failures.items()))
        result = dict(method=METHOD, status='unverifiable' if failures else ('changed' if changes else 'stable_observed'),
                      atomic_snapshot=False, changed_files=sorted(changes), unverified_files=sorted(failures))
        return issues, result
