import calendar
import json
from pathlib import Path
import tempfile
import unittest

import finish_ms_reporting_recovery_qualification as subject


class RecoveryAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder=Path(self.temp.name)
        self.runtime=self.folder/'runtime-v1'
        self.runtime.mkdir()
        self.image='registry/candidate@sha256:'+'1'*64
        self.base='registry/base@sha256:'+'2'*64
        self.rows=[]
        outputs=self.runtime/'rollback-outputs';outputs.mkdir()
        for year,month in [(2025,m) for m in range(1,13)]+[(2026,m) for m in range(1,10)]:
            date=f'{year}-{month:02d}-{calendar.monthrange(year,month)[1]}'
            path=outputs/f'AlphaWorld_MS_Factsheet_{date}.pdf'
            path.write_bytes(date.encode())
            self.rows.append({'period_end':date,'factsheet_sha256':subject.build.sha(path)})
        protected={'protected_readback_verified':True}
        self.save('runtime-v1/candidate-qualified.json',{'proof':{'artifacts':self.rows}})
        self.save('runtime-v1/candidate-render-acceptance.json',{'status':'isolated_cloud_render_and_output_acceptance_passed',
            'image':self.image,'total_slides_visually_covered':420,'protected_receipt':protected,
            'candidate_qualified_sha256':subject.build.sha(self.runtime/'candidate-qualified.json'),
            'persistent_reporting_jobs_unchanged':True,'database_writes':0,'ibkr_source_data_changed':False,
            'reports_released':False,'onboarding_activated':False,'website_batch_registered':False})
        self.save('runtime-v1/rollback-qualified.json',{'status':'passed','mode':'rollback','image':self.base,
            'proof':{'count':21,'artifacts':self.rows,'database_writes':0,'read_only_transaction_enforced':True,
                'status':'passed','image':self.base,'owner_identity_verified':True,'source_data_changed':False,
                'reports_released':False,'onboarding_activated':False},
            'artifact_readback_verified':True,'persistent_jobs_unchanged':True,'protected_receipt':protected,
            'production_promotion':False})
        self.save('rollback-rehearsal-v1/acceptance.json',{'status':'isolated_image_switch_and_exact_archive_restore_passed',
            'candidate_image':self.image,'base_image':self.base,'live_reporting_jobs_unchanged':True,
            'scratch_resource_removed':True,'protected_definition_restored':True,
            'both_live_worker_rollback_definitions_recovered':True,'database_connections':0,
            'production_promotion':False,'protected_receipt':protected,'database_writes':0,
            'ibkr_source_changed':False,'reports_released':False,'full_live_image_switch_rollback_exercised':False})

    def save(self,name,record):
        path=self.folder/name;path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(json.dumps(record),encoding='utf-8')

    def mutate(self,name,key,value):
        record=json.loads((self.folder/name).read_bytes());record[key]=value;self.save(name,record)

    def test_complete_bounded_acceptance_is_pure(self):
        result=subject.validate(self.folder,self.image)
        self.assertEqual(len(result),3)

    def test_wrong_candidate_image_blocks(self):
        self.mutate('runtime-v1/candidate-render-acceptance.json','image',self.base)
        with self.assertRaisesRegex(ValueError,'exact-image'):
            subject.validate(self.folder,self.image)

    def test_missing_fallback_month_blocks(self):
        record=json.loads((self.runtime/'rollback-qualified.json').read_bytes())
        record['proof']['artifacts']=self.rows[:-1]
        self.save('runtime-v1/rollback-qualified.json',record)
        with self.assertRaisesRegex(ValueError,'period coverage'):
            subject.validate(self.folder,self.image)

    def test_changed_fallback_bytes_blocks(self):
        next((self.runtime/'rollback-outputs').glob('*.pdf')).write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'accepted bytes'):
            subject.validate(self.folder,self.image)

    def test_non_readonly_fallback_blocks(self):
        record=json.loads((self.runtime/'rollback-qualified.json').read_bytes())
        record['proof']['read_only_transaction_enforced']=False
        self.save('runtime-v1/rollback-qualified.json',record)
        with self.assertRaisesRegex(ValueError,'Read-only'):
            subject.validate(self.folder,self.image)

    def test_unremoved_scratch_job_blocks(self):
        self.mutate('rollback-rehearsal-v1/acceptance.json','scratch_resource_removed',False)
        with self.assertRaisesRegex(ValueError,'bounded image-switch'):
            subject.validate(self.folder,self.image)

    def test_unrecovered_definition_blocks(self):
        self.mutate('rollback-rehearsal-v1/acceptance.json','protected_definition_restored',False)
        with self.assertRaisesRegex(ValueError,'bounded image-switch'):
            subject.validate(self.folder,self.image)

    def test_unprotected_receipt_blocks(self):
        self.mutate('runtime-v1/rollback-qualified.json','protected_receipt',{'protected_readback_verified':False})
        with self.assertRaisesRegex(ValueError,'Protected qualification'):
            subject.validate(self.folder,self.image)

    def test_changed_candidate_manifest_blocks(self):
        self.save('runtime-v1/candidate-qualified.json',{'proof':{'artifacts':self.rows},'changed':True})
        with self.assertRaisesRegex(ValueError,'evidence identity'):
            subject.validate(self.folder,self.image)

    def test_duplicate_fallback_rows_block(self):
        record=json.loads((self.runtime/'rollback-qualified.json').read_bytes())
        record['proof']['artifacts'].append(self.rows[0])
        self.save('runtime-v1/rollback-qualified.json',record)
        with self.assertRaisesRegex(ValueError,'period coverage'):
            subject.validate(self.folder,self.image)

    def test_changed_fallback_source_scope_blocks(self):
        record=json.loads((self.runtime/'rollback-qualified.json').read_bytes())
        record['proof']['source_data_changed']=True
        self.save('runtime-v1/rollback-qualified.json',record)
        with self.assertRaisesRegex(ValueError,'non-release/source'):
            subject.validate(self.folder,self.image)

    def test_recovery_mutations_or_live_rollback_claim_block(self):
        for key,value in (('database_writes',1),('reports_released',True),
                          ('ibkr_source_changed',True),('full_live_image_switch_rollback_exercised',True)):
            with self.subTest(key=key):
                self.mutate('rollback-rehearsal-v1/acceptance.json',key,value)
                with self.assertRaisesRegex(ValueError,'bounded image-switch'):
                    subject.validate(self.folder,self.image)
                self.mutate('rollback-rehearsal-v1/acceptance.json',key,0 if key=='database_writes' else False)
