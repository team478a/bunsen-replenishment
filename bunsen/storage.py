"""Storage selection is explicit. Never discover or migrate business data."""
import os,sqlite3,uuid
from contextlib import closing
from datetime import datetime
from pathlib import Path
from .resources import resource_root
from .core import DataError

def default_home():
    return (resource_root()/'data').resolve()

def legacy_home():
    return (Path(os.environ.get('LOCALAPPDATA',str(Path.home())))/'BunsenReplenishment').resolve()

def new_home(parent):
    parent=Path(parent).resolve()
    if not parent.is_dir():raise DataError(['新規データを作る親フォルダーを選んでください'])
    target=parent/('bunsen-data-'+datetime.now().strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:8])
    target.mkdir(exist_ok=False)
    return target

def verify_existing(home):
    from .backup import validate_database
    home=Path(home).resolve();db=home/'history.db'
    if not db.is_file():raise DataError(['history.dbがあるデータフォルダーを選んでください。空のデータは新規作成操作を使ってください'])
    with closing(sqlite3.connect(db.as_uri()+'?mode=ro',uri=True)) as connection:validate_database(connection)
    return home
