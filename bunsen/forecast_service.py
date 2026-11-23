"""Local approved mapping and forecast-source history."""
import json
from pathlib import Path
from .core import DataError, stamp
from .forecast_csv import FORMAT, read_forecast, make_outputs


class ForecastService:
    def forecast_mapping(self):
        row=self.db.execute("SELECT id,payload FROM events WHERE kind='forecast_csv_review' ORDER BY id DESC LIMIT 1").fetchone()
        return dict(revision=row[0],mapping=json.loads(row[1])['mapping']) if row else dict(revision=0,mapping={})

    def save_forecast_csv(self,report,mapping,operator,acknowledged,master_revision,mapping_revision,folder):
        from .service import encode
        self.ensure_writable()
        if acknowledged is not True:raise DataError(['予測の採用と倉庫コード対応の担当者確認が必要です'])
        if report.get('path') and read_forecast(report['path'])['source_sha256']!=report['source_sha256']:
            raise DataError(['予測CSVが変更されています。再読取してください'])
        outputs,proof=make_outputs(report,mapping,operator)
        for row in report['rows']:
            if stamp(row['as_of'])>self.clock():raise DataError(['予測生成日時が未来です。時計と出力元を確認してください'])
        created=[]
        try:
            with self.db:
                self.db.execute('BEGIN IMMEDIATE');master=self.jan_master();previous=self.forecast_mapping()
                if type(mapping_revision) is not int or previous['revision']!=mapping_revision:raise DataError(['倉庫コード対応が更新されました。予測確認画面を開き直してください'])
                known={p['product'] for p in master['products']}
                missing=sorted({r['product'] for r in report['rows']}-known)
                for name,text in outputs.items():
                    path=Path(folder)/name
                    with path.open('x',encoding='utf-8-sig',newline='') as stream:created.append(path);stream.write(text)
                payload=dict(format=FORMAT,mapping=mapping,before_mapping=previous['mapping'],operator=operator.strip(),
                             acknowledged=True,master_revision=master_revision,previous_mapping_revision=mapping_revision,
                             report=report,proof=proof,outputs=outputs,unregistered_jans=missing)
                self.db.execute('INSERT INTO events(at,kind,payload) VALUES(?,?,?)',
                                (self.clock().isoformat(),'forecast_csv_review',encode(payload)))
        except Exception:
            for path in created:path.unlink(missing_ok=True)
            raise
        return created
