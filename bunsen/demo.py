"""Fixed-date destination-only workflow, without Bunsen stock."""
from datetime import date,datetime
from pathlib import Path
import tempfile
import uuid
from .core import JST,DataError
from .resources import resource_root
from .service import Service
from .shipping_output import write_documents

def main(output=None):
    output=Path(output) if output else Path('demo-output')/('v2-'+uuid.uuid4().hex[:8])
    output.mkdir(parents=True,exist_ok=True)
    day=date(2026,11,9)
    clock=lambda:datetime(2026,11,9,9,tzinfo=JST)
    with tempfile.TemporaryDirectory() as temp:
        home=Path(temp);(home/'trial-info.json').write_text('{"format":"bunsen-trial-home","version":1,"trial_only":true}')
        service=Service(home/'history.db',clock)
        try:
            data=service.import_folder(resource_root()/'samples/replenishment')
            lines=service.calculate(data,day)
            assert [r['quantity_p'] for r in lines]==[120,48]
            draft=service.issue(data,lines,day,'架空担当者',False)
            write_documents(service.report_payload(draft['number']),output/'draft')
            service.edit_quantity(data,lines,('KOBE','P001'),144,'架空の増量理由','架空担当者')
            final=service.issue(data,lines,day,'架空担当者',True,True)
            write_documents(service.report_payload(final['number']),output/'formal')
            data=service.import_folder(resource_root()/'samples/replenishment-provisional')
            lines=service.calculate(data,day,allow_provisional=True)
            draft=service.issue(data,lines,day,'架空担当者',False)
            write_documents(service.report_payload(draft['number']),output/'provisional')
            try:service.issue(data,lines,day,'架空担当者',True,True,expected_revision=1)
            except DataError as exc:
                assert any('未確定' in e for e in exc.errors)
            else:raise AssertionError('Unconfirmed forecast formally issued')
            print('PASS: destination-only replenishment, edit history, formal Excel/PDF; provisional forecast blocked.')
        finally:service.close()

if __name__=='__main__':main()
