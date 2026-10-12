import copy
import hashlib
import io
import json
from contextlib import nullcontext
from pathlib import Path
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from pypdf import PdfReader, PdfWriter
from pypdf.generic import ContentStream, DecodedStreamObject, NameObject, TextStringObject
from ibkr_etl import web
from ibkr_etl.monthly_artifacts import MonthlyArtifactError, validate_factsheet_return_typography
from ibkr_etl.ms_factsheet_release import validate_ms_factsheet_release

FIXTURES=Path(__file__).parent/'ms-release-fixtures'
if not FIXTURES.exists():
    FIXTURES=Path(__file__).resolve().parents[1]/'.work/ms-release-validation-20261008-r2/web/context/tests/ms-release-fixtures'
PERIOD='2026-09-30'

def files(period=PERIOD):
    raw=(FIXTURES/'inputs'/(period+'.json')).read_text(encoding='utf-8')
    content=(FIXTURES/'pdf'/('AlphaWorld_MS_Factsheet_'+period+'.pdf')).read_bytes()
    return raw,json.loads(raw),content

def changed(content,old,new):
    reader=PdfReader(io.BytesIO(content));writer=PdfWriter();writer.clone_document_from_reader(reader)
    data=writer.pages[0].get_contents().get_data()
    if old in data:
        data=data.replace(old,new)
    else:
        contents=ContentStream(writer.pages[0].get_contents(),writer);count=0
        for args,operator in contents.operations:
            if operator==b'Tj' and str(args[0])==old.decode():
                args[0]=TextStringObject(new.decode());count+=1
        if count!=1:raise AssertionError('Expected exactly one visible mutation anchor')
        data=contents.get_data()
    stream=DecodedStreamObject();stream.set_data(data)
    writer.pages[0][NameObject('/Contents')]=writer._add_object(stream)
    out=io.BytesIO();writer.write(out);return out.getvalue()

class Cursor:
    rowcount=1
    def __init__(self,row=None):self.row=row
    def fetchone(self):return self.row

class Connection:
    def __init__(self,raw,*,corrupt=False):self.raw=raw;self.corrupt=corrupt;self.statements=[]
    def execute(self,sql,args):
        self.statements.append((sql,args))
        if 'SELECT snapshot_payload_json' in sql:
            return Cursor({'snapshot_payload_json':self.raw,'data_sha256':'a'*64 if self.corrupt else hashlib.sha256(self.raw.encode()).hexdigest(),
                           'subject_id':'alpha-world-ms','period_end':json.loads(self.raw)['period_end']})
        if 'SELECT release_decision_id' in sql:return Cursor({'release_decision_id':5})
        return Cursor()

