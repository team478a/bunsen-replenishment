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
    def __init__(self, folder):
        self.folder = folder
        info = folder.stat()
        self.folder_identity = (info.st_dev, info.st_ino)
        self.initial = self._inventory()
        self.contents = {}

    def _inventory(self):
        info = self.folder.stat()
        if (info.st_dev, info.st_ino) != self.folder_identity:
            raise OSError('CSVフォルダー自体が差し替えられました')
        return {path.name: _signature(path) for path in self.folder.iterdir()
                if path.suffix.lower() == '.csv'}

    def remember(self, path, content):
        self.contents[path.name] = content

    def finish(self):
        changes, failures = {}, {}

        def compare(current):
            for name in sorted(self.initial.keys() | current.keys()):
                if name not in self.initial:
                    changes[name] = '読取中にCSVが追加されました'
                elif name not in current:
                    changes[name] = '読取中にCSVが削除・移動されました'
                elif self.initial[name] != current[name]:
                    changes[name] = '読取前後でファイル属性・識別が変わりました'

        try:
            current = self._inventory()
            compare(current)
            # Re-read the actual bytes, including files whose size/mtime appear unchanged.
            for name, original in self.contents.items():
                if name not in current:
                    continue
                try:
                    if (self.folder / name).read_bytes() != original:
                        changes[name] = '読取前後でCSVの内容が変わりました'
                except OSError:
                    failures[name] = '読取後のCSV内容を再照合できません'
            compare(self._inventory())
        except OSError:
            failures[str(self.folder)] = '読取後のCSV一覧・属性を再照合できません'
        for name in self.initial.keys() - self.contents.keys():
            if name not in changes:
                failures[name] = '初回内容を取得できず、前後のCSV内容を照合できません'
        issues = [ValidationIssue(name, 'input_file', 'INPUT_CHANGED', reason)
                  for name, reason in sorted(changes.items())]
        issues.extend(ValidationIssue(name, 'input_file', 'INPUT_RECHECK_FAILED', reason)
                      for name, reason in sorted(failures.items()))
        result = dict(method=METHOD, status='unverifiable' if failures else ('changed' if changes else 'stable_observed'),
                      atomic_snapshot=False, changed_files=sorted(changes), unverified_files=sorted(failures))
        return issues, result