class MsFactsheetReleaseTests(unittest.TestCase):
    def test_actual_ms_pdf_fails_the_wrong_generic_contract(self):
        _,_,content=files()
        with self.assertRaises(MonthlyArtifactError):validate_factsheet_return_typography(content)

    def test_all_21_actual_months_pass_subject_specific_full_validation(self):
        sources=sorted((FIXTURES/'inputs').glob('20*.json'));self.assertEqual(len(sources),21)
        for source in sources:
            with self.subTest(period=source.stem):
                _,snapshot,content=files(source.stem)
                validate_ms_factsheet_release(content,snapshot,period_end=source.stem)

    def test_wrong_subject_period_currency_fail_closed(self):
        _,snapshot,content=files()
        for field,value in (('subject_id','alpha-world-t'),('period_end','2026-08-31'),('reporting_currency','USD')):
            candidate=copy.deepcopy(snapshot);candidate[field]=value
            with self.subTest(field=field),self.assertRaises(MonthlyArtifactError):
                validate_ms_factsheet_release(content,candidate,period_end=PERIOD)

    def test_invalid_pdf_is_rejected(self):
        _,snapshot,_=files()
        with self.assertRaises(Exception):validate_ms_factsheet_release(b'not a PDF',snapshot,period_end=PERIOD)

    def test_return_value_tampering_is_rejected_even_with_unchanged_metadata(self):
        _,snapshot,content=files()
        bad=changed(content,b'+24.71%',b'+25.71%')
        with self.assertRaises(ValueError):validate_ms_factsheet_release(bad,snapshot,period_end=PERIOD)

    def test_kpi_font_size_tampering_is_rejected(self):
        _,snapshot,content=files()
        bad=changed(content,b'7 Tf',b'8 Tf')
        with self.assertRaises((MonthlyArtifactError,ValueError)):
            validate_ms_factsheet_release(bad,snapshot,period_end=PERIOD)

    def test_non_ms_contract_is_still_enforced(self):
        connection=Connection('{}')
        with patch.object(web,'validate_factsheet_return_typography',side_effect=MonthlyArtifactError('generic invalid')) as validate:
            with self.assertRaises(MonthlyArtifactError):
                web._validate_release_factsheet(connection,b'generic',subject_id='hong-kong-china',period_end=PERIOD,snapshot_id='snapshot')
        validate.assert_called_once_with(b'generic');self.assertEqual(connection.statements,[])

    def test_frozen_payload_hash_is_checked_before_pdf_validation(self):
        raw,_,content=files();connection=Connection(raw,corrupt=True)
        with self.assertRaises(MonthlyArtifactError):
            web._validate_release_factsheet(connection,content,subject_id='alpha-world-ms',period_end=PERIOD,snapshot_id='snapshot')
        self.assertTrue(all(sql.lstrip().startswith('SELECT') for sql,_ in connection.statements))

    def call_release(self,*,corrupt_snapshot=False,corrupt_pdf=False):
        raw,_,content=files();content=b'broken PDF' if corrupt_pdf else content
        connection=Connection(raw,corrupt=corrupt_snapshot)
        digest=hashlib.sha256(content).hexdigest();ppt=b'original approved deck fixture';ppt_sha=hashlib.sha256(ppt).hexdigest()
        preview={'snapshot_id':'751c2b5a-c9cd-54fb-808e-ff98edbd8d67','subject_id':'alpha-world-ms','period_end':PERIOD,
                 'package_sha256':'b'*64,'selection_version':'c'*64,'approved_types':[],
                 'release_eligible':True,'release_blocker':None,'publication_blockers':[],
                 'artifacts':[{'report_type':kind,'generation_run_id':run,'source_kind':'review',
                    'container_name':'internal-reports','blob_name':'review/alpha-world-ms/'+PERIOD+'/'+name,
                    'sha256':sha,'filename':name,'media_type':mime,'approved':False}
                    for kind,run,sha,name,mime in (('factsheet',1025,digest,'original_'+digest+'.pdf','application/pdf'),
                     ('presentation',1026,ppt_sha,'original_'+ppt_sha+'.pptx','application/vnd.openxmlformats-officedocument.presentationml.presentation'))]}
        payload=web.MonthlyReleaseRequest(snapshot_id=preview['snapshot_id'],package_sha256='b'*64,
            selection_version='c'*64,selected_report_types=['factsheet','presentation'])
        with (patch.object(web,'_portfolio_access_context',return_value={'preview_mode':False}),
              patch.object(web,'_approval_database',return_value=nullcontext(connection)),
              patch.object(web,'_monthly_release_candidate',return_value=preview),
              patch.object(web,'AZURE_STORAGE_ACCOUNT_URL','https://example.blob.core.windows.net'),
              patch.object(web,'download_blob_bytes',side_effect=lambda _url,_container,blob:content if blob.endswith('.pdf') else ppt),
              patch.object(web,'upload_bytes') as upload):
            if corrupt_snapshot or corrupt_pdf:
                with self.assertRaises(HTTPException) as blocked:
                    web.release_subject_monthly_reports('alpha-world-ms',PERIOD,payload,{'role':'owner','email':'owner@example.com','object_id':'owner'})
                self.assertEqual(blocked.exception.status_code,409);upload.assert_not_called()
                self.assertFalse(any(sql.lstrip().startswith(('INSERT','UPDATE','DELETE')) for sql,_ in connection.statements))
            else:
                result=web.release_subject_monthly_reports('alpha-world-ms',PERIOD,payload,{'role':'owner','email':'owner@example.com','object_id':'owner'})
                self.assertEqual(set(result['newly_approved_types']),{'factsheet','presentation'})
                self.assertEqual(upload.call_count,2)
                self.assertEqual({c.args[3] for c in upload.call_args_list},{content,ppt})
                self.assertTrue(all(not c.kwargs['overwrite'] for c in upload.call_args_list))

    def test_actual_approval_function_passes_valid_ms_pair_without_mocking_validator(self):self.call_release()
    def test_actual_approval_rejects_corrupt_frozen_snapshot_before_writes(self):self.call_release(corrupt_snapshot=True)
    def test_actual_approval_rejects_corrupt_pdf_before_writes(self):self.call_release(corrupt_pdf=True)

if __name__=='__main__':unittest.main()
